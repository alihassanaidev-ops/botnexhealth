"""SMS consent, suppression, and do-not-contact enforcement."""

from __future__ import annotations

from contextlib import suppress as ignore_errors
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.sms_consent import (
    ConsentChannel,
    ConsentRecord,
    ConsentSource,
    ConsentStatus,
    DncScope,
    DoNotContact,
    SmsSuppression,
)
from src.app.services.sms_privacy import hash_phone, mask_phone

# DNC scopes that reach beyond a single location (block for the whole tenant).
_TENANT_WIDE_DNC_SCOPES = frozenset({DncScope.INSTITUTION.value, DncScope.GROUP.value})


class SmsBlockedReason(str):
    """Stable enum-like reason codes used in audit & log lines.

    Values are short identifiers — never include user-supplied strings,
    free-text bodies, or PHI in the reason.
    """

    DO_NOT_CONTACT = "do_not_contact"
    OPTED_OUT = "opted_out"
    CONSENT_REVOKED = "consent_revoked"


class SmsSendBlockedError(RuntimeError):
    """Raised when an SMS must not be sent for compliance reasons.

    The exception message is one of :class:`SmsBlockedReason` so it is safe
    to surface in audit metadata and log lines without PHI leakage.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SmsRecipientIdentity:
    phone_hash: str
    phone_masked: str


class SmsComplianceService:
    """Enforces SMS consent and suppression rules."""

    def __init__(self, session: AsyncSession):
        self.session = session

    def identify(self, phone: str) -> SmsRecipientIdentity:
        phone_hash = hash_phone(phone)
        if not phone_hash:
            raise ValueError("Recipient phone number is required")
        return SmsRecipientIdentity(phone_hash=phone_hash, phone_masked=mask_phone(phone))

    async def assert_can_send(
        self,
        *,
        institution_id: str,
        location_id: str | None,
        to_number: str,
        contact_id: str | None = None,
    ) -> SmsRecipientIdentity:
        """Block only explicit suppressions, DNC records, and latest revoked consent."""
        identity = self.identify(to_number)

        if await self.is_do_not_contact(
            institution_id=institution_id,
            location_id=location_id,
            phone_hash=identity.phone_hash,
            contact_id=contact_id,
        ):
            raise SmsSendBlockedError(SmsBlockedReason.DO_NOT_CONTACT)

        suppression = (
            await self.session.execute(
                select(SmsSuppression).where(
                    SmsSuppression.institution_id == institution_id,
                    SmsSuppression.channel == ConsentChannel.SMS.value,
                    SmsSuppression.phone_hash == identity.phone_hash,
                    SmsSuppression.is_active.is_(True),
                )
            )
        ).scalars().first()
        if suppression:
            raise SmsSendBlockedError(SmsBlockedReason.OPTED_OUT)

        latest_consent = (
            await self.session.execute(
                select(ConsentRecord)
                .where(
                    ConsentRecord.institution_id == institution_id,
                    ConsentRecord.channel == ConsentChannel.SMS.value,
                    ConsentRecord.phone_hash == identity.phone_hash,
                )
                .order_by(ConsentRecord.created_at.desc(), ConsentRecord.id.desc())
            )
        ).scalars().first()
        if latest_consent and latest_consent.status == ConsentStatus.REVOKED.value:
            raise SmsSendBlockedError(SmsBlockedReason.CONSENT_REVOKED)

        return identity

    async def is_do_not_contact(
        self,
        *,
        institution_id: str,
        location_id: str | None = None,
        phone_hash: str | None = None,
        contact_id: str | None = None,
    ) -> bool:
        """Scope-aware, channel-agnostic do-not-contact check (scope §11).

        A DNC blocks a send to ``(institution_id, location_id)`` when an active
        row matches the recipient (by ``phone_hash`` or ``contact_id``) and its
        scope reaches that location: ``institution``/``group`` block the whole
        tenant; ``location`` blocks only its own location. Matching on
        ``contact_id`` (not just phone) lets a DNC also cover email-only contacts.
        """
        identity_conditions = []
        if phone_hash:
            identity_conditions.append(DoNotContact.phone_hash == phone_hash)
        if contact_id:
            identity_conditions.append(DoNotContact.contact_id == contact_id)
        if not identity_conditions:
            return False

        rows = (
            await self.session.execute(
                select(DoNotContact).where(
                    DoNotContact.institution_id == institution_id,
                    DoNotContact.is_active.is_(True),
                    or_(*identity_conditions),
                )
            )
        ).scalars().all()

        for dnc in rows:
            scope = getattr(dnc, "scope", DncScope.INSTITUTION.value)
            if scope in _TENANT_WIDE_DNC_SCOPES:
                return True
            if scope == DncScope.LOCATION.value and location_id is not None:
                if dnc.location_id == location_id:
                    return True
        return False

    async def set_do_not_contact(
        self,
        *,
        institution_id: str,
        phone: str,
        scope: DncScope | str = DncScope.LOCATION,
        location_id: str | None = None,
        contact_id: str | None = None,
        source: ConsentSource | str = ConsentSource.MANUAL,
        reason: str | None = None,
        created_by_user_id: str | None = None,
    ) -> DoNotContact:
        """Create (or return the existing active) do-not-contact record.

        ``scope`` tiers reach: ``location`` (this location's sender only),
        ``institution`` (whole tenant), or ``group`` (privileged DSO-wide). The
        institution/group tiers are the "remove me everywhere" privileged action
        (scope §11); the caller is responsible for the RBAC step-up.
        """
        identity = self.identify(phone)
        scope_value = scope.value if isinstance(scope, DncScope) else scope
        existing = (
            await self.session.execute(
                select(DoNotContact).where(
                    DoNotContact.institution_id == institution_id,
                    DoNotContact.phone_hash == identity.phone_hash,
                    DoNotContact.is_active.is_(True),
                )
            )
        ).scalars().first()
        if existing:
            return existing

        row = DoNotContact(
            institution_id=institution_id,
            location_id=location_id,
            contact_id=contact_id,
            phone_hash=identity.phone_hash,
            phone_masked=identity.phone_masked,
            scope=scope_value,
            is_active=True,
            source=source.value if isinstance(source, ConsentSource) else source,
            reason=reason,
            created_by_user_id=created_by_user_id,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            with ignore_errors(Exception):
                self.session.expunge(row)
            existing = (
                await self.session.execute(
                    select(DoNotContact).where(
                        DoNotContact.institution_id == institution_id,
                        DoNotContact.phone_hash == identity.phone_hash,
                        DoNotContact.is_active.is_(True),
                    )
                )
            ).scalars().first()
            if existing:
                return existing
            raise
        return row

    async def suppress(
        self,
        *,
        institution_id: str,
        phone: str,
        location_id: str | None = None,
        contact_id: str | None = None,
        source: ConsentSource | str = ConsentSource.MANUAL,
        keyword: str | None = None,
        reason: str | None = None,
        created_by_user_id: str | None = None,
    ) -> SmsSuppression:
        """Create an active suppression if one does not already exist."""
        identity = self.identify(phone)
        active = await self._active_suppression(
            institution_id=institution_id,
            phone_hash=identity.phone_hash,
        )
        if active:
            return active

        row = SmsSuppression(
            institution_id=institution_id,
            location_id=location_id,
            contact_id=contact_id,
            channel=ConsentChannel.SMS.value,
            phone_hash=identity.phone_hash,
            phone_masked=identity.phone_masked,
            is_active=True,
            source=source.value if isinstance(source, ConsentSource) else source,
            keyword=keyword,
            reason=reason,
            created_by_user_id=created_by_user_id,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            with ignore_errors(Exception):
                self.session.expunge(row)
            active = await self._active_suppression(
                institution_id=institution_id,
                phone_hash=identity.phone_hash,
            )
            if active:
                return active
            raise

        await self.record_consent(
            institution_id=institution_id,
            phone=phone,
            status=ConsentStatus.REVOKED,
            location_id=location_id,
            contact_id=contact_id,
            source=source,
            reason=reason or "SMS opt-out",
            created_by_user_id=created_by_user_id,
        )
        return row

    async def release_suppression(
        self,
        *,
        institution_id: str,
        phone: str,
        source: ConsentSource | str = ConsentSource.MANUAL,
        reason: str | None = None,
        released_by_user_id: str | None = None,
        grant_consent: bool = True,
        location_id: str | None = None,
        contact_id: str | None = None,
    ) -> int:
        """Release all active suppressions for a phone within an institution."""
        identity = self.identify(phone)
        rows = (
            await self.session.execute(
                select(SmsSuppression).where(
                    SmsSuppression.institution_id == institution_id,
                    SmsSuppression.channel == ConsentChannel.SMS.value,
                    SmsSuppression.phone_hash == identity.phone_hash,
                    SmsSuppression.is_active.is_(True),
                )
            )
        ).scalars().all()

        now = datetime.now(timezone.utc)
        for row in rows:
            row.is_active = False
            row.released_at = now
            row.released_by_user_id = released_by_user_id

        if rows and grant_consent:
            await self.record_consent(
                institution_id=institution_id,
                phone=phone,
                status=ConsentStatus.GRANTED,
                location_id=location_id,
                contact_id=contact_id,
                source=source,
                reason=reason or "SMS opt-in",
                created_by_user_id=released_by_user_id,
            )
        return len(rows)

    async def record_consent(
        self,
        *,
        institution_id: str,
        phone: str,
        status: ConsentStatus | str,
        location_id: str | None = None,
        contact_id: str | None = None,
        source: ConsentSource | str = ConsentSource.MANUAL,
        reason: str | None = None,
        created_by_user_id: str | None = None,
    ) -> ConsentRecord:
        identity = self.identify(phone)
        row = ConsentRecord(
            institution_id=institution_id,
            location_id=location_id,
            contact_id=contact_id,
            channel=ConsentChannel.SMS.value,
            phone_hash=identity.phone_hash,
            phone_masked=identity.phone_masked,
            status=status.value if isinstance(status, ConsentStatus) else status,
            source=source.value if isinstance(source, ConsentSource) else source,
            reason=reason,
            created_by_user_id=created_by_user_id,
        )
        self.session.add(row)
        return row

    async def _active_suppression(self, *, institution_id: str, phone_hash: str) -> SmsSuppression | None:
        return (
            await self.session.execute(
                select(SmsSuppression).where(
                    SmsSuppression.institution_id == institution_id,
                    SmsSuppression.channel == ConsentChannel.SMS.value,
                    SmsSuppression.phone_hash == phone_hash,
                    SmsSuppression.is_active.is_(True),
                )
            )
        ).scalars().first()

    async def record_consent_identity(
        self,
        *,
        institution_id: str,
        phone_hash: str,
        phone_masked: str,
        status: ConsentStatus | str,
        location_id: str | None = None,
        contact_id: str | None = None,
        source: ConsentSource | str = ConsentSource.MANUAL,
        reason: str | None = None,
        created_by_user_id: str | None = None,
    ) -> ConsentRecord:
        row = ConsentRecord(
            institution_id=institution_id,
            location_id=location_id,
            contact_id=contact_id,
            channel=ConsentChannel.SMS.value,
            phone_hash=phone_hash,
            phone_masked=phone_masked,
            status=status.value if isinstance(status, ConsentStatus) else status,
            source=source.value if isinstance(source, ConsentSource) else source,
            reason=reason,
            created_by_user_id=created_by_user_id,
        )
        self.session.add(row)
        return row
