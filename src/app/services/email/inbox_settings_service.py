"""Effective institution/location settings for the inbound email channel."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.models.email_inbox_setting import EmailInboxSetting
from src.app.models.email_sending_identity import EmailSendingIdentity
from src.app.models.institution_location import InstitutionLocation
from src.app.services.email.reply_address import make_inbox_address


@dataclass(frozen=True)
class EffectiveInboxSettings:
    institution_id: str
    location_id: str | None
    is_enabled: bool
    allow_new_contacts: bool
    stop_automation_on_reply: bool
    forward_to: str | None
    inherited: bool
    email_identity_id: str | None
    inbound_domain: str | None

    @property
    def receiving_pipeline_ready(self) -> bool:
        """Whether the shared SES storage/queue path is configured."""
        return bool(settings.ses_inbound_bucket and settings.ses_inbound_queue_url)

    @property
    def platform_fallback_ready(self) -> bool:
        """Whether a location can select the ScaleNexus receiving fallback."""
        return bool(settings.ses_inbound_domain and self.receiving_pipeline_ready)

    @property
    def platform_ready(self) -> bool:
        # Compatibility name retained for existing clients. This represents
        # the *selected* receiving path, which may be clinic-owned.
        return bool(self.inbound_domain and self.receiving_pipeline_ready)

    @property
    def inbox_address(self) -> str | None:
        # A cold email must land in one operational queue. Institution defaults
        # are inherited controls, not a routable address; otherwise a
        # multi-location practice would receive an attributed but unassignable
        # message.
        if not self.inbound_domain or not self.location_id:
            return None
        return make_inbox_address(
            self.inbound_domain,
            institution_id=self.institution_id,
            location_id=self.location_id,
        )


class InboxSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, institution_id: str, location_id: str | None = None
    ) -> EffectiveInboxSettings:
        row = await self._exact(institution_id, location_id)
        inherited = False
        if row is None and location_id:
            row = await self._exact(institution_id, None)
            inherited = row is not None
        identity: EmailSendingIdentity | None = None
        if row is not None and row.email_identity_id:
            identity = await self.session.get(
                EmailSendingIdentity, row.email_identity_id
            )
            if identity is not None and str(identity.institution_id) != str(institution_id):
                identity = None
        domain = (
            identity.inbound_domain
            if identity is not None and identity.inbound_enabled
            else (None if row is not None and row.email_identity_id else settings.ses_inbound_domain)
        )
        return EffectiveInboxSettings(
            institution_id=institution_id,
            location_id=location_id,
            is_enabled=bool(row and row.is_enabled),
            allow_new_contacts=bool(row and row.allow_new_contacts),
            stop_automation_on_reply=True if row is None else bool(row.stop_automation_on_reply),
            forward_to=row.forward_to if row else None,
            inherited=inherited,
            email_identity_id=(str(row.email_identity_id) if row and row.email_identity_id else None),
            inbound_domain=domain,
        )

    async def upsert(
        self,
        institution_id: str,
        location_id: str | None,
        *,
        is_enabled: bool,
        allow_new_contacts: bool,
        stop_automation_on_reply: bool,
        forward_to: str | None,
        email_identity_id: str | None = None,
    ) -> EffectiveInboxSettings:
        if location_id:
            location = await self.session.get(InstitutionLocation, location_id)
            if location is None or str(location.institution_id) != str(institution_id):
                raise ValueError("Location does not belong to this institution")
        if is_enabled and not (
            settings.ses_inbound_bucket and settings.ses_inbound_queue_url
        ):
            raise ValueError("Inbound email infrastructure is not ready")
        if email_identity_id:
            identity = await self.session.get(EmailSendingIdentity, email_identity_id)
            if identity is None or str(identity.institution_id) != str(institution_id):
                raise ValueError("Receiving domain does not belong to this institution")
            if not identity.inbound_domain:
                raise ValueError("Selected domain has no receiving subdomain")
            if is_enabled and not identity.inbound_enabled:
                raise ValueError("Selected receiving domain must be verified and activated first")
        elif is_enabled and not settings.ses_inbound_domain:
            raise ValueError("The ScaleNexus receiving fallback is not configured")
        row = await self._exact(institution_id, location_id)
        if row is None:
            row = EmailInboxSetting(
                institution_id=institution_id, location_id=location_id
            )
            self.session.add(row)
        row.is_enabled = is_enabled
        row.allow_new_contacts = allow_new_contacts
        row.stop_automation_on_reply = stop_automation_on_reply
        row.forward_to = forward_to
        row.email_identity_id = email_identity_id
        await self.session.flush()
        return await self.get(institution_id, location_id)

    async def _exact(
        self, institution_id: str, location_id: str | None
    ) -> EmailInboxSetting | None:
        query = select(EmailInboxSetting).where(
            EmailInboxSetting.institution_id == institution_id
        )
        query = query.where(
            EmailInboxSetting.location_id == location_id
            if location_id
            else EmailInboxSetting.location_id.is_(None)
        )
        return (await self.session.execute(query)).scalar_one_or_none()
