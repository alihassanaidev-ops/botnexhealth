"""HIPAA: PHI must not survive into audit_metadata JSONB.

The audit_metadata column docstring (audit_log.py) explicitly forbids
PHI. Two paths used to leak it anyway:

1. ``audit_context`` stored ``str(e)[:200]`` of the raised exception
   under ``error_message``. Truncation is not de-identification.

2. ``audit_decorator._classify_soft_error`` stored a 200-char prefix of
   the handler's ``{"error": "..."}`` string under ``error_message``.
   Vendor errors echoed back through this path could carry phone/DOB.

Both now route through the privacy primitives in sms_privacy.
"""

from __future__ import annotations

import pytest

from src.app.models.audit_log import AuditAction, AuditActor
from src.app.services.audit import (
    AuditService,
    InMemoryAuditRepository,
    audit_context,
    set_audit_service,
)


@pytest.mark.asyncio
async def test_audit_context_does_not_persist_raw_exception_message() -> None:
    """When the wrapped block raises, audit_metadata gets a structural
    summary — not the exception message body."""
    repo = InMemoryAuditRepository()
    service = AuditService(repo)
    set_audit_service(service)

    class _PHIErr(RuntimeError):
        response = type("R", (), {"status_code": 422})()

    with pytest.raises(_PHIErr):
        async with audit_context(
            service,
            actor=AuditActor.ADMIN,
            action=AuditAction.READ_PATIENT,
            target_resource="patient:abc",
            metadata={"actor_role": "INSTITUTION_ADMIN"},
        ):
            raise _PHIErr(
                "PMS error 422 patient John Smith DOB 1972-03-05 phone "
                "+14155552671 email john.smith@example.com"
            )

    assert len(repo.entries) == 1
    metadata = repo.entries[0].metadata
    summary = metadata["error_summary"]

    assert metadata["error_type"] == "_PHIErr"
    assert summary == "type=_PHIErr status=422", (
        f"Expected only structural fields, got {summary!r}"
    )

    blob = " ".join(str(v) for v in metadata.values())
    for fragment in (
        "John Smith", "John", "Smith",
        "1972-03-05", "March", "1972",
        "+14155552671", "4155552671", "555-2671",
        "john.smith@example.com", "@example.com",
    ):
        assert fragment not in blob, (
            f"PHI {fragment!r} leaked into audit_metadata: {blob!r}"
        )


def test_classify_soft_error_redacts_phi_shapes() -> None:
    """audit_decorator's soft-failure branch must scrub phone/email/DOB."""
    from src.app.services.audit_decorator import _classify_soft_error

    outcome, msg = _classify_soft_error(
        {"error": "Patient phone +14155552671 email a@b.com DOB 1972-03-05 not found"}
    )
    assert outcome is not None
    assert msg is not None
    assert "+14155552671" not in msg
    assert "4155552671" not in msg
    assert "a@b.com" not in msg
    assert "1972-03-05" not in msg
    # Diagnostic fragments must survive.
    assert "Patient" in msg
    assert "not found" in msg


def test_classify_soft_error_caps_length() -> None:
    """Even after sanitization, the message must not balloon audit JSONB."""
    from src.app.services.audit_decorator import _classify_soft_error

    long_error = "x" * 5_000
    _, msg = _classify_soft_error({"error": long_error})
    assert msg is not None
    assert len(msg) <= 200
