"""Provision and verify clinic-owned or platform-fallback SES domains.

Clinic-owned domains return DNS records for the clinic to publish. Generated
fallback subdomains under the configured ScaleNexus Route 53 zone are published
automatically. Optional receiving always uses a dedicated subdomain so we never
replace the MX for a clinic's ordinary staff mailbox.

Alongside the identity, each clinic gets an SES **tenant** and a **configuration
set** for attribution. Tenant creation alone does not isolate suppression or
reputation: explicit tenant suppression policies and event destinations are
rollout gates documented in ``docs/EMAIL_AUTOMATION.md``.

boto3 is synchronous; every call here runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from src.app.config import settings

logger = logging.getLogger(__name__)

#: SES tenant and configuration-set names allow a restricted character set.
#: Runs of unsupported characters collapse to a single separator, so
#: "Bright Smile / Dental" reads as "bright-smile-dental" rather than
#: "bright-smile---dental".
_NAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9_-]+")
_MAX_NAME_LEN = 64
_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class SesProvisioningError(RuntimeError):
    """Provisioning could not complete. Safe to retry unless stated otherwise."""


@dataclass(frozen=True)
class DnsRecord:
    name: str
    type: str
    value: str
    purpose: str = "sending"

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "type": self.type,
            "value": self.value,
            "purpose": self.purpose,
        }


@dataclass(frozen=True)
class ProvisionedIdentity:
    domain: str
    dns_records: list[DnsRecord]
    tenant_name: str
    configuration_set: str
    #: True when the records were published into our own Route 53 zone. False
    #: means a clinic-owned domain whose records the clinic must publish.
    dns_published: bool
    inbound_domain: str | None = None
    inbound_dns_records: list[DnsRecord] = field(default_factory=list)


def safe_name(value: str, prefix: str = "") -> str:
    """Build a provider-safe tenant / configuration-set name."""
    cleaned = _NAME_SAFE_RE.sub("-", (value or "").strip().lower()).strip("-")
    name = f"{prefix}-{cleaned}" if prefix else cleaned
    return name[:_MAX_NAME_LEN].strip("-") or "unnamed"


def subdomain_for(slug: str, parent_domain: str) -> str:
    """``brightsmile`` + ``mail.scalenexus.ai`` → ``brightsmile.mail.scalenexus.ai``."""
    label = _NAME_SAFE_RE.sub("-", (slug or "").strip().lower()).strip("-")
    if not label:
        raise SesProvisioningError("Cannot build a sending domain from an empty slug")
    # DNS labels cap at 63 characters.
    return f"{label[:63]}.{parent_domain}"


def normalize_domain(value: str) -> str:
    """Return a canonical ASCII domain or reject URL/email-shaped input."""
    raw = (value or "").strip().rstrip(".").lower()
    if not raw or any(part in raw for part in ("@", "://", "/", ":")):
        raise SesProvisioningError("Enter a domain name, not a URL or email address")
    try:
        domain = raw.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise SesProvisioningError("Domain name is not valid") from exc
    labels = domain.split(".")
    if len(labels) < 2 or len(domain) > 253 or any(
        not _DOMAIN_LABEL_RE.fullmatch(label) for label in labels
    ):
        raise SesProvisioningError("Domain name is not valid")
    return domain


class SesProvisioningClient:
    """Thin adapter over the AWS calls, so the service above it stays testable."""

    def __init__(self, region: str | None = None, ses=None, route53=None, sts=None) -> None:  # noqa: ANN001
        self._region = region or settings.ses_region
        self._ses = ses
        self._route53 = route53
        self._sts = sts

    @property
    def region(self) -> str:
        return self._region

    # -- lazily constructed clients ------------------------------------

    def ses(self):  # noqa: ANN201
        if self._ses is None:
            import boto3

            self._ses = boto3.client("sesv2", region_name=self._region)
        return self._ses

    def route53(self):  # noqa: ANN201
        if self._route53 is None:
            import boto3

            # Route 53 is a global service; its endpoint lives in us-east-1.
            self._route53 = boto3.client("route53")
        return self._route53

    def sts(self):  # noqa: ANN201
        if self._sts is None:
            import boto3

            self._sts = boto3.client("sts", region_name=self._region)
        return self._sts

    # -- SES -----------------------------------------------------------

    def create_identity(self, domain: str) -> list[DnsRecord]:
        """Create the domain identity and return the DKIM records to publish."""
        from botocore.exceptions import ClientError

        try:
            response = self.ses().create_email_identity(EmailIdentity=domain)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "AlreadyExistsException":
                # Idempotent: re-provisioning an existing identity should return
                # its current records rather than fail.
                response = self.ses().get_email_identity(EmailIdentity=domain)
            else:
                raise SesProvisioningError(
                    f"SES could not create identity for {domain} ({code})"
                ) from exc

        tokens = (response.get("DkimAttributes") or {}).get("Tokens") or []
        if not tokens:
            raise SesProvisioningError(
                f"SES returned no DKIM tokens for {domain}; cannot authenticate the domain"
            )
        return [
            DnsRecord(
                name=f"{token}._domainkey.{domain}",
                type="CNAME",
                value=f"{token}.dkim.amazonses.com",
                purpose="sending",
            )
            for token in tokens
        ]

    def configure_mail_from(self, domain: str) -> list[DnsRecord]:
        """Use a clinic-owned Return-Path so SPF can align with its From domain."""
        from botocore.exceptions import ClientError

        mail_from = f"bounce.{domain}"
        try:
            self.ses().put_email_identity_mail_from_attributes(
                EmailIdentity=domain,
                MailFromDomain=mail_from,
                BehaviorOnMxFailure="REJECT_MESSAGE",
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            raise SesProvisioningError(
                f"SES could not configure MAIL FROM for {domain} ({code})"
            ) from exc
        return [
            DnsRecord(
                name=mail_from,
                type="MX",
                value=f"10 feedback-smtp.{self._region}.amazonses.com",
                purpose="mail_from",
            ),
            DnsRecord(
                name=mail_from,
                type="TXT",
                value="v=spf1 include:amazonses.com ~all",
                purpose="mail_from",
            ),
        ]

    def identity_status(self, domain: str) -> tuple[str, str | None]:
        """Return ``(dkim_status, failure_reason)`` for a domain identity."""
        from botocore.exceptions import ClientError

        try:
            response = self.ses().get_email_identity(EmailIdentity=domain)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "NotFoundException":
                return "NOT_FOUND", "The sending domain no longer exists in SES"
            raise SesProvisioningError(f"SES status check failed ({code})") from exc

        dkim = response.get("DkimAttributes") or {}
        status = dkim.get("Status", "PENDING")
        verified = response.get("VerifiedForSendingStatus", False)
        if status == "SUCCESS" and not verified:
            # DKIM signed but SES still withholding sending — surface it rather
            # than reporting a verified identity that cannot send.
            return "PENDING", "DKIM verified but SES has not enabled sending yet"
        return status, None

    def receiving_mx_status(self, domain: str) -> tuple[bool, str | None]:
        """Verify that the clinic has delegated its receiving subdomain to SES."""
        try:
            import dns.resolver

            answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        except Exception as exc:  # DNS absence/timeouts are actionable, not fatal
            return False, f"Receiving MX lookup failed ({type(exc).__name__})"
        expected = f"inbound-smtp.{self._region}.amazonaws.com"
        exchanges = {str(answer.exchange).lower().rstrip(".") for answer in answers}
        if expected not in exchanges:
            return False, f"Receiving MX must point to {expected}"
        return True, None

    def ensure_tenant(self, tenant_name: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.ses().create_tenant(TenantName=tenant_name)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("AlreadyExistsException", "ConflictException"):
                return
            # Tenants are an isolation nicety, not a prerequisite for sending.
            # Losing them should not block a clinic from going live.
            logger.warning("SES tenant %s could not be created (%s)", tenant_name, code)

    def ensure_configuration_set(self, name: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.ses().create_configuration_set(ConfigurationSetName=name)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("AlreadyExistsException", "ConflictException"):
                return
            logger.warning(
                "SES configuration set %s could not be created (%s)", name, code
            )

    def associate_tenant_resource(self, tenant_name: str, resource_arn: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.ses().create_tenant_resource_association(
                TenantName=tenant_name, ResourceArn=resource_arn
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("AlreadyExistsException", "ConflictException"):
                return
            logger.warning(
                "SES tenant association failed tenant=%s resource=%s (%s)",
                tenant_name,
                resource_arn,
                code,
            )

    def associate_tenant_resources(
        self, tenant_name: str, domain: str, configuration_set: str
    ) -> None:
        """Attach both reputation-bearing resources to the institution tenant."""
        try:
            account_id = self.sts().get_caller_identity()["Account"]
        except Exception as exc:  # noqa: BLE001 — attribution must be observable
            logger.warning("Could not resolve AWS account for SES tenant association: %s", exc)
            return
        prefix = f"arn:aws:ses:{self._region}:{account_id}"
        self.associate_tenant_resource(
            tenant_name, f"{prefix}:identity/{domain}"
        )
        self.associate_tenant_resource(
            tenant_name, f"{prefix}:configuration-set/{configuration_set}"
        )

    def delete_identity(self, domain: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.ses().delete_email_identity(EmailIdentity=domain)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code != "NotFoundException":
                raise SesProvisioningError(
                    f"Could not delete {domain} ({code})"
                ) from exc

    def delete_tenant(self, tenant_name: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.ses().delete_tenant(TenantName=tenant_name)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code != "NotFoundException":
                logger.warning(
                    "SES tenant %s could not be deleted (%s)", tenant_name, code
                )

    def delete_configuration_set(self, name: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.ses().delete_configuration_set(ConfigurationSetName=name)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code != "NotFoundException":
                logger.warning(
                    "SES configuration set %s could not be deleted (%s)", name, code
                )

    # -- Route 53 ------------------------------------------------------

    def publish_records(self, hosted_zone_id: str, records: list[DnsRecord]) -> None:
        """UPSERT the records so re-provisioning is safe to repeat."""
        from botocore.exceptions import ClientError

        changes = [
            {
                "Action": "UPSERT",
                "ResourceRecordSet": {
                    "Name": record.name,
                    "Type": record.type,
                    "TTL": 1800,
                    "ResourceRecords": [{"Value": record.value}],
                },
            }
            for record in records
        ]
        try:
            self.route53().change_resource_record_sets(
                HostedZoneId=hosted_zone_id,
                ChangeBatch={
                    "Comment": "ScaleNexus per-clinic email sending identity",
                    "Changes": changes,
                },
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            raise SesProvisioningError(
                f"Could not publish DNS records ({code})"
            ) from exc

    def delete_records(self, hosted_zone_id: str, records: list[DnsRecord]) -> None:
        from botocore.exceptions import ClientError

        changes = [
            {
                "Action": "DELETE",
                "ResourceRecordSet": {
                    "Name": record.name,
                    "Type": record.type,
                    "TTL": 1800,
                    "ResourceRecords": [{"Value": record.value}],
                },
            }
            for record in records
        ]
        try:
            self.route53().change_resource_record_sets(
                HostedZoneId=hosted_zone_id,
                ChangeBatch={"Comment": "ScaleNexus teardown", "Changes": changes},
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            # Already gone is a success for teardown purposes.
            if "NotFound" not in code and code != "InvalidChangeBatch":
                raise SesProvisioningError(
                    f"Could not delete DNS records ({code})"
                ) from exc


class SesProvisioningService:
    """Async wrapper that composes the AWS calls into one provisioning step."""

    def __init__(self, client: SesProvisioningClient | None = None) -> None:
        self.client = client or SesProvisioningClient()

    async def provision(
        self,
        *,
        slug: str,
        institution_id: str,
        parent_domain: str | None = None,
        hosted_zone_id: str | None = None,
        domain: str | None = None,
        inbound_domain: str | None = None,
        tenant_name: str | None = None,
        configuration_set: str | None = None,
    ) -> ProvisionedIdentity:
        parent = parent_domain or settings.ses_sending_domain
        if not domain and not parent:
            raise SesProvisioningError(
                "No sending domain configured (SES_SENDING_DOMAIN). A parent zone "
                "this account controls is required before clinics can be provisioned."
            )
        zone_id = hosted_zone_id or settings.ses_sending_hosted_zone_id

        configured_domain = (
            normalize_domain(domain) if domain else subdomain_for(slug, str(parent))
        )
        configured_inbound = normalize_domain(inbound_domain) if inbound_domain else None
        if configured_inbound:
            if configured_inbound == configured_domain:
                raise SesProvisioningError(
                    "Use a dedicated receiving subdomain such as reply.clinic.com; "
                    "pointing the clinic's main domain MX at ScaleNexus would take over its mailbox"
                )
            if not configured_inbound.endswith(f".{configured_domain}"):
                raise SesProvisioningError(
                    "The receiving domain must be a subdomain of the sending domain"
                )
        tenant = tenant_name or safe_name(f"{slug}-{institution_id[:8]}")
        config_set = configuration_set or safe_name(
            f"{slug}-{institution_id[:8]}", prefix=settings.ses_configuration_set_prefix
        )

        records = await asyncio.to_thread(
            self.client.create_identity, configured_domain
        )
        records += await asyncio.to_thread(
            self.client.configure_mail_from, configured_domain
        )
        inbound_records: list[DnsRecord] = []
        if configured_inbound:
            inbound_records = await asyncio.to_thread(
                self.client.create_identity, configured_inbound
            )
            inbound_records = [
                DnsRecord(r.name, r.type, r.value, purpose="receiving")
                for r in inbound_records
            ]
            inbound_records.append(
                DnsRecord(
                    name=configured_inbound,
                    type="MX",
                    value=f"10 inbound-smtp.{self.client.region}.amazonaws.com",
                    purpose="receiving",
                )
            )
        await asyncio.to_thread(self.client.ensure_configuration_set, config_set)
        await asyncio.to_thread(self.client.ensure_tenant, tenant)
        await asyncio.to_thread(
            self.client.associate_tenant_resources,
            tenant,
            configured_domain,
            config_set,
        )

        published = False
        platform_managed = bool(parent) and configured_domain.endswith(f".{parent}")
        if zone_id and platform_managed:
            await asyncio.to_thread(
                self.client.publish_records, zone_id, records + inbound_records
            )
            published = True
        else:
            # No zone configured: the records are handed to the clinic to publish.
            logger.info(
                "no hosted zone configured; DNS records for %s must be published manually",
                configured_domain,
            )

        return ProvisionedIdentity(
            domain=configured_domain,
            dns_records=records,
            tenant_name=tenant,
            configuration_set=config_set,
            dns_published=published,
            inbound_domain=configured_inbound,
            inbound_dns_records=inbound_records,
        )

    async def check_status(self, domain: str) -> tuple[str, str | None]:
        return await asyncio.to_thread(self.client.identity_status, domain)

    async def check_receiving_status(self, domain: str) -> tuple[bool, str | None]:
        identity_status, detail = await self.check_status(domain)
        if identity_status != "SUCCESS":
            return False, detail or "Receiving domain identity is not verified"
        return await asyncio.to_thread(self.client.receiving_mx_status, domain)

    async def teardown(
        self,
        *,
        domain: str,
        inbound_domain: str | None = None,
        dns_records: list[DnsRecord] | None = None,
        hosted_zone_id: str | None = None,
        tenant_name: str | None = None,
        configuration_set: str | None = None,
        manage_dns: bool = True,
    ) -> None:
        """Remove everything provisioning created.

        The tenant and configuration set are torn down too. Deleting only the
        identity leaves them orphaned in the account, and both are capped
        resources — 10,000 each per region — so an institution-delete loop that
        skipped them would leak until it hit the ceiling.
        """
        zone_id = (
            hosted_zone_id or settings.ses_sending_hosted_zone_id
            if manage_dns
            else None
        )
        if zone_id and dns_records:
            await asyncio.to_thread(self.client.delete_records, zone_id, dns_records)
        await asyncio.to_thread(self.client.delete_identity, domain)
        if inbound_domain and inbound_domain != domain:
            await asyncio.to_thread(self.client.delete_identity, inbound_domain)
        if tenant_name:
            await asyncio.to_thread(self.client.delete_tenant, tenant_name)
        if configuration_set:
            await asyncio.to_thread(
                self.client.delete_configuration_set, configuration_set
            )
