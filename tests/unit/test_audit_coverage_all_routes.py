"""Every state-changing endpoint is audited, or says why not (Item 39).

Item 32 built this check for the campaign routes. Item 39's review found that
everywhere else, "is this audited?" was answered by whoever remembered — and the
answer turned out to be yes far more often than expected, which is the point: it
was true by diligence, not by construction, and diligence is not a control.

This is the repo-wide version. Each mutating endpoint either carries an audit
signal, or appears below with a stated reason. A new endpoint that does neither
fails the build, so the decision gets made once, in review, by someone who knows
what the endpoint does.


Reading the allowlist
---------------------

Three kinds of entry, and the difference matters:

``AUDITED_VIA_HELPER``
    Genuinely audited — the call sits in a helper the endpoint delegates to, so
    static analysis of the endpoint alone cannot see it. Not gaps. They are
    listed so nobody "fixes" them into double-logging.

``NOT_A_STATE_CHANGE``
    POST-shaped reads: previews, validators, dry runs. They change nothing, so
    there is nothing to record.

``ACCEPTED_GAP``
    Real gaps, deliberately left, each with a reason and — where relevant — the
    thing that would change the decision.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ROUTES = pathlib.Path(__file__).resolve().parents[2] / "src" / "app" / "api" / "routes"
MUTATING_VERBS = {"post", "put", "patch", "delete"}


#: Audited, but through a helper the endpoint calls. Verified by reading each
#: one during the Item 39 review.
AUDITED_VIA_HELPER: dict[str, str] = {
    "dead_letter:discard_dead_letter_event": (
        "_discard_dead_letter_event -> DEAD_LETTER_DISCARD"
    ),
    "institution_undeliverables:discard_institution_undeliverable": (
        "_discard_dead_letter_event -> DEAD_LETTER_DISCARD"
    ),
    "dead_letter:replay_dead_letter_event": (
        "_replay_dead_letter_event -> DEAD_LETTER_REPLAY"
    ),
    "institution_undeliverables:replay_institution_undeliverable": (
        "_replay_dead_letter_event -> DEAD_LETTER_REPLAY"
    ),
    "do_not_contact:remove_do_not_contact": "_audit_release → DO_NOT_CONTACT_RELEASE",
    "do_not_contact:release_do_not_contact_entry": (
        "_audit_release → DO_NOT_CONTACT_RELEASE"
    ),
    # auth.py records MFA and login outcomes through module-level helpers that
    # take the action as an argument, so the endpoint body never names one.
    "auth:login_oauth_form": "shared login path → LOGIN / LOGIN_FAILED",
    "auth:mfa_webauthn_register_verify": "_record_mfa → MFA_ENROLL / MFA_VERIFY",
    "auth:mfa_webauthn_authenticate_verify": "_record_mfa → MFA_VERIFY",
    "auth:mfa_totp_setup_verify": "_record_mfa → MFA_ENROLL",
    "auth:mfa_totp_verify": "_record_mfa → MFA_VERIFY",
    "auth:mfa_step_up_totp_verify": "step-up path → MFA_CHALLENGE",
    "auth:mfa_step_up_webauthn_verify": "step-up path → MFA_CHALLENGE",
    "auth:mfa_step_up_recovery_code_verify": "→ MFA_RECOVERY_CODE_USE",
}


#: POST-shaped reads. They render, validate or simulate; none writes anything.
NOT_A_STATE_CHANGE: dict[str, str] = {
    "auth:mfa_totp_setup_options": "returns enrolment options; enrolment is the verify step",
    "auth:mfa_step_up_webauthn_options": "returns a challenge, stores no decision",
    "auth:mfa_factors_totp_setup_options": "returns enrolment options",
    "automation_workflows:validate_definition": "validates, saves nothing",
    "automation_workflows:dry_run_definition": "simulates, sends nothing",
    "automation_workflows:preview_launch_checklist": "read of readiness state",
    "campaign_email_templates:live_preview_campaign_email_template": "renders a preview",
    "email_templates:live_preview_email_template": "renders a preview",
    "email_templates:validate_template_syntax": "parses, saves nothing",
    "sms_templates:live_preview_sms_template": "renders a preview",
    "sms_templates:validate_sms_template_syntax": "parses, saves nothing",
    "institution_setup:preview_bulk_link_range_availabilities": "previews a bulk edit",
    "admin_institutions:verify_institution_nexhealth_credentials": (
        "tests credentials, stores nothing"
    ),
    "sse:create_event_ticket": "short-lived stream ticket, not a domain change",
}


#: Real gaps, accepted for now. Each says why, and what would change the answer.
ACCEPTED_GAP: dict[str, str] = {
    # Inbound machine-to-machine. Each already writes a durable event row with
    # its own idempotency key, which is the provider-facing equivalent of an
    # audit trail: what arrived, when, and whether it was processed. Auditing
    # them as user actions would attribute them to nobody and bury real ones.
    "nexhealth_webhooks:nexhealth_appointment_webhook": "webhook; durable event row",
    "nexhealth_webhooks:nexhealth_patient_webhook": "webhook; durable event row",
    "nexhealth_webhooks:nexhealth_sync_status_webhook": "webhook; durable event row",
    "nexhealth_webhooks:nexhealth_shadow_appointment_webhook": "shadow webhook; no writes",
    "nexhealth_webhooks:nexhealth_shadow_patient_webhook": "shadow webhook; no writes",
    "nexhealth_webhooks:nexhealth_shadow_sync_status_webhook": "shadow webhook; no writes",
    "gotracker_webhooks:gotracker_webhook": "webhook; durable event row",
    "twilio_webhooks:sms_status": "delivery receipt; recorded on the message row",
    "email_compliance:resend_webhook": "provider bounce/complaint; suppression is recorded",
    # Configuration deciding what patients are told. The same class of action
    # Item 32 audited for campaigns, in surfaces it did not reach.
    #
    # The six endpoints deciding *when* — operating hours and breaks, which
    # quiet hours is derived from — were closed during the Item 39 review and
    # now carry LOCATION_UPDATE.
    #
    # The rest are listed rather than fixed, because each wants an action type
    # chosen deliberately, and inventing nine of them in one pass is how an
    # audit vocabulary stops meaning anything.
    "admin_institutions:create_admin_outbound_voice_profile": "GAP: binds the agent that calls patients",
    "admin_institutions:update_admin_outbound_voice_profile": "GAP: binds the agent that calls patients",
    "admin_institutions:delete_admin_outbound_voice_profile": "GAP: binds the agent that calls patients",
    "outbound_voice:create_profile": "GAP: binds the agent that calls patients",
    "outbound_voice:update_profile": "GAP: binds the agent that calls patients",
    "outbound_voice:delete_profile": "GAP: binds the agent that calls patients",
    "retell_sms:create_profile": "GAP: binds the agent that texts patients",
    "retell_sms:update_profile": "GAP: binds the agent that texts patients",
    "retell_sms:delete_profile": "GAP: binds the agent that texts patients",
    "campaign_email_templates:create_campaign_email_template": "GAP: what patients are told",
    "campaign_email_templates:update_campaign_email_template": "GAP: what patients are told",
    "campaign_email_templates:delete_campaign_email_template": "GAP: what patients are told",
    "email_templates:update_email_template": "GAP: what patients are told",
    "email_templates:reset_email_template": "GAP: what patients are told",
    "sms_templates:update_sms_template": "GAP: what patients are told",
    "sms_templates:reset_sms_template": "GAP: what patients are told",
    "email_sending_identities:recheck_email_sending_identity": "re-checks provider state only",
    "custom_fields:create_definition": "GAP: custom fields may hold PHI",
    "custom_fields:update_definition": "GAP: custom fields may hold PHI",
    "custom_fields:delete_definition": "GAP: custom fields may hold PHI",
    "workflow_statuses:create_status": "GAP: low consequence, config only",
    "workflow_statuses:update_status": "GAP: low consequence, config only",
    "workflow_statuses:delete_status": "GAP: low consequence, config only",
    "inbox:assign_thread": "GAP: staff assignment, no patient contact",
    "inbox:resolve_thread": "GAP: staff workflow state",
    "notification_preferences:update_notification_preferences": "GAP: staff's own preferences",
    "admin_institutions:send_test_call_notification": "GAP: places a test call to staff",
}


ALLOWLIST = {**AUDITED_VIA_HELPER, **NOT_A_STATE_CHANGE, **ACCEPTED_GAP}


def _mutating_endpoints() -> list[tuple[str, bool]]:
    """Every mutating route endpoint, and whether it shows an audit signal."""
    found: list[tuple[str, bool]] = []
    for path in sorted(ROUTES.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            is_mutating = decorated = False
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                func = dec.func
                if isinstance(func, ast.Attribute) and getattr(func.value, "id", "") == "router":
                    is_mutating = func.attr in MUTATING_VERBS
                elif isinstance(func, ast.Name) and func.id == "audit":
                    decorated = True
            if not is_mutating:
                continue
            src = ast.dump(node)
            audited = decorated or "log_audit" in src or "AuditAction" in src
            found.append((f"{path.stem}:{node.name}", audited))
    return found


def test_every_state_changing_endpoint_is_audited_or_explained() -> None:
    """The check Item 32 built for campaigns, applied everywhere else."""
    unexplained = [
        name
        for name, audited in _mutating_endpoints()
        if not audited and name not in ALLOWLIST
    ]
    assert not unexplained, (
        "These endpoints change state, record no audit, and are not classified:\n  "
        + "\n  ".join(sorted(unexplained))
        + "\n\nAdd an audit call, or add the endpoint to one of the three "
        "dictionaries in this file with a reason. A privileged action with no "
        "record is what Item 39 exists to prevent."
    )


def test_the_allowlist_does_not_outlive_its_entries() -> None:
    """An entry for an endpoint that is now audited, or gone, is stale.

    A stale allowlist is worse than none: it reads as though someone considered
    the current code when they considered something else.
    """
    live = {name for name, _ in _mutating_endpoints()}
    audited_now = {name for name, audited in _mutating_endpoints() if audited}

    departed = sorted(set(ALLOWLIST) - live)
    assert not departed, f"allowlisted endpoints that no longer exist: {departed}"

    fixed = sorted(set(ALLOWLIST) & audited_now)
    assert not fixed, (
        f"these are audited now and can leave the allowlist: {fixed}"
    )


@pytest.mark.parametrize("name,reason", sorted(ALLOWLIST.items()))
def test_every_allowlist_entry_gives_a_reason(name: str, reason: str) -> None:
    """A bare entry is a silent exemption, which is what we are avoiding."""
    assert reason.strip(), f"{name} is allowlisted with no reason"
    assert len(reason.strip()) > 10, f"{name}: '{reason}' does not explain anything"


def test_the_accepted_gaps_stay_visible() -> None:
    """Guards the count, so gaps get closed rather than quietly accumulating.

    Lower it when one is fixed. If this fails upward, someone has added a gap
    instead of an audit call, and that should be a conversation rather than a
    diff nobody reads.
    """
    gaps = [k for k, v in ACCEPTED_GAP.items() if v.startswith("GAP:")]
    assert len(gaps) <= 30, (
        f"{len(gaps)} accepted audit gaps — this list should shrink, not grow"
    )
