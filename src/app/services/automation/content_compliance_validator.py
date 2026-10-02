"""Plan 12 content-class + PHI compliance validator.

Implements the ``ContentComplianceValidator`` seam declared in
``validation_service.py``. Runs at publish time (and behind the builder's
``/validate`` endpoint) over the whole immutable ``WorkflowDefinition``.

Two rule families, both scoped to the message *bodies* of send steps
(SMS ``body_template``, email ``subject_template`` + ``body_template``; voice
has no text body — AI-voice consent/disclosure is handled separately):

  1. **Promotional language in an exempt content class.** A ``transactional_care``
     (Confirmation/Reminder) or ``recall`` campaign that contains promotional/
     marketing language *voids the TCPA healthcare exemption and CASL implied-
     consent basis* — it becomes a marketing message legally. This is a publish
     **error**: reclassify the campaign as ``sales``/``marketing`` (which then
     requires an express-consent path) or remove the promotional copy.

  2. **PHI / financial detail in an insecure-channel body.** Minimum-necessary:
     clinical diagnosis and financial-balance detail must not be baked into an
     SMS/email body. High-risk terms are a publish **error**; broader sensitive
     clinical terms are a **warning** (the detection is heuristic and tunable, so
     it warns rather than hard-blocks on ambiguous words).

Detection is deliberately conservative — over-blocking legitimate care reminders
is itself a failure mode (Plan 12 Risks). Term lists are module-level constants so
they can be tuned without touching the logic.
"""

from __future__ import annotations

import re

from src.app.services.automation.definition_schema import (
    SendEmailNode,
    SendSmsNode,
    SendVoiceNode,
    WorkflowDefinition,
)
from src.app.services.automation.validation_service import ValidationIssue

_MARKETING_CONTENT_CLASSES = {"sales", "marketing"}

# Content classes that carry a care/exempt consent basis. Promotional language
# in these voids the exemption.
_EXEMPT_CONTENT_CLASSES = {"transactional_care", "recall"}

# Clearly-promotional phrases. Kept conservative to avoid flagging ordinary
# reminder copy ("please confirm", "book your appointment" are NOT here).
_PROMOTIONAL_TERMS: tuple[str, ...] = (
    "discount",
    "% off",
    "percent off",
    "special offer",
    "limited time",
    "limited-time",
    "coupon",
    "promo code",
    "promotion",
    "gift card",
    "refer a friend",
    "new patient special",
    "offer expires",
    "buy one",
    "save $",
    "save up to",
    "free whitening",
    "whitening special",
    "sale ends",
)

# High-risk PHI/financial detail — a publish error if it appears in a body.
_HIGH_RISK_PHI_TERMS: tuple[str, ...] = (
    "diagnosis",
    "diagnosed",
    "biopsy",
    "lab result",
    "lab results",
    "test result",
    "balance due",
    "amount owed",
    "outstanding balance",
    "past due",
    "insurance claim",
    "claim denied",
)

# Broader clinical detail — a warning (heuristic, minimum-necessary guidance).
_SENSITIVE_CLINICAL_TERMS: tuple[str, ...] = (
    "root canal",
    "extraction",
    "cavity",
    "cavities",
    "gum disease",
    "periodontal",
    "abscess",
    "prescription",
    "x-ray",
    "crown",
    "filling",
)


def _matches(text: str, terms: tuple[str, ...]) -> list[str]:
    """Return the terms that appear in ``text`` (case-insensitive, word/phrase
    boundary aware so 'sale' does not match 'wholesale')."""
    lowered = text.lower()
    hits: list[str] = []
    for term in terms:
        # Phrases with non-word chars ($, %) can't use \b cleanly — substring is
        # fine for those; single words use word boundaries.
        if re.search(r"[^\w\s]", term):
            if term in lowered:
                hits.append(term)
        elif re.search(rf"\b{re.escape(term)}\b", lowered):
            hits.append(term)
    return hits


def _send_bodies(definition: WorkflowDefinition) -> list[tuple[str, str]]:
    """(node_id, text) for every scannable message body in the definition."""
    out: list[tuple[str, str]] = []
    for node in definition.nodes:
        if isinstance(node, SendSmsNode):
            out.append((node.id, node.body_template))
        elif isinstance(node, SendEmailNode):
            out.append((node.id, f"{node.subject_template}\n{node.body_template}"))
    return out


class ContentComplianceValidator:
    """Real Plan 12 content/PHI validator. Implements the ContentComplianceValidator
    protocol (async ``validate`` returning node-linked ``ValidationIssue``s)."""

    async def validate(
        self,
        definition: WorkflowDefinition,
        *,
        institution_id: str,
        location_id: str | None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        content_class = (
            definition.compliance.content_class if definition.compliance else None
        )
        is_exempt_class = content_class in _EXEMPT_CONTENT_CLASSES

        for node_id, text in _send_bodies(definition):
            if is_exempt_class:
                promo = _matches(text, _PROMOTIONAL_TERMS)
                if promo:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            message=(
                                f"Promotional language {promo} in a '{content_class}' "
                                "campaign voids its healthcare/implied-consent exemption. "
                                "Remove the promotional copy or reclassify the campaign as "
                                "sales/marketing (which requires an express-consent path)."
                            ),
                            node_id=node_id,
                            field_path=["body_template"],
                            code="promotional_in_exempt_class",
                        )
                    )

            high_risk = _matches(text, _HIGH_RISK_PHI_TERMS)
            if high_risk:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=(
                            f"Message body contains protected health / financial detail "
                            f"{high_risk}. Minimum-necessary: keep clinical diagnoses and "
                            "account balances out of SMS/email — reference them generically "
                            "and direct the patient to a secure channel."
                        ),
                        node_id=node_id,
                        field_path=["body_template"],
                        code="phi_in_body",
                    )
                )

            sensitive = _matches(text, _SENSITIVE_CLINICAL_TERMS)
            if sensitive:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        message=(
                            f"Message body references specific clinical detail {sensitive}. "
                            "Confirm this is minimum-necessary for the patient to act; prefer "
                            "generic wording (e.g. 'your upcoming appointment')."
                        ),
                        node_id=node_id,
                        field_path=["body_template"],
                        code="sensitive_clinical_in_body",
                    )
                )

        issues += self._voice_disclosure_issues(definition, content_class)
        return issues

    @staticmethod
    def _voice_disclosure_issues(
        definition: WorkflowDefinition, content_class: str | None
    ) -> list[ValidationIssue]:
        """AI-voice (artificial-voice) disclosure + consent obligations (Plan 12).

        Every outbound AI call must open with an automated-call identity
        disclosure and an opt-out — the engine injects this as the
        ``compliance_disclosure`` dynamic variable (see VoiceNodeExecutor), and
        the Retell agent prompt must speak it. For sales/marketing content, an
        AI/artificial-voice call additionally requires an *express* consent basis
        (opt-out alone is not consent)."""
        voice_nodes = [n for n in definition.nodes if isinstance(n, SendVoiceNode)]
        if not voice_nodes:
            return []
        issues = [
            ValidationIssue(
                severity="warning",
                message=(
                    "This workflow places AI voice calls. Each call must open with an "
                    "automated-call identity disclosure and a spoken opt-out (the engine "
                    "supplies these as the 'compliance_disclosure' variable — the Retell "
                    "agent prompt must reference it)."
                ),
                node_id=voice_nodes[0].id,
                code="ai_voice_disclosure_required",
            )
        ]
        if content_class in _MARKETING_CONTENT_CLASSES:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message=(
                        f"AI voice calls in a '{content_class}' campaign require an express "
                        "(written where mandated) consent basis on the voice channel — an "
                        "opt-out keyword is a withdrawal mechanism, not a consent basis."
                    ),
                    node_id=voice_nodes[0].id,
                    code="ai_voice_marketing_needs_express_consent",
                )
            )
        return issues
