from __future__ import annotations

import sys
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.app.api.routes import calls as calls_routes
from src.app.models.audit_log import AuditAction
from src.app.models.user import UserRole


class _ExecuteResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def tuples(self):
        return self

    def all(self):
        return self.value


class _FakeSession:
    def __init__(self, *results):
        self.results = list(results)

    async def execute(self, _stmt):
        if not self.results:
            raise AssertionError("No fake execute result left")
        return _ExecuteResult(self.results.pop(0))


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_exc):
        return None


def _route_target(fn):
    return getattr(fn, "__wrapped__", fn)


def _install_session(monkeypatch: pytest.MonkeyPatch, *results):
    session = _FakeSession(*results)
    monkeypatch.setattr(calls_routes, "get_db_session", lambda: _SessionContext(session))
    return session


def _user(role: str = UserRole.INSTITUTION_ADMIN.value, *, location_id: str | None = None):
    return SimpleNamespace(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        role=role,
        institution_id="11111111-1111-1111-1111-111111111111",
        location_id=location_id,
    )


def _location(agent_id: str = "agent_1"):
    return SimpleNamespace(
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        institution_id="11111111-1111-1111-1111-111111111111",
        retell_agent_id=agent_id,
    )


def _call():
    contact = SimpleNamespace(
        id="22222222-2222-2222-2222-222222222222",
        full_name="Sarah Loomer",
        first_name="Sarah",
        last_name="Loomer",
    )
    transcript_payload = [{"role": "user", "content": "My DOB is [REDACTED]"}]
    summary_payload = "Redacted summary from Retell"
    # Simulate the encrypted column being present (truthy) so
    # transcript_available reflects "yes". The decrypted property is what the
    # reveal endpoint serves; we set it directly here via a SimpleNamespace
    # so the test stays focused on route behavior, not encryption mechanics.
    return SimpleNamespace(
        id="33333333-3333-3333-3333-333333333333",
        institution_id="11111111-1111-1111-1111-111111111111",
        contact_id=contact.id,
        contact=contact,
        call_direction="inbound",
        call_status="needs_callback",
        call_tags="needs_callback,complaint",
        patient_status="contacted",
        summary=summary_payload,
        patient_sentiment="Neutral",
        next_action="Redacted next action",
        is_new_patient=False,
        is_complaint=True,
        is_insurance_billing=False,
        call_date=date(2026, 4, 30),
        call_time=time(9, 45),
        call_duration_seconds=180,
        callback_resolved=False,
        created_at=datetime(2026, 4, 30, 13, 45, tzinfo=timezone.utc),
        agent_used="agent_1",
        transcript_with_tool_calls=transcript_payload,
        transcript_with_tool_calls_encrypted="ciphertext-stub",
        recording_url="s3://bucket/scrubbed-recording.wav",
    )


def _field(field_key: str, *, is_phi: bool, display_order: int = 0):
    return SimpleNamespace(
        field_key=field_key,
        field_name=field_key.replace("_", " ").title(),
        field_type="text",
        is_phi=is_phi,
        display_order=display_order,
    )


def _value(
    value: str | None,
    *,
    value_encrypted: str | None = None,
    fail_if_called: bool = False,
):
    def get_value(is_phi=False):
        if fail_if_called:
            raise AssertionError("PHI value should not be decrypted by default detail")
        return value

    return SimpleNamespace(
        get_value=get_value,
        value_encrypted=value_encrypted,
        value_text=None if value_encrypted else value,
    )


@pytest.mark.asyncio
async def test_call_detail_hides_full_phi_until_audited_reveal(monkeypatch):
    monkeypatch.setattr(calls_routes, "log_audit_background", lambda **_kwargs: None)
    protected_field = _field("diagnosis_note", is_phi=True)
    plain_field = _field("referral_source", is_phi=False, display_order=1)
    _install_session(
        monkeypatch,
        _call(),
        [
            (
                protected_field,
                _value("Sensitive diagnosis", value_encrypted="ciphertext", fail_if_called=True),
            ),
            (plain_field, _value("Google")),
        ],
    )

    response = await _route_target(calls_routes.get_call)(
        request=object(),
        call_id="33333333-3333-3333-3333-333333333333",
        current_user=_user(),
    )

    # The detail endpoint never returns transcript or recording bodies —
    # only availability flags and metadata.
    assert response.transcript_available is True
    assert response.recording_available is True
    assert not hasattr(response, "transcript")
    assert not hasattr(response, "transcript_with_tool_calls")
    assert not hasattr(response, "recording_url") or response.recording_url is None

    protected = next(f for f in response.custom_fields if f.field_key == "diagnosis_note")
    assert protected.value is None
    assert protected.value_masked is True
    assert protected.reveal_available is True

    plain = next(f for f in response.custom_fields if f.field_key == "referral_source")
    assert plain.value == "Google"
    assert plain.value_masked is False
    assert plain.reveal_available is False


@pytest.mark.asyncio
async def test_reveal_transcript_returns_scrubbed_data_and_audits(monkeypatch):
    # PHI reveals use the two-row pre-then-post pattern: each reveal writes
    # an INITIATED row before decrypting and an outcome row after. We
    # filter to outcome rows here to assert the actions were audited.
    from src.app.models.audit_log import AuditOutcome
    from src.app.services import audit as audit_service

    audit_events: list[tuple[AuditAction, AuditOutcome]] = []

    async def _capture_audit(**kwargs):
        audit_events.append((kwargs["action"], kwargs["outcome"]))

    # phi_reveal_audit (the context manager used by reveal routes) calls
    # the module-level log_audit inside services.audit, so patch it there.
    monkeypatch.setattr(audit_service, "log_audit", _capture_audit)

    _install_session(monkeypatch, _call())
    revealed = await _route_target(calls_routes.reveal_transcript)(
        request=object(),
        call_id="33333333-3333-3333-3333-333333333333",
        current_user=_user(),
    )
    assert revealed.transcript_with_tool_calls == [
        {"role": "user", "content": "My DOB is [REDACTED]"},
    ]

    monkeypatch.setitem(
        sys.modules,
        "src.app.tasks.recordings",
        SimpleNamespace(generate_presigned_url=lambda url: f"signed:{url}"),
    )
    _install_session(monkeypatch, _call())
    recording = await _route_target(calls_routes.reveal_recording)(
        request=object(),
        call_id="33333333-3333-3333-3333-333333333333",
        current_user=_user(),
    )
    assert recording.recording_url == "signed:s3://bucket/scrubbed-recording.wav"

    _install_session(monkeypatch, _call(), [(_field("diagnosis_note", is_phi=True), _value("Sensitive diagnosis"))])
    custom = await _route_target(calls_routes.reveal_custom_phi_field)(
        request=object(),
        call_id="33333333-3333-3333-3333-333333333333",
        field_key="diagnosis_note",
        current_user=_user(),
    )
    assert custom.value == "Sensitive diagnosis"

    # Three reveals × (INITIATED + SUCCESS) = 6 rows total.
    assert len(audit_events) == 6
    outcome_rows = [e for e in audit_events if e[1] != AuditOutcome.INITIATED]
    assert outcome_rows == [
        (AuditAction.VIEW_FULL_TRANSCRIPT, AuditOutcome.SUCCESS),
        (AuditAction.VIEW_CALL_RECORDING, AuditOutcome.SUCCESS),
        (AuditAction.VIEW_CUSTOM_PHI_FIELD, AuditOutcome.SUCCESS),
    ]
    intent_rows = [e for e in audit_events if e[1] == AuditOutcome.INITIATED]
    assert {a for a, _ in intent_rows} == {
        AuditAction.VIEW_FULL_TRANSCRIPT,
        AuditAction.VIEW_CALL_RECORDING,
        AuditAction.VIEW_CUSTOM_PHI_FIELD,
    }


@pytest.mark.asyncio
async def test_super_admin_cannot_reveal_clinic_phi_without_break_glass(monkeypatch):
    """Denied PHI-reveal attempts must leave a FAILURE_UNAUTHORIZED audit
    row — leaving probing attempts un-audited would be a §164.312(b) gap."""
    from src.app.models.audit_log import AuditOutcome
    from src.app.api.routes import calls as calls_routes_mod

    captured: list[dict] = []

    def _capture_bg(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(calls_routes_mod, "log_audit_background", _capture_bg)

    with pytest.raises(HTTPException) as exc:
        await _route_target(calls_routes.reveal_transcript)(
            request=object(),
            call_id="33333333-3333-3333-3333-333333333333",
            current_user=_user(UserRole.SUPER_ADMIN.value),
        )

    assert exc.value.status_code == 403
    assert any(
        kw["outcome"] == AuditOutcome.FAILURE_UNAUTHORIZED
        and kw["action"] == AuditAction.VIEW_FULL_TRANSCRIPT
        for kw in captured
    ), "Denied PHI reveal must emit a FAILURE_UNAUTHORIZED audit row"


@pytest.mark.asyncio
async def test_call_not_found_emits_failure_audit_for_phi_reveal(monkeypatch):
    """When a clinic user requests a call_id outside their scope, the 404
    must be paired with a FAILURE_NOT_FOUND audit row so probing attempts
    leave a trail in compliance reports."""
    from src.app.models.audit_log import AuditOutcome
    from src.app.api.routes import calls as calls_routes_mod

    captured: list[dict] = []

    def _capture_bg(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(calls_routes_mod, "log_audit_background", _capture_bg)

    # Install a session that returns no call (simulates out-of-scope call_id).
    _install_session(monkeypatch, None)

    with pytest.raises(HTTPException) as exc:
        await _route_target(calls_routes.reveal_transcript)(
            request=object(),
            call_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
            current_user=_user(),
        )

    assert exc.value.status_code == 404
    assert any(
        kw["outcome"] == AuditOutcome.FAILURE_NOT_FOUND
        and kw["action"] == AuditAction.VIEW_FULL_TRANSCRIPT
        for kw in captured
    ), "404 on PHI-reveal must emit a FAILURE_NOT_FOUND audit row"


async def _invoke_reveal_endpoint(endpoint: str, current_user):
    if endpoint == "transcript":
        return await _route_target(calls_routes.reveal_transcript)(
            request=object(),
            call_id="33333333-3333-3333-3333-333333333333",
            current_user=current_user,
        )
    if endpoint == "recording":
        return await _route_target(calls_routes.reveal_recording)(
            request=object(),
            call_id="33333333-3333-3333-3333-333333333333",
            current_user=current_user,
        )
    if endpoint == "custom-field":
        return await _route_target(calls_routes.reveal_custom_phi_field)(
            request=object(),
            call_id="33333333-3333-3333-3333-333333333333",
            field_key="diagnosis_note",
            current_user=current_user,
        )
    raise AssertionError(f"Unknown endpoint: {endpoint}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,location_id",
    [
        (UserRole.INSTITUTION_ADMIN.value, None),
        (UserRole.LOCATION_ADMIN.value, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        (UserRole.STAFF.value, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
    ],
)
@pytest.mark.parametrize(
    "endpoint",
    ["transcript", "recording", "custom-field"],
)
async def test_phi_reveal_rbac_matrix_allows_in_scope_clinic_users(
    monkeypatch,
    role,
    location_id,
    endpoint,
):
    # phi_reveal_audit (the context manager) writes via services.audit.log_audit;
    # patch there rather than on calls_routes so the route module doesn't need
    # a forwarding re-export for tests.
    from src.app.services import audit as audit_service

    async def _noop_audit(**_kwargs):
        return None

    monkeypatch.setattr(audit_service, "log_audit", _noop_audit)
    monkeypatch.setitem(
        sys.modules,
        "src.app.tasks.recordings",
        SimpleNamespace(generate_presigned_url=lambda url: f"signed:{url}"),
    )

    # _get_scoped_call now filters by Call.location_id directly using the
    # user's location_id (no extra round-trip to InstitutionLocation). The
    # test mock just returns the call.
    session_results = [_call()]
    if endpoint == "custom-field":
        session_results.append([(_field("diagnosis_note", is_phi=True), _value("Sensitive diagnosis"))])
    _install_session(monkeypatch, *session_results)

    response = await _invoke_reveal_endpoint(endpoint, _user(role, location_id=location_id))

    assert response.call_id == "33333333-3333-3333-3333-333333333333"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value])
async def test_phi_reveal_rbac_matrix_denies_location_users_outside_location_scope(
    monkeypatch,
    role,
):
    # The route's WHERE Call.location_id = current_user.location_id eliminates
    # the row, so the call lookup returns None — same 404 as before.
    _install_session(monkeypatch, None)

    with pytest.raises(HTTPException) as exc:
        await _invoke_reveal_endpoint(
            "transcript",
            _user(role, location_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value])
async def test_phi_reveal_rbac_matrix_requires_location_assignment(monkeypatch, role):
    _install_session(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await _invoke_reveal_endpoint("transcript", _user(role, location_id=None))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    ["transcript", "recording", "custom-field"],
)
async def test_phi_reveal_rbac_matrix_blocks_super_admin_without_break_glass(endpoint):
    with pytest.raises(HTTPException) as exc:
        await _invoke_reveal_endpoint(endpoint, _user(UserRole.SUPER_ADMIN.value))

    assert exc.value.status_code == 403
