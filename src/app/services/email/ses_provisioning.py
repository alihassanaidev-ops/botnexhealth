"""Provision and verify a clinic's SES sending domain.

The clinic does nothing. Each clinic gets a subdomain of a parent zone this
account controls (``brightsmile.mail.scalenexus.ai``), so the DKIM records can
be published straight into Route 53 — no DNS work, no IT ticket, no waiting on
the practice.

Alongside the identity, each clinic gets an SES **tenant** and a **configuration
set**. That is what keeps one clinic's bounce rate from pausing everyone else's
sending, and lets bounce and complaint events be attributed back to the clinic
that caused them.

boto3 is synchronous; every call here runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from src.app.config import settings

logger = logging.getLogger(__name__)

#: SES tenant and configuration-set names allow a restricted character set.
_NAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9_-]")
_MAX_NAME_LEN = 64


class SesProvisioningError(RuntimeError):
    """Provisioning could not complete. Safe to retry unless stated otherwise."""


@dataclass(frozen=True)
class DnsRecord:
    name: str
    type: str
    value: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "type": self.type, "value": self.value}


@dataclass(frozen=True)
class ProvisionedIdentity:
    domain: str
    dns_records: list[DnsRecord]
    tenant_name: str
    configuration_set: str
    #: True when the records were published into our own Route 53 zone. False
    #: means a clinic-owned domain whose records the clinic must publish.
    dns_published: bool


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


class SesProvisioningClient:
    """Thin adapter over the AWS calls, so the service above it stays testable."""

    def __init__(self, region: str | None = None, ses=None, route53=None) -> None:  # noqa: ANN001
        self._region = region or settings.ses_region
        self._ses = ses
        self._route53 = route53

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
            )
            for token in tokens
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
            logger.warning("SES configuration set %s could not be created (%s)", name, code)

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
                tenant_name, resource_arn, code,
            )

    def delete_identity(self, domain: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.ses().delete_email_identity(EmailIdentity=domain)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code != "NotFoundException":
                raise SesProvisioningError(f"Could not delete {domain} ({code})") from exc

    def delete_tenant(self, tenant_name: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.ses().delete_tenant(TenantName=tenant_name)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code != "NotFoundException":
                logger.warning("SES tenant %s could not be deleted (%s)", tenant_name, code)

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
            raise SesProvisioningError(f"Could not publish DNS records ({code})") from exc

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
                raise SesProvisioningError(f"Could not delete DNS records ({code})") from exc


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
    ) -> ProvisionedIdentity:
        parent = parent_domain or settings.ses_sending_domain
        if not parent:
            raise SesProvisioningError(
                "No sending domain configured (SES_SENDING_DOMAIN). A parent zone "
                "this account controls is required before clinics can be provisioned."
            )
        zone_id = hosted_zone_id or settings.ses_sending_hosted_zone_id

        domain = subdomain_for(slug, parent)
        tenant = safe_name(f"{slug}-{institution_id[:8]}")
        config_set = safe_name(
            f"{slug}-{institution_id[:8]}", prefix=settings.ses_configuration_set_prefix
        )

        records = await asyncio.to_thread(self.client.create_identity, domain)
        await asyncio.to_thread(self.client.ensure_configuration_set, config_set)
        await asyncio.to_thread(self.client.ensure_tenant, tenant)

        published = False
        if zone_id:
            await asyncio.to_thread(self.client.publish_records, zone_id, records)
            published = True
        else:
            # No zone configured: the records are handed to the clinic to publish.
            logger.info(
                "no hosted zone configured; DNS records for %s must be published manually",
                domain,
            )

        return ProvisionedIdentity(
            domain=domain,
            dns_records=records,
            tenant_name=tenant,
            configuration_set=config_set,
            dns_published=published,
        )

    async def check_status(self, domain: str) -> tuple[str, str | None]:
        return await asyncio.to_thread(self.client.identity_status, domain)

    async def teardown(
        self,
        *,
        domain: str,
        dns_records: list[DnsRecord] | None = None,
        hosted_zone_id: str | None = None,
        tenant_name: str | None = None,
        configuration_set: str | None = None,
    ) -> None:
        """Remove everything provisioning created.

        The tenant and configuration set are torn down too. Deleting only the
        identity leaves them orphaned in the account, and both are capped
        resources — 10,000 each per region — so an institution-delete loop that
        skipped them would leak until it hit the ceiling.
        """
        zone_id = hosted_zone_id or settings.ses_sending_hosted_zone_id
        if zone_id and dns_records:
            await asyncio.to_thread(self.client.delete_records, zone_id, dns_records)
        await asyncio.to_thread(self.client.delete_identity, domain)
        if tenant_name:
            await asyncio.to_thread(self.client.delete_tenant, tenant_name)
        if configuration_set:
            await asyncio.to_thread(
                self.client.delete_configuration_set, configuration_set
            )
