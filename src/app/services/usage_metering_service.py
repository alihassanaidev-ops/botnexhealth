"""Usage-metering ingestion (Plan 11 core).

Captures per-interaction consumption signals (SMS segments/cost, email sends,
voice minutes) as :class:`UsageEvent` rows. Recording is idempotent on
``(institution_id, idempotency_key)`` so repeated provider webhook deliveries
never double-count.

Two entry points:

* :class:`UsageMeteringService` — call ``record(...)`` when you already hold an
  RLS session whose context is authorized for ``usage_events`` (e.g. the
  ``celery`` automation-step session). The insert participates in the caller's
  transaction and is protected by a savepoint.
* :func:`record_usage_event` — a best-effort module helper (mirrors
  ``services/dead_letter.py:capture_dead_letter``) that opens its own
  RLS-scoped session. Use it from contexts whose session is not authorized for
  ``usage_events`` (e.g. the public Twilio status-callback webhook).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.database import (
    get_system_db_session,
    init_database,
    is_database_initialized,
)
from src.app.models.usage_event import UsageEvent

logger = logging.getLogger(__name__)

# RLS context type used when this module opens its own session. Must be one of
# the system contexts allowed by the usage_events RLS policy (see the
# consolidated baseline migration's ``_usage_events_expr``).
USAGE_METERING_CONTEXT = "usage_metering"

# TODO(Plan 03): The voice channel (channel="voice", provider="retell",
# minutes/dials) will be metered by the Retell voice executor once it lands.
# Call UsageMeteringService.record(...) from that executor's success path with
# the call's billable minutes/dials and the Retell call id as idempotency_key.
# Do not implement voice ingestion here — this note marks the integration point.


class UsageMeteringService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(
        self,
        *,
        institution_id: str,
        channel: str,
        direction: str,
        provider: str,
        location_id: str | None = None,
        segments: int | None = None,
        minutes: Decimal | float | None = None,
        dials: int | None = None,
        emails: int | None = None,
        cost_amount: Decimal | float | None = None,
        currency: str = "USD",
        provider_message_id: str | None = None,
        external_ref: str | None = None,
        idempotency_key: str | None = None,
        occurred_at: datetime | None = None,
    ) -> UsageEvent | None:
        """Insert one UsageEvent idempotently.

        Returns the created event, or ``None`` when a row with the same
        ``(institution_id, idempotency_key)`` already exists (treated as an
        already-recorded no-op). The INSERT is wrapped in a savepoint so a
        unique-key collision never poisons the caller's outer transaction.
        """
        event = UsageEvent(
            institution_id=institution_id,
            location_id=location_id,
            channel=channel,
            direction=direction,
            provider=provider,
            segments=segments,
            minutes=minutes,
            dials=dials,
            emails=emails,
            cost_amount=cost_amount,
            currency=currency,
            provider_message_id=provider_message_id,
            external_ref=external_ref,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at or datetime.now(timezone.utc),
        )
        self.session.add(event)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError:
            # A concurrent/replayed delivery already recorded this key.
            logger.info(
                "usage_event already recorded: institution=%s channel=%s key=%s",
                institution_id, channel, idempotency_key,
            )
            return None
        return event


async def record_usage_event(
    *,
    institution_id: str,
    channel: str,
    direction: str,
    provider: str,
    location_id: str | None = None,
    segments: int | None = None,
    minutes: Decimal | float | None = None,
    dials: int | None = None,
    emails: int | None = None,
    cost_amount: Decimal | float | None = None,
    currency: str = "USD",
    provider_message_id: str | None = None,
    external_ref: str | None = None,
    idempotency_key: str | None = None,
    occurred_at: datetime | None = None,
) -> None:
    """Best-effort usage capture that opens its own RLS-scoped session.

    Safe to call from tasks/webhooks whose ambient session is not authorized
    for ``usage_events``. Never raises — a metering failure must not break the
    caller's primary flow.
    """
    try:
        if not settings.database_url:
            logger.warning(
                "Skipping usage capture because DATABASE_URL is not configured"
            )
            return
        if not is_database_initialized():
            init_database(settings.database_url)
        async with get_system_db_session(
            USAGE_METERING_CONTEXT,
            institution_id=institution_id,
            location_id=location_id,
        ) as session:
            svc = UsageMeteringService(session)
            await svc.record(
                institution_id=institution_id,
                channel=channel,
                direction=direction,
                provider=provider,
                location_id=location_id,
                segments=segments,
                minutes=minutes,
                dials=dials,
                emails=emails,
                cost_amount=cost_amount,
                currency=currency,
                provider_message_id=provider_message_id,
                external_ref=external_ref,
                idempotency_key=idempotency_key,
                occurred_at=occurred_at,
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — best-effort, never break caller
        logger.warning("Failed to record usage event: %s", exc)


def parse_cost_amount(raw: str | None) -> Decimal | None:
    """Parse a Twilio ``Price`` string into a positive Decimal.

    Twilio reports Price as a negative string (amount debited, e.g.
    ``"-0.00750"``). Usage cost is stored as a positive magnitude.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return abs(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def parse_segments(raw: str | None) -> int | None:
    """Parse a Twilio ``NumSegments`` string into an int (None if unparseable)."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None
