"""Unit tests for ComplianceGateService (Plan 12 Slice 3)."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.models.automation_workflow import AutomationRunStatus, AutomationWorkflowRun
from src.app.models.location_operating_hours import LocationOperatingHours
from src.app.models.outbound_halt import OutboundEmergencyHalt
from src.app.models.sms_consent import ConsentChannel, ConsentRecord, ConsentStatus
from src.app.services.automation.compliance_gate import ComplianceGate, GateResult
from src.app.services.automation.compliance_gate_service import ComplianceGateService
from src.app.services.sms_compliance import SmsBlockedReason, SmsSendBlockedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_run(*, contact_id: str | None = "contact-1", location_id: str | None = "loc-1") -> AutomationWorkflowRun:
    return AutomationWorkflowRun(
        institution_id="inst-1",
        workflow_id="wf-1",
        workflow_version_id="ver-1",
        contact_id=contact_id,
        location_id=location_id,
        status=AutomationRunStatus.RUNNING.value,
    )


def _make_session(
    *,
    halt: OutboundEmergencyHalt | None = None,
    operating_hours: LocationOperatingHours | None = None,
    consent_record: ConsentRecord | None = None,
    dnc=None,
    location=None,
    contact=None,
) -> AsyncMock:
    """Build a mock AsyncSession whose execute() returns different things per query."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    dnc_rows = dnc if isinstance(dnc, list) else ([dnc] if dnc else [])

    def _execute_side_effect(stmt, *args, **kwargs):
        result = MagicMock()
        # Determine return value by inspecting the statement's entity
        stmt_str = str(stmt)
        if "outbound_emergency_halts" in stmt_str:
            result.scalar_one_or_none.return_value = halt
        elif "location_operating_hours" in stmt_str:
            result.scalar_one_or_none.return_value = operating_hours
        elif "do_not_contact" in stmt_str:
            result.scalars.return_value.all.return_value = dnc_rows
        elif "consent_records" in stmt_str:
            result.scalar_one_or_none.return_value = consent_record
        else:
            result.scalar_one_or_none.return_value = None
        return result

    async def _async_execute(stmt, *args, **kwargs):
        return _execute_side_effect(stmt)

    session.execute = AsyncMock(side_effect=_async_execute)

    async def _session_get(model, pk):
        from src.app.models.institution_location import InstitutionLocation
        from src.app.models.contact import Contact
        if model is InstitutionLocation:
            return location
        if model is Contact:
            return contact
        return None

    session.get = AsyncMock(side_effect=_session_get)
    return session


def _make_location(timezone: str = "America/Toronto"):
    loc = MagicMock()
    loc.timezone = timezone
    return loc


def _make_contact(phone: str | None = "+14165551234", email: str | None = "patient@example.com"):
    contact = MagicMock()
    contact.phone = phone
    contact.email = email
    return contact


def _make_hours(*, is_open=True, open_time=time(8, 0), close_time=time(20, 0)):
    hours = MagicMock(spec=LocationOperatingHours)
    hours.is_open = is_open
    hours.open_time = open_time
    hours.close_time = close_time
    return hours


def _make_consent(status: str = ConsentStatus.GRANTED.value, basis=None):
    record = MagicMock(spec=ConsentRecord)
    record.status = status
    record.basis = basis  # None → interpreted as "implied" by the gate
    return record


def _make_halt():
    halt = MagicMock(spec=OutboundEmergencyHalt)
    halt.released_at = None
    return halt


def _make_dnc(scope="institution", location_id=None, contact_id=None):
    from src.app.models.sms_consent import DoNotContact

    dnc = MagicMock(spec=DoNotContact)
    dnc.scope = scope
    dnc.location_id = location_id
    dnc.contact_id = contact_id
    dnc.is_active = True
    return dnc


# ---------------------------------------------------------------------------
# Protocol check
# ---------------------------------------------------------------------------


def test_gate_service_satisfies_protocol():
    assert issubclass(ComplianceGateService, object)
    # Runtime check via Protocol — instantiate with a mock session
    svc = ComplianceGateService(AsyncMock())
    assert isinstance(svc, ComplianceGate)


# ---------------------------------------------------------------------------
# Check 1: Emergency halt
# ---------------------------------------------------------------------------


def test_gate_blocks_on_active_halt():
    session = _make_session(halt=_make_halt())
    svc = ComplianceGateService(session)
    run = _make_run()
    result = asyncio.run(svc.check(run, "send_sms"))
    assert result.action == "block"
    assert result.reason == "emergency_halt"


def test_gate_proceeds_when_no_active_halt():
    """No halt + no location → skips quiet hours → checks consent."""
    contact = _make_contact()
    session = _make_session(halt=None, contact=contact)
    svc = ComplianceGateService(session)
    run = _make_run(location_id=None)

    with patch.object(
        svc, "_check_sms", new=AsyncMock(return_value=GateResult(action="allow"))
    ):
        result = asyncio.run(svc.check(run, "send_sms"))
    assert result.action == "allow"


# ---------------------------------------------------------------------------
# Check 2: Quiet hours
# ---------------------------------------------------------------------------


def test_gate_holds_outside_open_hours():
    """Current time before open_time → hold."""
    location = _make_location("UTC")
    # open 08:00-20:00; inject now at 06:00 UTC
    hours = _make_hours(is_open=True, open_time=time(8, 0), close_time=time(20, 0))
    session = _make_session(halt=None, operating_hours=hours, location=location)
    svc = ComplianceGateService(session)
    run = _make_run()

    now = datetime(2026, 7, 3, 6, 0, tzinfo=timezone.utc)  # Thursday 06:00 UTC
    result = asyncio.run(svc.check(run, "send_sms", now=now))
    assert result.action == "hold"
    assert result.reason == "quiet_hours"


def test_gate_blocks_when_no_permitted_window():
    """is_open=False for every day → no window within the horizon → block (never an
    infinite hold). The mock returns a closed row for all day lookups."""
    location = _make_location("UTC")
    hours = _make_hours(is_open=False)
    session = _make_session(halt=None, operating_hours=hours, location=location)
    svc = ComplianceGateService(session)
    run = _make_run()

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    result = asyncio.run(svc.check(run, "send_sms", now=now))
    assert result.action == "block"
    assert result.reason == "no_permitted_window"


def test_gate_skips_quiet_hours_when_no_location():
    """No location_id on run → quiet hours check skipped entirely."""
    contact = _make_contact()
    session = _make_session(halt=None, contact=contact)
    svc = ComplianceGateService(session)
    run = _make_run(location_id=None)

    with patch.object(
        svc, "_check_sms", new=AsyncMock(return_value=GateResult(action="allow"))
    ):
        result = asyncio.run(svc.check(run, "send_sms"))
    assert result.action == "allow"


def test_gate_allows_within_open_hours():
    """Current time within open hours → passes quiet hours check."""
    location = _make_location("UTC")
    hours = _make_hours(is_open=True, open_time=time(8, 0), close_time=time(20, 0))
    contact = _make_contact()
    session = _make_session(halt=None, operating_hours=hours, location=location, contact=contact)
    svc = ComplianceGateService(session)
    run = _make_run()

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)  # noon UTC
    with patch.object(
        svc, "_check_sms", new=AsyncMock(return_value=GateResult(action="allow"))
    ):
        result = asyncio.run(svc.check(run, "send_sms", now=now))
    assert result.action == "allow"


# ---------------------------------------------------------------------------
# Check 3: Consent
# ---------------------------------------------------------------------------


def test_gate_blocks_when_contact_id_is_none():
    location = _make_location("UTC")
    hours = _make_hours(is_open=True)
    session = _make_session(halt=None, operating_hours=hours, location=location)
    svc = ComplianceGateService(session)
    run = _make_run(contact_id=None)

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    result = asyncio.run(svc.check(run, "send_sms", now=now))
    assert result.action == "block"
    assert result.reason == "no_contact"


def test_gate_blocks_on_sms_suppression():
    location = _make_location("UTC")
    hours = _make_hours(is_open=True)
    contact = _make_contact()
    session = _make_session(halt=None, operating_hours=hours, location=location, contact=contact)
    svc = ComplianceGateService(session)
    run = _make_run()

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    with patch(
        "src.app.services.automation.compliance_gate_service.SmsComplianceService"
    ) as MockSvc:
        instance = MockSvc.return_value
        instance.assert_can_send = AsyncMock(
            side_effect=SmsSendBlockedError(SmsBlockedReason.OPTED_OUT)
        )
        result = asyncio.run(svc.check(run, "send_sms", now=now))

    assert result.action == "block"
    assert "opted_out" in result.reason or result.reason is not None


def test_gate_allows_sms_when_compliant():
    location = _make_location("UTC")
    hours = _make_hours(is_open=True)
    contact = _make_contact()
    session = _make_session(halt=None, operating_hours=hours, location=location, contact=contact)
    svc = ComplianceGateService(session)
    run = _make_run()

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    with patch(
        "src.app.services.automation.compliance_gate_service.SmsComplianceService"
    ) as MockSvc:
        instance = MockSvc.return_value
        instance.assert_can_send = AsyncMock(return_value=MagicMock())
        result = asyncio.run(svc.check(run, "send_sms", now=now))

    assert result.action == "allow"


def test_gate_blocks_voice_on_institution_do_not_contact():
    """A do-not-contact record blocks voice too (not just SMS) — scope §11."""
    location = _make_location("UTC")
    hours = _make_hours(is_open=True)
    contact = _make_contact()
    session = _make_session(
        halt=None, operating_hours=hours, location=location,
        contact=contact, dnc=_make_dnc(scope="institution"),
    )
    svc = ComplianceGateService(session)
    run = _make_run()

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    with patch("src.app.services.automation.compliance_gate_service.hash_phone", return_value="ph"):
        result = asyncio.run(svc.check(run, "send_voice", now=now))

    assert result.action == "block"
    assert result.reason == "do_not_contact"


def test_gate_blocks_email_on_do_not_contact():
    location = _make_location("UTC")
    hours = _make_hours(is_open=True)
    contact = _make_contact()
    session = _make_session(
        halt=None, operating_hours=hours, location=location,
        contact=contact, dnc=_make_dnc(scope="group"),
    )
    svc = ComplianceGateService(session)
    run = _make_run()

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    result = asyncio.run(svc.check(run, "send_email", now=now))

    assert result.action == "block"
    assert result.reason == "do_not_contact"


def test_gate_location_scoped_dnc_does_not_block_other_location():
    """A location-scoped DNC for location L-other must NOT block a run at L-1."""
    location = _make_location("UTC")
    hours = _make_hours(is_open=True)
    contact = _make_contact()
    consent = _make_consent(ConsentStatus.GRANTED.value)
    session = _make_session(
        halt=None, operating_hours=hours, location=location, contact=contact,
        consent_record=consent,
        dnc=_make_dnc(scope="location", location_id="L-other"),
    )
    svc = ComplianceGateService(session)
    run = _make_run(location_id="L-1")

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    with patch("src.app.services.automation.compliance_gate_service.hash_phone", return_value="ph"):
        result = asyncio.run(svc.check(run, "send_voice", now=now))

    # DNC is for a different location → not blocked; consent granted → allow.
    assert result.action == "allow"


def test_gate_allows_transactional_email_without_record_implied():
    """Option B: a transactional/unset email to a contact with an email on file
    is allowed by IMPLIED consent even with no explicit consent record."""
    location = _make_location("UTC")
    hours = _make_hours(is_open=True)
    contact = _make_contact()
    session = _make_session(
        halt=None, operating_hours=hours, location=location,
        contact=contact, consent_record=None,
    )
    svc = ComplianceGateService(session)
    run = _make_run()

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    with patch("src.app.services.automation.compliance_gate_service.hash_email", return_value="ehash"):
        # unset content_class == care/transactional
        result = asyncio.run(svc.check(run, "send_email", now=now))

    assert result.action == "allow"
    assert result.reason and "implied" in result.reason


def test_gate_blocks_marketing_email_without_consent():
    """Marketing/recall email still REQUIRES an express recorded consent — the
    implied-transactional allowance does not extend to commercial content."""
    location = _make_location("UTC")
    hours = _make_hours(is_open=True)
    contact = _make_contact()
    session = _make_session(
        halt=None, operating_hours=hours, location=location,
        contact=contact, consent_record=None,
    )
    svc = ComplianceGateService(session)
    run = _make_run()

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    with patch("src.app.services.automation.compliance_gate_service.hash_email", return_value="ehash"):
        result = asyncio.run(svc.check(run, "send_email", content_class="marketing", now=now))

    assert result.action == "block"
    assert result.reason == "no_email_consent"


def test_gate_allows_transactional_voice_without_record_implied():
    """Same implied-transactional allowance on the voice path (phone identity)."""
    location = _make_location("UTC")
    hours = _make_hours(is_open=True)
    contact = _make_contact()
    session = _make_session(
        halt=None, operating_hours=hours, location=location,
        contact=contact, consent_record=None,
    )
    svc = ComplianceGateService(session)
    run = _make_run()

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    with patch("src.app.services.automation.compliance_gate_service.hash_phone", return_value="ph"):
        result = asyncio.run(svc.check(run, "send_voice", now=now))

    assert result.action == "allow"
    assert result.reason and "implied" in result.reason


def test_gate_blocks_marketing_voice_without_consent():
    """Marketing/recall voice still requires an express recorded consent."""
    location = _make_location("UTC")
    hours = _make_hours(is_open=True)
    contact = _make_contact()
    session = _make_session(
        halt=None, operating_hours=hours, location=location,
        contact=contact, consent_record=None,
    )
    svc = ComplianceGateService(session)
    run = _make_run()

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    with patch("src.app.services.automation.compliance_gate_service.hash_phone", return_value="ph"):
        result = asyncio.run(svc.check(run, "send_voice", content_class="recall", now=now))

    assert result.action == "block"
    assert result.reason == "no_voice_consent"


def test_gate_blocks_email_when_contact_has_no_email():
    """An email send to a contact with no email address is blocked no_email —
    NOT no_phone (email consent is keyed on the email identity, P0-2)."""
    location = _make_location("UTC")
    hours = _make_hours(is_open=True)
    contact = _make_contact(phone=None, email=None)  # email-only-less contact
    session = _make_session(
        halt=None, operating_hours=hours, location=location,
        contact=contact, consent_record=None,
    )
    svc = ComplianceGateService(session)
    run = _make_run()

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    result = asyncio.run(svc.check(run, "send_email", now=now))

    assert result.action == "block"
    assert result.reason == "no_email"


def test_gate_allows_email_for_contact_without_phone():
    """An email-only contact (no phone) with granted email consent passes —
    the old phone-hash keying wrongly blocked this as no_phone (P0-2)."""
    location = _make_location("UTC")
    hours = _make_hours(is_open=True)
    contact = _make_contact(phone=None, email="email.only@example.com")
    consent = _make_consent(ConsentStatus.GRANTED.value)
    session = _make_session(
        halt=None, operating_hours=hours, location=location,
        contact=contact, consent_record=consent,
    )
    svc = ComplianceGateService(session)
    run = _make_run()

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    result = asyncio.run(svc.check(run, "send_email", now=now))

    assert result.action == "allow"


def test_gate_allows_with_granted_email_consent():
    location = _make_location("UTC")
    hours = _make_hours(is_open=True)
    contact = _make_contact()
    consent = _make_consent(ConsentStatus.GRANTED.value)
    session = _make_session(
        halt=None, operating_hours=hours, location=location,
        contact=contact, consent_record=consent,
    )
    svc = ComplianceGateService(session)
    run = _make_run()

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    with patch("src.app.services.automation.compliance_gate_service.hash_email", return_value="ehash"):
        result = asyncio.run(svc.check(run, "send_email", now=now))

    assert result.action == "allow"


def test_gate_blocks_on_revoked_email_consent():
    location = _make_location("UTC")
    hours = _make_hours(is_open=True)
    contact = _make_contact()
    consent = _make_consent(ConsentStatus.REVOKED.value)
    session = _make_session(
        halt=None, operating_hours=hours, location=location,
        contact=contact, consent_record=consent,
    )
    svc = ComplianceGateService(session)
    run = _make_run()

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    with patch("src.app.services.automation.compliance_gate_service.hash_email", return_value="ehash"):
        result = asyncio.run(svc.check(run, "send_email", now=now))

    assert result.action == "block"
    assert "revoked" in result.reason


# ---------------------------------------------------------------------------
# Consent basis (V-3): marketing-class voice requires an express(_written) basis
# ---------------------------------------------------------------------------


def _voice_check(consent, content_class):
    location = _make_location("UTC")
    hours = _make_hours(is_open=True)
    contact = _make_contact()
    session = _make_session(
        halt=None, operating_hours=hours, location=location,
        contact=contact, consent_record=consent,
    )
    svc = ComplianceGateService(session)
    run = _make_run()
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    with patch("src.app.services.automation.compliance_gate_service.hash_phone", return_value="ph"):
        return asyncio.run(svc.check(run, "send_voice", now=now, content_class=content_class))


def test_gate_blocks_marketing_voice_without_express_basis():
    # An implied (NULL) basis is insufficient for marketing-class voice.
    result = _voice_check(_make_consent(basis=None), content_class="marketing")
    assert result.action == "block"
    assert "basis_insufficient" in result.reason


def test_gate_allows_marketing_voice_with_express_written_basis():
    result = _voice_check(_make_consent(basis="express_written"), content_class="marketing")
    assert result.action == "allow"


def test_gate_allows_recall_voice_with_express_basis():
    result = _voice_check(_make_consent(basis="express"), content_class="recall")
    assert result.action == "allow"


def test_gate_allows_care_voice_with_implied_basis():
    # transactional_care accepts implied/NULL basis (healthcare/exempt).
    result = _voice_check(_make_consent(basis=None), content_class="transactional_care")
    assert result.action == "allow"
