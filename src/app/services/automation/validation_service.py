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

from src.app.services.automation.definition_schema import (
    ConditionNode,
    SendEmailNode,
    SendSmsNode,
    SendVoiceNode,
    WaitNode,
    WorkflowDefinition,
)
from src.app.services.automation.merge_field_catalog import MERGE_FIELD_CATALOG, MergeFieldSpec

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
        # 1. Structural validation. If this fails the graph can't be reasoned
        #    about further, so return the structural errors alone.
        try:
            definition = WorkflowDefinition.model_validate(definition_dict)
        except ValidationError as exc:
            return [
                ValidationIssue(
                    severity="error",
                    message=e.get("msg", "invalid"),
                    node_id=_node_id_for_loc(e.get("loc", ()), definition_dict),
                    field_path=[str(p) for p in e.get("loc", ())],
                    code="schema",
                )
                for e in exc.errors()
            ]

        issues: list[ValidationIssue] = []
        issues += self._unreachable_nodes(definition)
        issues += self._consent_and_content(definition)
        issues += self._merge_field_issues(definition)
        issues += await self.content_validator.validate(
            definition, institution_id=institution_id, location_id=location_id
        )
        issues += await self.readiness_checker.check(
            definition, institution_id=institution_id, location_id=location_id
        )
        return issues

    @staticmethod
    def is_publishable(issues: list[ValidationIssue]) -> bool:
        return not any(i.severity == "error" for i in issues)

    # ------------------------------------------------------------------

    @staticmethod
    def _unreachable_nodes(definition: WorkflowDefinition) -> list[ValidationIssue]:
        node_map = {n.id: n for n in definition.nodes}
        reachable: set[str] = set()
        stack = [definition.entry_node_id]
        while stack:
            nid = stack.pop()
            if nid in reachable or nid not in node_map:
                continue
            reachable.add(nid)
            node = node_map[nid]
            if isinstance(node, ConditionNode):
                stack.extend([node.true_next_node_id, node.false_next_node_id])
            elif isinstance(node, (WaitNode, *_SEND_NODE_TYPES)):
                stack.append(node.next_node_id)
        return [
            ValidationIssue(
                severity="warning",
                message=f"Node '{n.id}' is unreachable from the trigger and will never run.",
                node_id=n.id,
                code="unreachable",
            )
            for n in definition.nodes
            if n.id not in reachable
        ]

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
