from __future__ import annotations

import sys
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.app.api.routes import calls as calls_routes
from src.app.models.audit_log import AuditAction
from src.app.models.user import UserRole
from src.app.retell.webhooks import RetellCallWebhook, _preferred_recording_url


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
    return SimpleNamespace(
        id="33333333-3333-3333-3333-333333333333",
        institution_id="11111111-1111-1111-1111-111111111111",
        contact_id=contact.id,
        contact=contact,
        call_direction="inbound",
        call_status="needs_callback",
        call_tags="needs_callback,complaint",
        patient_status="contacted",
        summary="Redacted summary from Retell",
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
        transcript="Raw transcript with patient PHI",
        transcript_with_tool_calls=[
            {"role": "user", "content": "My DOB is 1990-01-01"},
        ],
        scrubbed_transcript_with_tool_calls=[
            {"role": "user", "content": "My DOB is [REDACTED]"},
        ],
        recording_url="s3://bucket/raw-or-scrubbed-recording.wav",
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

    assert response.transcript is None
    assert response.transcript_with_tool_calls is None
    assert response.recording_url is None
    assert response.scrubbed_transcript_with_tool_calls == [
        {"role": "user", "content": "My DOB is [REDACTED]"},
    ]
    assert response.full_transcript_available is True
    assert response.raw_transcript_available is True
    assert response.recording_available is True

    protected = next(f for f in response.custom_fields if f.field_key == "diagnosis_note")
    assert protected.value is None
    assert protected.value_masked is True
    assert protected.reveal_available is True

    plain = next(f for f in response.custom_fields if f.field_key == "referral_source")
    assert plain.value == "Google"
    assert plain.value_masked is False
    assert plain.reveal_available is False


@pytest.mark.asyncio
async def test_call_phi_reveal_endpoints_return_data_and_write_audit(monkeypatch):
    audit_events: list[AuditAction] = []

    async def _capture_audit(**kwargs):
        audit_events.append(kwargs["action"])

    monkeypatch.setattr(calls_routes, "log_audit", _capture_audit)

    _install_session(monkeypatch, _call())
    full = await _route_target(calls_routes.reveal_full_transcript)(
        request=object(),
        call_id="33333333-3333-3333-3333-333333333333",
        current_user=_user(),
    )
    assert full.transcript_with_tool_calls == [
        {"role": "user", "content": "My DOB is 1990-01-01"},
    ]

    _install_session(monkeypatch, _call())
    raw = await _route_target(calls_routes.reveal_raw_transcript)(
        request=object(),
        call_id="33333333-3333-3333-3333-333333333333",
        current_user=_user(),
    )
    assert raw.transcript == "Raw transcript with patient PHI"

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
    assert recording.recording_url == "signed:s3://bucket/raw-or-scrubbed-recording.wav"

    _install_session(monkeypatch, _call(), [(_field("diagnosis_note", is_phi=True), _value("Sensitive diagnosis"))])
    custom = await _route_target(calls_routes.reveal_custom_phi_field)(
        request=object(),
        call_id="33333333-3333-3333-3333-333333333333",
        field_key="diagnosis_note",
        current_user=_user(),
    )
    assert custom.value == "Sensitive diagnosis"

    assert audit_events == [
        AuditAction.VIEW_FULL_TRANSCRIPT,
        AuditAction.VIEW_RAW_TRANSCRIPT,
        AuditAction.VIEW_CALL_RECORDING,
        AuditAction.VIEW_CUSTOM_PHI_FIELD,
    ]


@pytest.mark.asyncio
async def test_super_admin_cannot_reveal_clinic_phi_without_break_glass():
    with pytest.raises(HTTPException) as exc:
        await _route_target(calls_routes.reveal_raw_transcript)(
            request=object(),
            call_id="33333333-3333-3333-3333-333333333333",
            current_user=_user(UserRole.SUPER_ADMIN.value),
        )

    assert exc.value.status_code == 403


async def _invoke_reveal_endpoint(endpoint: str, current_user):
    if endpoint == "full-transcript":
        return await _route_target(calls_routes.reveal_full_transcript)(
            request=object(),
            call_id="33333333-3333-3333-3333-333333333333",
            current_user=current_user,
        )
    if endpoint == "raw-transcript":
        return await _route_target(calls_routes.reveal_raw_transcript)(
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
    ["full-transcript", "raw-transcript", "recording", "custom-field"],
)
async def test_phi_reveal_rbac_matrix_allows_in_scope_clinic_users(
    monkeypatch,
    role,
    location_id,
    endpoint,
):
    async def _noop_audit(**_kwargs):
        return None

    monkeypatch.setattr(calls_routes, "log_audit", _noop_audit)
    monkeypatch.setitem(
        sys.modules,
        "src.app.tasks.recordings",
        SimpleNamespace(generate_presigned_url=lambda url: f"signed:{url}"),
    )

    session_results = []
    if role in (UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value):
        session_results.append(_location("agent_1"))
    session_results.append(_call())
    if endpoint == "custom-field":
        session_results.append([(_field("diagnosis_note", is_phi=True), _value("Sensitive diagnosis"))])
    _install_session(monkeypatch, *session_results)

    response = await _invoke_reveal_endpoint(endpoint, _user(role, location_id=location_id))

    assert response.call_id == "33333333-3333-3333-3333-333333333333"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value])
async def test_phi_reveal_rbac_matrix_denies_location_users_outside_agent_scope(
    monkeypatch,
    role,
):
    _install_session(monkeypatch, _location("agent_other"), None)

    with pytest.raises(HTTPException) as exc:
        await _invoke_reveal_endpoint(
            "raw-transcript",
            _user(role, location_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value])
async def test_phi_reveal_rbac_matrix_requires_location_assignment(monkeypatch, role):
    _install_session(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await _invoke_reveal_endpoint("raw-transcript", _user(role, location_id=None))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    ["full-transcript", "raw-transcript", "recording", "custom-field"],
)
async def test_phi_reveal_rbac_matrix_blocks_super_admin_without_break_glass(endpoint):
    with pytest.raises(HTTPException) as exc:
        await _invoke_reveal_endpoint(endpoint, _user(UserRole.SUPER_ADMIN.value))

    assert exc.value.status_code == 403


def test_retell_recording_upload_prefers_scrubbed_recording_url():
    call = RetellCallWebhook(
        call_id="retell_1",
        recording_url="https://retell.example/raw.wav",
        scrubbed_recording_url="https://retell.example/scrubbed.wav",
    )
    assert _preferred_recording_url(call) == "https://retell.example/scrubbed.wav"

    fallback = RetellCallWebhook(call_id="retell_2", recording_url="https://retell.example/raw.wav")
    assert _preferred_recording_url(fallback) == "https://retell.example/raw.wav"
