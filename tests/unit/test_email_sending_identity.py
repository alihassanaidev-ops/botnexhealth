"""Unit tests for per-clinic sending identities.

Two behaviours matter most and are easy to get subtly wrong:

* **Only a verified identity is used.** Sending from an unverified domain does
  not bounce, it lands in spam, so the failure is invisible until deliverability
  has already been damaged.
* **Losing verification after being verified is not the same as never having
  verified.** In the first case mail *was* flowing and has silently started
  failing authentication, which is the more urgent condition.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.models.email_sending_identity import (
    EmailIdentityStatus,
    EmailSendingIdentity,
)
from src.app.services.email.identity_service import (
    VERIFICATION_TIMEOUT,
    EmailIdentityService,
)
from src.app.services.email.ses_provisioning import (
    SesProvisioningError,
    safe_name,
    subdomain_for,
)


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slug,parent,expected",
    [
        ("brightsmile", "mail.scalenexus.ai", "brightsmile.mail.scalenexus.ai"),
        ("Bright Smile", "mail.scalenexus.ai", "bright-smile.mail.scalenexus.ai"),
        ("a_b", "mail.x.io", "a_b.mail.x.io"),
    ],
)
def test_subdomain_for(slug, parent, expected):
    assert subdomain_for(slug, parent) == expected


def test_subdomain_rejects_empty_slug():
    with pytest.raises(SesProvisioningError):
        subdomain_for("   ", "mail.scalenexus.ai")


def test_subdomain_label_is_capped_at_63_characters():
    """DNS labels cannot exceed 63 characters."""
    domain = subdomain_for("x" * 100, "mail.scalenexus.ai")
    assert len(domain.split(".")[0]) == 63


def test_safe_name_strips_unsupported_characters():
    """SES tenant and configuration-set names allow a restricted set."""
    assert safe_name("Bright Smile / Dental!") == "bright-smile-dental"


def test_safe_name_applies_prefix_and_cap():
    name = safe_name("x" * 200, prefix="scalenexus")
    assert name.startswith("scalenexus-")
    assert len(name) <= 64


# ---------------------------------------------------------------------------
# Resolution precedence
# ---------------------------------------------------------------------------


def _identity(status=EmailIdentityStatus.VERIFIED.value, location_id=None, **kw):
    identity = EmailSendingIdentity(
        institution_id="inst-1",
        location_id=location_id,
        provider="ses",
        domain="brightsmile.mail.scalenexus.ai",
        from_address="hello@brightsmile.mail.scalenexus.ai",
        from_name="Bright Smile",
        status=status,
    )
    for key, value in kw.items():
        setattr(identity, key, value)
    return identity


def _service(effective=None, institution=None):
    session = AsyncMock()
    session.get = AsyncMock(return_value=institution)
    svc = EmailIdentityService(session, provisioning=AsyncMock())
    svc.get_effective_identity = AsyncMock(return_value=effective)
    return svc


def _resolve(svc):
    return asyncio.run(svc.resolve("inst-1", "loc-1"))


def test_verified_identity_is_used():
    svc = _service(effective=_identity())
    resolved = _resolve(svc)
    assert resolved.from_address == "hello@brightsmile.mail.scalenexus.ai"
    assert resolved.provider == "ses"
    assert resolved.is_platform_fallback is False


@pytest.mark.parametrize(
    "status",
    [
        EmailIdentityStatus.PENDING_DNS.value,
        EmailIdentityStatus.VERIFYING.value,
        EmailIdentityStatus.FAILED.value,
        EmailIdentityStatus.REVOKED.value,
    ],
)
def test_unverified_identity_is_not_used(status):
    """Unauthenticated mail from the clinic's own domain is worse than
    recognisable mail from ours: it lands in spam and damages their domain."""
    institution = MagicMock()
    institution.email_from_address = None
    svc = _service(effective=_identity(status=status), institution=institution)

    with patch("src.app.services.email.identity_service.settings") as s:
        s.resend_from_email = "platform@scalenexus.ai"
        s.resend_reply_to = None
        s.patient_email_provider = "resend"
        resolved = _resolve(svc)

    assert resolved.from_address == "platform@scalenexus.ai"
    assert resolved.is_platform_fallback is True


def test_legacy_institution_address_is_used_when_no_identity():
    institution = MagicMock()
    institution.email_from_address = "legacy@clinic.com"
    institution.email_from_name = "Legacy Clinic"
    svc = _service(effective=None, institution=institution)

    with patch("src.app.services.email.identity_service.settings") as s:
        s.resend_from_email = "platform@scalenexus.ai"
        s.resend_reply_to = None
        s.patient_email_provider = "resend"
        resolved = _resolve(svc)

    assert resolved.from_address == "legacy@clinic.com"


def test_platform_address_is_the_last_resort():
    institution = MagicMock()
    institution.email_from_address = None
    svc = _service(effective=None, institution=institution)

    with patch("src.app.services.email.identity_service.settings") as s:
        s.resend_from_email = "platform@scalenexus.ai"
        s.resend_reply_to = None
        s.patient_email_provider = "resend"
        resolved = _resolve(svc)

    assert resolved.from_address == "platform@scalenexus.ai"
    assert resolved.is_platform_fallback is True


def test_resolution_is_unsendable_when_nothing_is_configured():
    institution = MagicMock()
    institution.email_from_address = None
    svc = _service(effective=None, institution=institution)

    with patch("src.app.services.email.identity_service.settings") as s:
        s.resend_from_email = None
        s.resend_reply_to = None
        s.patient_email_provider = "resend"
        resolved = _resolve(svc)

    assert resolved.is_sendable is False


# ---------------------------------------------------------------------------
# Verification state machine
# ---------------------------------------------------------------------------


def _refresh(identity, status, detail=None, now=None):
    session = AsyncMock()
    provisioning = AsyncMock()
    provisioning.check_status = AsyncMock(return_value=(status, detail))
    svc = EmailIdentityService(session, provisioning=provisioning)
    return asyncio.run(svc.refresh(identity, now=now))


def test_success_marks_verified_and_stamps_the_time():
    identity = _identity(status=EmailIdentityStatus.VERIFYING.value)
    identity.created_at = datetime.now(timezone.utc)

    refreshed = _refresh(identity, "SUCCESS")

    assert refreshed.status == EmailIdentityStatus.VERIFIED.value
    assert refreshed.verified_at is not None
    assert refreshed.failure_reason is None


def test_pending_stays_verifying_within_the_timeout():
    identity = _identity(status=EmailIdentityStatus.PENDING_DNS.value)
    identity.created_at = datetime.now(timezone.utc)

    refreshed = _refresh(identity, "PENDING")

    assert refreshed.status == EmailIdentityStatus.VERIFYING.value


def test_pending_past_the_timeout_fails():
    identity = _identity(status=EmailIdentityStatus.VERIFYING.value)
    identity.created_at = datetime.now(timezone.utc) - VERIFICATION_TIMEOUT - timedelta(hours=1)

    refreshed = _refresh(identity, "PENDING")

    assert refreshed.status == EmailIdentityStatus.FAILED.value
    assert "72 hours" in refreshed.failure_reason


def test_previously_verified_domain_going_pending_is_revoked_not_failed():
    """Mail *was* flowing and has now silently started failing authentication.
    That is a different, more urgent condition than never having verified."""
    identity = _identity(status=EmailIdentityStatus.VERIFIED.value)
    identity.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    identity.verified_at = datetime.now(timezone.utc) - timedelta(days=29)

    refreshed = _refresh(identity, "PENDING")

    assert refreshed.status == EmailIdentityStatus.REVOKED.value
    assert "DNS" in refreshed.failure_reason


def test_deleted_identity_is_revoked_when_it_had_verified():
    identity = _identity(status=EmailIdentityStatus.VERIFIED.value)
    identity.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    identity.verified_at = datetime.now(timezone.utc) - timedelta(days=29)

    refreshed = _refresh(identity, "NOT_FOUND", "gone")

    assert refreshed.status == EmailIdentityStatus.REVOKED.value


def test_failed_identity_that_never_verified_is_failed():
    identity = _identity(status=EmailIdentityStatus.VERIFYING.value)
    identity.created_at = datetime.now(timezone.utc)

    refreshed = _refresh(identity, "FAILED", "bad records")

    assert refreshed.status == EmailIdentityStatus.FAILED.value


def test_refresh_stamps_last_checked_at():
    identity = _identity(status=EmailIdentityStatus.VERIFYING.value)
    identity.created_at = datetime.now(timezone.utc)
    now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)

    refreshed = _refresh(identity, "PENDING", now=now)

    assert refreshed.last_checked_at == now


def test_is_sendable_only_for_verified():
    assert _identity(status=EmailIdentityStatus.VERIFIED.value).is_sendable is True
    assert _identity(status=EmailIdentityStatus.REVOKED.value).is_sendable is False
