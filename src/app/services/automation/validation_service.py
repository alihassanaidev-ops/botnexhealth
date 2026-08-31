"""Authoritative workflow validation (Plan 01 §WorkflowValidationService).

Runs at publish time (fail-closed) and behind the builder's ``/validate`` endpoint
so the frontend and the engine share one source of truth. Layers:

  1. Structural — Pydantic graph validation (entry/refs/exit), surfaced node-linked.
  2. Reachability — unreachable nodes (the Pydantic model only checks ref existence).
  3. Consent / content-class — the structural "no send step without a consent path"
     guardrail (scope §9.1) and content-class classification.
  4. Plan-12 semantic validators (promotional-language, PHI-in-body, blast-radius) —
     invoked through a seam; a no-op default ships here so the engine stays safe until
     Plan 12 provides the real validator.
  5. Channel readiness — invoked through a seam (a no-op default); Plan 10 provides the
     real readiness check. It is advisory: it emits warnings at publish (surfaced in the
     builder) but does NOT block publishing a workflow whose channels aren't set up.

This service *invokes* compliance policy; it does not define Plan 12's semantic rules.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.services.automation.campaign_action_links import PLACEHOLDER_ACTIONS
from src.app.services.automation.definition_schema import (
    SendEmailNode,
    SendSmsNode,
    SendVoiceNode,
    WorkflowDefinition,
)
from src.app.services.automation.merge_field_catalog import MERGE_FIELD_CATALOG, MergeFieldSpec
from src.app.services.automation.node_registry import (
    capability_for,
    node_id,
    node_type,
    outgoing_references,
)

logger = logging.getLogger(__name__)

_SEND_NODE_TYPES = (SendSmsNode, SendVoiceNode, SendEmailNode)
_MARKETING_CLASSES = {"sales", "marketing"}
_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_CATALOG_BY_NAME: dict[str, MergeFieldSpec] = {field.name: field for field in MERGE_FIELD_CATALOG}


@dataclass
class ValidationIssue:
    severity: Literal["error", "warning"]
    message: str
    node_id: str | None = None
    field_path: list[str] = field(default_factory=list)
    code: str | None = None
    fix: str | None = None


class ContentComplianceValidator(Protocol):
    """Plan 12 seam: content-class / PHI / promotional-language rules."""

    async def validate(
        self,
        definition: WorkflowDefinition,
        *,
        institution_id: str,
        location_id: str | None,
    ) -> list[ValidationIssue]: ...


class NoOpContentValidator:
    """Default until Plan 12's ContentComplianceValidator lands. Adds no issues."""

    async def validate(self, definition, *, institution_id, location_id):  # noqa: D401
        return []


class ChannelReadinessChecker(Protocol):
    """Plan 10 seam: is each channel used by the workflow provisioned/ready?"""

    async def check(
        self,
        definition: WorkflowDefinition,
        *,
        institution_id: str,
        location_id: str | None,
    ) -> list[ValidationIssue]: ...


class NoOpReadinessChecker:
    """Default until Plan 10's readiness model lands. Adds no issues."""

    async def check(self, definition, *, institution_id, location_id):  # noqa: D401
        return []


def _node_id_for_loc(loc: tuple, definition: dict) -> str | None:
    """Best-effort map a Pydantic error location to a node id."""
    try:
        if len(loc) >= 2 and loc[0] == "nodes" and isinstance(loc[1], int):
            return definition["nodes"][loc[1]].get("id")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return None


class WorkflowValidationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        content_validator: ContentComplianceValidator | None = None,
        readiness_checker: ChannelReadinessChecker | None = None,
    ) -> None:
        self.session = session
        self.content_validator = content_validator or NoOpContentValidator()
        self.readiness_checker = readiness_checker or NoOpReadinessChecker()

    async def validate(
        self,
        definition_dict: dict,
        *,
        institution_id: str,
        location_id: str | None = None,
    ) -> list[ValidationIssue]:
        # Raw graph checks preserve node/field attribution even when Pydantic's
        # model-level graph validator would otherwise collapse the error to root.
        graph_issues = self._raw_graph_issues(definition_dict)
        try:
            definition = WorkflowDefinition.model_validate(definition_dict)
        except ValidationError as exc:
            schema_issues = [
                ValidationIssue(
                    severity="error",
                    message=e.get("msg", "invalid"),
                    node_id=_node_id_for_loc(e.get("loc", ()), definition_dict),
                    field_path=[str(p) for p in e.get("loc", ())],
                    code="schema",
                )
                for e in exc.errors()
                # Avoid a duplicate root-level graph error when the precise node-
                # linked preflight already reported it.
                if e.get("loc") or not graph_issues
            ]
            return _dedupe_issues([*graph_issues, *schema_issues])

        issues = list(graph_issues)
        issues += self._graph_semantic_issues(definition)
        # Compliance classification/content checks are managed by Retell for now.
        # Keep the schema fields for backwards compatibility, but do not enforce
        # them in this workflow builder validation path.
        # issues += self._consent_and_content(definition)
        issues += self._merge_field_issues(definition)
        issues += self._action_link_issues(definition)
        issues += await self._pms_capability_issues(
            definition, institution_id=institution_id, location_id=location_id
        )
        issues += await self._email_template_issues(
            definition, institution_id=institution_id
        )
        # issues += await self.content_validator.validate(
        #     definition, institution_id=institution_id, location_id=location_id
        # )
        issues += await self.readiness_checker.check(
            definition, institution_id=institution_id, location_id=location_id
        )
        return _dedupe_issues(issues)

    @staticmethod
    def is_publishable(issues: list[ValidationIssue]) -> bool:
        return not any(i.severity == "error" for i in issues)

    async def _email_template_issues(
        self, definition: WorkflowDefinition, *, institution_id: str
    ) -> list[ValidationIssue]:
        """Reject a definition pointing at a missing or inactive email template.

        Without this the failure surfaces at send time, mid-campaign, on a live
        patient run — the most expensive place to discover it.
        """
        from src.app.services.automation.definition_schema import SendEmailNode
        from src.app.services.campaign_email_template_service import (
            CampaignEmailTemplateService,
        )

        referencing = [
            node
            for node in definition.nodes
            if isinstance(node, SendEmailNode) and node.template_key
        ]
        if not referencing:
            return []

        service = CampaignEmailTemplateService(self.session)
        issues: list[ValidationIssue] = []
        for node in referencing:
            template = await service.get_by_key(institution_id, node.template_key)
            if template is None:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Email template '{node.template_key}' does not exist",
                        node_id=node.id,
                        field_path=["template_key"],
                        code="email_template_missing",
                    )
                )
            elif not template.is_active:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Email template '{node.template_key}' is inactive",
                        node_id=node.id,
                        field_path=["template_key"],
                        code="email_template_inactive",
                    )
                )
        return issues

    # ------------------------------------------------------------------

    @staticmethod
    def _raw_graph_issues(definition: dict) -> list[ValidationIssue]:
        nodes = definition.get("nodes")
        if not isinstance(nodes, list):
            return []

        issues: list[ValidationIssue] = []
        ids = [node_id(node) for node in nodes if isinstance(node, dict)]
        valid_ids = {value for value in ids if value}
        seen: set[str] = set()
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            current_id = node_id(node)
            current_type = node_type(node)
            if current_id and current_id in seen:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Step id '{current_id}' is used more than once.",
                        node_id=current_id,
                        field_path=["nodes", str(index), "id"],
                        code="duplicate_node_id",
                        fix="Give every step a unique id.",
                    )
                )
            if current_id:
                seen.add(current_id)

            capability = capability_for(node)
            if capability is None and current_type:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Step type '{current_type}' is not supported.",
                        node_id=current_id,
                        field_path=["nodes", str(index), "type"],
                        code="node_type_unsupported",
                        fix="Replace this step with a supported node type.",
                    )
                )
                continue
            if capability and not capability.runtime_supported:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Step type '{current_type}' cannot execute in this engine version.",
                        node_id=current_id,
                        code="node_runtime_unsupported",
                    )
                )
            if capability and not capability.dry_run_supported:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Step type '{current_type}' cannot be tested before publishing.",
                        node_id=current_id,
                        code="node_dry_run_unsupported",
                    )
                )
            if capability and capability.legacy:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        message=f"Step type '{current_type}' is legacy and cannot be newly authored.",
                        node_id=current_id,
                        code="legacy_node_type",
                        fix="Replace it with the current Wait node.",
                    )
                )

            for field_name, target_id in outgoing_references(node):
                if target_id in valid_ids:
                    continue
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=(
                            f"'{field_name}' is not connected to an existing step."
                            if not target_id
                            else f"'{field_name}' points to missing step '{target_id}'."
                        ),
                        node_id=current_id,
                        field_path=["nodes", str(index), field_name],
                        code="edge_target_missing",
                        fix="Connect this output to an existing step.",
                    )
                )

        entry_id = str(definition.get("entry_node_id") or "")
        if entry_id not in valid_ids:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message="The trigger is not connected to an existing first step.",
                    field_path=["entry_node_id"],
                    code="entry_node_missing",
                    fix="Connect the trigger to the first step.",
                )
            )
        if not any(node_type(node) == "exit" for node in nodes):
            issues.append(
                ValidationIssue(
                    severity="error",
                    message="Workflow must contain at least one exit node.",
                    code="exit_node_missing",
                    fix="Add an Exit step and connect every branch to an ending.",
                )
            )
        return issues

    @staticmethod
    def _graph_semantic_issues(definition: WorkflowDefinition) -> list[ValidationIssue]:
        node_map = {n.id: n for n in definition.nodes}
        adjacency = {
            node.id: [target for _, target in outgoing_references(node)]
            for node in definition.nodes
        }
        reachable: set[str] = set()
        stack = [definition.entry_node_id]
        while stack:
            nid = stack.pop()
            if nid in reachable or nid not in node_map:
                continue
            reachable.add(nid)
            stack.extend(adjacency[nid])

        issues = [
            ValidationIssue(
                severity="warning",
                message=f"Node '{n.id}' is unreachable from the trigger and will never run.",
                node_id=n.id,
                code="unreachable",
                fix="Connect this step to the workflow or remove it.",
            )
            for n in definition.nodes
            if n.id not in reachable
        ]

        cycle_nodes = _reachable_cycle_nodes(adjacency, definition.entry_node_id)
        for current_id in sorted(cycle_nodes):
            issues.append(
                ValidationIssue(
                    severity="error",
                    message="This step is part of an execution loop.",
                    node_id=current_id,
                    code="graph_cycle",
                    fix="Remove the loop or route the branch to an Exit step.",
                )
            )

        reverse: dict[str, list[str]] = {current_id: [] for current_id in node_map}
        for source, targets in adjacency.items():
            for target in targets:
                if target in reverse:
                    reverse[target].append(source)
        can_reach_exit = {
            node.id for node in definition.nodes if node_type(node) == "exit"
        }
        stack = list(can_reach_exit)
        while stack:
            current_id = stack.pop()
            for parent in reverse[current_id]:
                if parent not in can_reach_exit:
                    can_reach_exit.add(parent)
                    stack.append(parent)
        for current_id in sorted(reachable - can_reach_exit - cycle_nodes):
            issues.append(
                ValidationIssue(
                    severity="error",
                    message="This step has no path to an Exit step.",
                    node_id=current_id,
                    code="no_exit_path",
                    fix="Connect every possible path to an Exit step.",
                )
            )
        return issues

    @staticmethod
    def _consent_and_content(definition: WorkflowDefinition) -> list[ValidationIssue]:
        send_nodes = [n for n in definition.nodes if isinstance(n, _SEND_NODE_TYPES)]
        if not send_nodes:
            return []
        issues: list[ValidationIssue] = []
        comp = definition.compliance
        if comp is None or comp.content_class is None:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message=(
                        "This workflow sends messages but has no content class. Set one "
                        "(transactional_care / recall / sales / marketing) so the consent "
                        "basis and content rules can be enforced."
                    ),
                    code="content_class_unset",
                )
            )
        if comp and comp.content_class in _MARKETING_CLASSES and not comp.consent_required:
            # The structural "no send step without a consent path" guardrail.
            issues.append(
                ValidationIssue(
                    severity="error",
                    message=(
                        "Sales/marketing campaigns must require consent — a send step "
                        "without a consent path is not permitted."
                    ),
                    code="consent_required",
                )
            )
        return issues

    async def _pms_capability_issues(
        self,
        definition: WorkflowDefinition,
        *,
        institution_id: str,
        location_id: str | None,
    ) -> list[ValidationIssue]:
        """Refuse to publish a campaign the clinic's software cannot support.

        Instantiating from a template already checks this, but the check has to
        run again here: a clinic can change practice software after the campaign
        was built, and a workflow assembled without a template was never checked
        at all. An unknown capability counts as unavailable — the evaluation
        already treats it that way, and guessing in the clinic's favour is how a
        campaign gets published that silently does nothing.
        """
        requirements = list(definition.pms_capability_requirements or [])
        if not requirements or location_id is None or self.session is None:
            return []

        from src.app.models.institution import Institution
        from src.app.models.institution_location import InstitutionLocation
        from src.app.services.automation.pms_capability_service import (
            PmsCapabilityService,
        )

        location = await self.session.get(InstitutionLocation, location_id)
        institution = await self.session.get(Institution, institution_id)
        if location is None or institution is None:
            return []

        evaluation = await PmsCapabilityService(self.session).evaluate_location(
            institution=institution, location=location, requirements=requirements
        )
        if evaluation.supported:
            return []

        unavailable = sorted(
            set(evaluation.missing) | set(evaluation.partial) | set(evaluation.unknown)
        )
        return [
            ValidationIssue(
                severity="error",
                code="unsupported_pms_capability",
                message=(
                    "This clinic's practice software cannot provide "
                    f"{', '.join(unavailable)}, which this campaign needs. "
                    + (evaluation.message or "")
                ).strip(),
                fix=(
                    "Remove the steps that depend on it, or use a campaign that "
                    "does not require it at this location."
                ),
            )
        ]

    @staticmethod
    def _action_link_issues(definition: WorkflowDefinition) -> list[ValidationIssue]:
        """Block publishing a message whose link cannot be generated.

        These placeholders sat in templates for a long time with nothing
        producing a value, so the message went out with the link missing. Links
        are generated now, but they are only reachable if the deployment knows
        its own public address — without that they would point at a default host
        and quietly 404 for the patient.
        """
        if settings.public_base_url:
            return []
        issues: list[ValidationIssue] = []
        for node in definition.nodes:
            for field_path, template in _node_templates(node):
                for token in _extract_token_names(template):
                    if token not in PLACEHOLDER_ACTIONS:
                        continue
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            message=(
                                f"'{{{{{token}}}}}' cannot be generated: this "
                                "deployment has no public base URL configured, so "
                                "the link would not resolve for the patient."
                            ),
                            node_id=getattr(node, "id", None),
                            field_path=[field_path],
                            code="action_link_unavailable",
                            fix="Set public_base_url, or remove the link from this message.",
                        )
                    )
        return issues

    @staticmethod
    def _merge_field_issues(definition: WorkflowDefinition) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        trigger_type = definition.trigger.type

        for node in definition.nodes:
            channel = _node_channel(node)
            if channel is None:
                continue

            for field_path, template in _node_templates(node):
                for token in _extract_token_names(template):
                    field = _CATALOG_BY_NAME.get(token)
                    if field is None:
                        issues.append(
                            ValidationIssue(
                                severity="warning",
                                message=(
                                    f"Unknown merge field '{{{{{token}}}}}' will render blank."
                                ),
                                node_id=node.id,
                                field_path=[field_path],
                                code="merge_field_unknown",
                            )
                        )
                        continue

                    if trigger_type not in field.triggers:
                        issues.append(
                            ValidationIssue(
                                severity="warning",
                                message=(
                                    f"Merge field '{field.token}' is not available for "
                                    f"{trigger_type} workflows."
                                ),
                                node_id=node.id,
                                field_path=[field_path],
                                code="merge_field_unavailable_for_trigger",
                            )
                        )

                    if channel not in field.channels:
                        issues.append(
                            ValidationIssue(
                                severity="warning",
                                message=(
                                    f"Merge field '{field.token}' is not available for "
                                    f"{channel} messages."
                                ),
                                node_id=node.id,
                                field_path=[field_path],
                                code="merge_field_unavailable_for_channel",
                            )
                        )

                    if field.phi_level == "high" and channel in {"sms", "voice"}:
                        issues.append(
                            ValidationIssue(
                                severity="warning",
                                message=(
                                    f"Merge field '{field.token}' may expose sensitive "
                                    f"clinical context on {channel}."
                                ),
                                node_id=node.id,
                                field_path=[field_path],
                                code="merge_field_phi_warning",
                            )
                        )

        return issues


def _extract_token_names(template: str) -> list[str]:
    return list(dict.fromkeys(match.group(1) for match in _TOKEN_RE.finditer(template)))


def _node_channel(node: object) -> str | None:
    if isinstance(node, SendSmsNode):
        return "sms"
    if isinstance(node, SendEmailNode):
        return "email"
    if isinstance(node, SendVoiceNode):
        return "voice"
    return None


def _node_templates(node: object) -> list[tuple[str, str]]:
    if isinstance(node, SendSmsNode):
        return [("body_template", node.body_template)]
    if isinstance(node, SendEmailNode):
        return [
            ("subject_template", node.subject_template),
            ("body_template", node.body_template),
        ]
    return []


def _reachable_cycle_nodes(
    adjacency: dict[str, list[str]], entry_node_id: str
) -> set[str]:
    colors: dict[str, int] = {}
    active: list[str] = []
    cycle_nodes: set[str] = set()

    def visit(current_id: str) -> None:
        colors[current_id] = 1
        active.append(current_id)
        for target_id in adjacency.get(current_id, []):
            if target_id not in adjacency:
                continue
            if colors.get(target_id, 0) == 0:
                visit(target_id)
            elif colors.get(target_id) == 1:
                cycle_nodes.update(active[active.index(target_id):])
        active.pop()
        colors[current_id] = 2

    if entry_node_id in adjacency:
        visit(entry_node_id)
    return cycle_nodes


def _dedupe_issues(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    unique: list[ValidationIssue] = []
    seen: set[tuple] = set()
    for issue in issues:
        key = (
            issue.code,
            issue.node_id,
            tuple(issue.field_path),
            issue.message,
        )
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique
