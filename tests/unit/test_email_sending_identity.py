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
from src.app.models.email_sender_address import EmailSenderAddress
from src.app.services.email.identity_service import (
    VERIFICATION_TIMEOUT,
    EmailIdentityService,
)
from src.app.services.email.ses_provisioning import (
    DnsRecord,
    ProvisionedIdentity,
    SesProvisioningError,
    SesProvisioningService,
    normalize_domain,
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


def test_custom_domain_is_normalized():
    assert normalize_domain("Mail.Example.COM.") == "mail.example.com"


@pytest.mark.parametrize("value", ["https://clinic.com", "hello@clinic.com", "localhost"])
def test_custom_domain_rejects_non_domain_input(value):
    with pytest.raises(SesProvisioningError):
        normalize_domain(value)


class _ProvisioningClient:
    region = "ca-central-1"

    def __init__(self):
        self.created = []
        self.published = []

    def create_identity(self, domain):
        self.created.append(domain)
        return [DnsRecord(f"dkim.{domain}", "CNAME", "token.amazonses.com")]

    def configure_mail_from(self, domain):
        return [DnsRecord(f"bounce.{domain}", "TXT", "spf", purpose="mail_from")]

    def ensure_configuration_set(self, _name):
        return None

    def ensure_tenant(self, _name):
        return None

    def associate_tenant_resources(self, _tenant, _domain, _configuration_set):
        return None

    def publish_records(self, zone, records):
        self.published.append((zone, records))


def test_clinic_owned_domain_returns_sending_and_receiving_dns_without_publishing():
    client = _ProvisioningClient()
    service = SesProvisioningService(client=client)
    with patch("src.app.services.email.ses_provisioning.settings") as configured:
        configured.ses_sending_domain = "mail.scalenexus.ai"
        configured.ses_sending_hosted_zone_id = "platform-zone"
        configured.ses_configuration_set_prefix = "scalenexus"
        result = asyncio.run(
            service.provision(
                slug="clinic",
                institution_id="11111111-2222",
                domain="clinic.com",
                inbound_domain="reply.clinic.com",
            )
        )

    assert client.created == ["clinic.com", "reply.clinic.com"]
    assert client.published == []
    assert result.dns_published is False
    assert result.inbound_domain == "reply.clinic.com"
    assert any(record.type == "MX" for record in result.inbound_dns_records)


def test_receiving_domain_must_be_dedicated_subdomain():
    service = SesProvisioningService(client=_ProvisioningClient())
    with patch("src.app.services.email.ses_provisioning.settings") as configured:
        configured.ses_sending_domain = "mail.scalenexus.ai"
        configured.ses_sending_hosted_zone_id = None
        configured.ses_configuration_set_prefix = "scalenexus"
        with pytest.raises(SesProvisioningError, match="dedicated receiving"):
            asyncio.run(
                service.provision(
                    slug="clinic",
                    institution_id="11111111-2222",
                    domain="clinic.com",
                    inbound_domain="clinic.com",
                )
            )


# ---------------------------------------------------------------------------
# Resolution precedence
# ---------------------------------------------------------------------------


def _identity(
    status=EmailIdentityStatus.VERIFIED.value,
    location_id=None,
    is_active=True,
    **kw,
):
    identity = EmailSendingIdentity(
        institution_id="inst-1",
        location_id=location_id,
        provider="ses",
        domain="brightsmile.mail.scalenexus.ai",
        from_address="hello@brightsmile.mail.scalenexus.ai",
        from_name="Bright Smile",
        status=status,
        is_active=is_active,
    )
    for key, value in kw.items():
        setattr(identity, key, value)
    return identity


def _address(location_id=None, **kw):
    address = EmailSenderAddress(
        id="address-1",
        institution_id="inst-1",
        email_identity_id="identity-1",
        location_id=location_id,
        local_part="hello",
        from_address="hello@brightsmile.mail.scalenexus.ai",
        from_name="Bright Smile",
        is_active=True,
        is_default=True,
    )
    for key, value in kw.items():
        setattr(address, key, value)
    return address


def _service(effective=None, institution=None):
    session = AsyncMock()
    session.get = AsyncMock(return_value=institution)
    svc = EmailIdentityService(session, provisioning=AsyncMock())
    svc.get_effective_sender = AsyncMock(
        return_value=((_address() if effective is not None else None), effective)
    )
    return svc


def _resolve(svc):
    return asyncio.run(svc.resolve("inst-1", "loc-1"))


def test_verified_identity_is_used():
    svc = _service(effective=_identity())
    with patch("src.app.services.email.identity_service.settings") as settings_mock:
        settings_mock.ses_clinic_sending_enabled = True
        resolved = _resolve(svc)
    assert resolved.from_address == "hello@brightsmile.mail.scalenexus.ai"
    assert resolved.provider == "ses"
    assert resolved.is_platform_fallback is False


def test_custom_receiving_domain_is_carried_to_workflow_reply():
    identity = _identity(inbound_domain="reply.clinic.com", inbound_enabled=True)
    svc = _service(effective=identity)
    with patch("src.app.services.email.identity_service.settings") as configured:
        configured.ses_clinic_sending_enabled = True
        resolved = _resolve(svc)
    assert resolved.inbound_domain == "reply.clinic.com"


def test_missing_explicit_sender_never_falls_back_to_another_brand():
    institution = MagicMock(email_from_address="platform@example.com")
    svc = _service(effective=None, institution=institution)
    with patch("src.app.services.email.identity_service.settings") as configured:
        configured.patient_email_provider = "resend"
        resolved = asyncio.run(
            svc.resolve("inst-1", "loc-1", sender_address_id="missing")
        )
    assert resolved.from_address is None
    assert resolved.is_platform_fallback is False


def test_verified_but_inactive_identity_falls_back_to_platform():
    institution = MagicMock()
    institution.email_from_address = None
    svc = _service(effective=_identity(is_active=False), institution=institution)

    with patch("src.app.services.email.identity_service.settings") as s:
        s.resend_from_email = "platform@scalenexus.ai"
        s.resend_reply_to = None
        s.patient_email_provider = "resend"
        resolved = _resolve(svc)

    assert resolved.from_address == "platform@scalenexus.ai"
    assert resolved.is_platform_fallback is True


def test_global_ses_interlock_falls_back_even_for_active_verified_identity():
    institution = MagicMock()
    institution.email_from_address = None
    svc = _service(effective=_identity(is_active=True), institution=institution)

    with patch("src.app.services.email.identity_service.settings") as s:
        s.resend_from_email = "platform@scalenexus.ai"
        s.resend_reply_to = None
        s.patient_email_provider = "resend"
        s.ses_clinic_sending_enabled = False
        resolved = _resolve(svc)

    assert resolved.from_address == "platform@scalenexus.ai"
    assert resolved.provider == "resend"


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


def test_provision_rejects_location_from_another_institution():
    institution = MagicMock(id="inst-1", name="Clinic", slug="clinic")
    location = MagicMock(
        id="loc-2", institution_id="inst-2", name="Other", slug="other"
    )
    session = AsyncMock()
    session.get = AsyncMock(side_effect=[institution, location])
    provisioning = AsyncMock()
    service = EmailIdentityService(session, provisioning=provisioning)

    with pytest.raises(SesProvisioningError, match="does not belong"):
        asyncio.run(service.provision(institution_id="inst-1", location_id="loc-2"))

    provisioning.provision.assert_not_awaited()


def test_provisioned_identity_starts_inactive():
    institution = MagicMock(id="inst-1", name="Clinic", slug="clinic")
    session = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock(return_value=institution)
    empty = MagicMock()
    empty.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=empty)
    provisioning = AsyncMock()
    provisioning.provision = AsyncMock(
        return_value=ProvisionedIdentity(
            domain="clinic.mail.scalenexus.ai",
            dns_records=[],
            tenant_name="clinic-inst-1",
            configuration_set="scalenexus-clinic-inst-1",
            dns_published=True,
        )
    )
    service = EmailIdentityService(session, provisioning=provisioning)
    service.create_address = AsyncMock(return_value=_address())

    identity = asyncio.run(service.provision(institution_id="inst-1"))

    assert identity.is_active is False
    assert identity.is_sendable is False


def test_reprovision_preserves_live_state_inbound_domain_and_provider_resources():
    institution = MagicMock(id="inst-1", name="Clinic", slug="clinic")
    identity = _identity(
        inbound_domain="reply.clinic.com",
        inbound_enabled=True,
        provider_tenant_name="existing-tenant",
        provider_configuration_set="existing-config",
    )
    identity.id = "identity-1"
    existing_address = _address()

    def result(value):
        row = MagicMock()
        row.scalar_one_or_none.return_value = value
        return row

    session = AsyncMock()
    session.get = AsyncMock(return_value=institution)
    session.execute = AsyncMock(
        side_effect=[
            result(identity),  # sending-domain owner
            result(None),  # no receiving-domain collision
            result(identity),  # existing receiving subdomain belongs to this row
            result(existing_address),
        ]
    )
    provisioning = AsyncMock()
    provisioning.provision.return_value = ProvisionedIdentity(
        domain="brightsmile.mail.scalenexus.ai",
        dns_records=[],
        tenant_name="existing-tenant",
        configuration_set="existing-config",
        dns_published=False,
        inbound_domain="reply.clinic.com",
        inbound_dns_records=[],
    )
    service = EmailIdentityService(session, provisioning=provisioning)

    returned = asyncio.run(
        service.provision(
            institution_id="inst-1",
            domain="brightsmile.mail.scalenexus.ai",
        )
    )

    assert returned is identity
    assert identity.status == EmailIdentityStatus.VERIFIED.value
    assert identity.is_active is True
    assert identity.inbound_enabled is True
    provisioning.provision.assert_awaited_once_with(
        slug="clinic",
        institution_id="inst-1",
        domain="brightsmile.mail.scalenexus.ai",
        inbound_domain="reply.clinic.com",
        tenant_name="existing-tenant",
        configuration_set="existing-config",
    )


def test_new_explicit_default_is_inserted_before_replacing_existing_default():
    identity = _identity()
    identity.id = "identity-1"
    session = AsyncMock()
    session.add = MagicMock()
    location = MagicMock(id="loc-1", institution_id="inst-1")
    session.get = AsyncMock(return_value=location)

    duplicate_result = MagicMock()
    duplicate_result.scalar_one_or_none.return_value = None
    default_result = MagicMock()
    default_result.scalar_one_or_none.return_value = "old-default"
    update_result = MagicMock()
    session.execute = AsyncMock(
        side_effect=[duplicate_result, default_result, update_result]
    )
    inserted_default_states: list[bool] = []

    async def capture_flush():
        if session.add.call_args:
            inserted_default_states.append(session.add.call_args.args[0].is_default)

    session.flush = AsyncMock(side_effect=capture_flush)
    service = EmailIdentityService(session, provisioning=AsyncMock())

    address = asyncio.run(
        service.create_address(
            identity,
            institution_id="inst-1",
            location_id="loc-1",
            local_part="appointments",
            make_default=True,
        )
    )

    assert inserted_default_states[0] is False
    assert address.is_default is True


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
    identity.created_at = (
        datetime.now(timezone.utc) - VERIFICATION_TIMEOUT - timedelta(hours=1)
    )

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
    assert refreshed.is_active is False
    assert "DNS" in refreshed.failure_reason


def test_deleted_identity_is_revoked_when_it_had_verified():
    identity = _identity(status=EmailIdentityStatus.VERIFIED.value)
    identity.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    identity.verified_at = datetime.now(timezone.utc) - timedelta(days=29)

    refreshed = _refresh(identity, "NOT_FOUND", "gone")

    assert refreshed.status == EmailIdentityStatus.REVOKED.value
    assert refreshed.is_active is False


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
    assert (
        _identity(
            status=EmailIdentityStatus.VERIFIED.value, is_active=False
        ).is_sendable
        is False
    )
    assert _identity(status=EmailIdentityStatus.REVOKED.value).is_sendable is False
