"""HIPAA: exception bodies on PHI paths must not leak identifiers into logs.

Vendor / PMS errors routinely echo the request payload in their message —
patient name, phone, email, DOB. The fix routes every PHI-path
``logger.error`` through ``sanitize_provider_error`` and the exception
type, never the raw exception. This test pins that contract for both
the sanitizer itself and a representative call site (Retell handlers).
"""

from __future__ import annotations

import logging

import pytest

from src.app.services.sms_privacy import safe_error_summary, sanitize_provider_error


# =============================================================================
# Direct sanitizer behaviour
# =============================================================================

@pytest.mark.parametrize(
    "raw,leak_fragment",
    [
        ("Patient phone +1 (415) 555-2671 not found", "555-2671"),
        ("Lookup failed for jane.doe@example.com", "jane.doe@example.com"),
        ("DOB 1972-03-05 invalid", "1972-03-05"),
        ("Cannot parse March 5, 1972 — bad month", "March 5"),
        ("Date 03/05/1972 conflict", "03/05/1972"),
        ("Date 12-31-99 conflict", "12-31-99"),
        ("HTTP 422 with phone 4155552671 in body", "4155552671"),
    ],
)
def test_sanitize_provider_error_redacts_identifiers(raw: str, leak_fragment: str) -> None:
    sanitized = sanitize_provider_error(raw)
    assert leak_fragment not in sanitized, (
        f"Expected '{leak_fragment}' to be redacted from {sanitized!r}"
    )


def test_sanitize_provider_error_preserves_diagnostic_text() -> None:
    """Non-PHI substrings must survive so operators can still debug."""
    raw = "HTTP 422: Validation failed — required field missing"
    sanitized = sanitize_provider_error(raw)
    assert "422" in sanitized
    assert "Validation failed" in sanitized


def test_sanitize_provider_error_handles_none() -> None:
    assert sanitize_provider_error(None) == "Unknown provider error"


def test_sanitize_provider_error_caps_length() -> None:
    raw = "X" * 5000
    assert len(sanitize_provider_error(raw, max_length=200)) <= 200


# =============================================================================
# safe_error_summary — strict body-less form for PHI-path logs
# =============================================================================

def test_safe_error_summary_drops_message_body() -> None:
    """Names, free-text, and the entire message body must NOT appear."""
    err = RuntimeError("Patient John Smith DOB 1972-03-05 not found")
    summary = safe_error_summary(err)
    assert summary == "type=RuntimeError"
    assert "John" not in summary
    assert "Smith" not in summary
    assert "Patient" not in summary


def test_safe_error_summary_keeps_http_status() -> None:
    class _Resp:
        status_code = 422

    class _HTTPErr(Exception):
        response = _Resp()

    summary = safe_error_summary(_HTTPErr("any body containing names"))
    assert "type=_HTTPErr" in summary
    assert "status=422" in summary
    assert "names" not in summary


def test_safe_error_summary_handles_none() -> None:
    assert safe_error_summary(None) == "type=NoneType"


# =============================================================================
# Call-site contract: Retell patient-lookup error path
# =============================================================================

@pytest.mark.asyncio
async def test_retell_patient_lookup_redacts_phi_in_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the PMS adapter raises with PHI in the message, no PHI reaches logs.

    This pins the H2 fix: the previous ``logger.error(f"... {e}")`` would
    have surfaced the entire exception body, which can contain patient
    name, DOB, phone, or email.
    """
    from src.app.retell import handlers

    class _FakeAdapter:
        async def search_patients(self, *args, **kwargs):
            raise RuntimeError(
                "PMS error 422: patient John Smith DOB 1972-03-05 phone "
                "+14155552671 email john.smith@example.com not found"
            )

    class _FakeContext:
        adapter = _FakeAdapter()
        institution = type("I", (), {"id": "inst-1"})()
        location = type("L", (), {"id": "loc-1", "timezone": "UTC"})()

    async def _fake_resolve_context():
        return _FakeContext()

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve_context)
    caplog.set_level(logging.ERROR, logger="src.app.retell.handlers")

    result = await handlers.lookup_patient(
        {"name": "John", "detail_level": "basic"}
    )

    # Caller-visible response is generic (no PHI).
    assert result["error"] == "patient_lookup_failed"
    assert "John Smith" not in str(result)
    assert "1972-03-05" not in str(result)

    # Log records carry only the type + sanitized message.
    log_text = " ".join(rec.getMessage() for rec in caplog.records)
    assert "RuntimeError" in log_text or "type=RuntimeError" in log_text
    for fragment in (
        "John Smith",
        "1972-03-05",
        "+14155552671",
        "4155552671",
        "john.smith@example.com",
    ):
        assert fragment not in log_text, (
            f"PHI fragment {fragment!r} leaked into log output: {log_text!r}"
        )
