"""Configure a location's recall campaign from the command line.

Sets ``InstitutionLocation.recall_config`` — the approved recall types, the
due-date window, the recall-to-appointment-type map, and the allowed providers
— and optionally upserts the location's "Recall" outbound voice profile.

This is the operator path for staging and production setup where the dashboard
recall-config editor is not yet wired. It is idempotent: re-running replaces
the stored config with the values passed.

Examples:

    python -m src.app.scripts.configure_recall_campaign \\
        --institution-slug kadri-dental \\
        --location-slug dr-kareem-kadri \\
        --recall-type-id 2288000 --recall-type-id 2288006 \\
        --appointment-map 2288000=nh-1230522 \\
        --appointment-map 2288006=nh-1230521 \\
        --provider-id nh-502294485 --provider-id nh-506291620 \\
        --due-within-days 30

    # Attach a voice profile in the same run
    python -m src.app.scripts.configure_recall_campaign ... \\
        --voice-profile-name "Recall" \\
        --retell-agent-id agent_xxx --retell-from-number +12267734006
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.app.config import settings
from src.app.models.institution import Institution
from src.app.models.institution_appointment_type import InstitutionAppointmentType
from src.app.models.institution_location import InstitutionLocation
from src.app.models.institution_provider import InstitutionProvider
from src.app.models.outbound_voice import OutboundVoiceProfile
from src.app.services.automation.recall_config import parse_recall_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("configure_recall_campaign")

_SYSTEM_UID = "00000000-0000-0000-0000-000000000000"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--institution-slug", required=True)
    parser.add_argument("--location-slug", required=True)
    parser.add_argument("--recall-type-id", action="append", default=[])
    parser.add_argument(
        "--appointment-map",
        action="append",
        default=[],
        metavar="RECALL_TYPE_ID=APPOINTMENT_TYPE_ID",
    )
    parser.add_argument("--provider-id", action="append", default=[])
    parser.add_argument("--due-within-days", type=int, default=30)
    parser.add_argument(
        "--exclude-overdue",
        action="store_true",
        help="Only enroll recalls due in the future window (not already overdue).",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear the location's recall config and exit.",
    )
    parser.add_argument("--voice-profile-name", default=None)
    parser.add_argument("--retell-agent-id", default=None)
    parser.add_argument("--retell-from-number", default=None)
    return parser.parse_args()


def _appointment_map(pairs: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--appointment-map expects RECALL_ID=APPT_ID, got {pair!r}")
        recall_type_id, appointment_type_id = pair.split("=", 1)
        mapping[recall_type_id.strip()] = appointment_type_id.strip()
    return mapping


async def _resolve_location(session, institution_slug: str, location_slug: str):
    institution = (
        await session.execute(
            select(Institution).where(Institution.slug == institution_slug)
        )
    ).scalar_one_or_none()
    if institution is None:
        raise SystemExit(f"No institution with slug {institution_slug!r}")
    location = (
        await session.execute(
            select(InstitutionLocation).where(
                InstitutionLocation.institution_id == institution.id,
                InstitutionLocation.slug == location_slug,
            )
        )
    ).scalar_one_or_none()
    if location is None:
        raise SystemExit(
            f"No location with slug {location_slug!r} in {institution_slug!r}"
        )
    return institution, location


async def _warn_unknown_ids(session, location, appointment_type_ids, provider_ids):
    known_types = {
        str(row)
        for row in (
            await session.execute(
                select(InstitutionAppointmentType.source_id).where(
                    InstitutionAppointmentType.location_id == str(location.id)
                )
            )
        ).scalars()
    }
    known_providers = {
        str(row)
        for row in (
            await session.execute(
                select(InstitutionProvider.source_id).where(
                    InstitutionProvider.location_id == str(location.id)
                )
            )
        ).scalars()
    }
    for value in appointment_type_ids:
        if value not in known_types:
            logger.warning(
                "appointment type %s is not in this location's cached catalog", value
            )
    for value in provider_ids:
        if value not in known_providers:
            logger.warning("provider %s is not in this location's cached catalog", value)


async def _upsert_voice_profile(session, institution, location, args) -> None:
    if not args.voice_profile_name:
        return
    existing = (
        await session.execute(
            select(OutboundVoiceProfile).where(
                OutboundVoiceProfile.location_id == str(location.id),
                OutboundVoiceProfile.purpose == args.voice_profile_name,
                OutboundVoiceProfile.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = OutboundVoiceProfile(
            institution_id=str(institution.id),
            location_id=str(location.id),
            purpose=args.voice_profile_name,
            display_name=args.voice_profile_name,
            is_active=True,
        )
        session.add(existing)
    if args.retell_agent_id:
        existing.retell_agent_id = args.retell_agent_id
    if args.retell_from_number:
        existing.retell_from_number = args.retell_from_number
    logger.info("Recall voice profile ready: %s", existing.id)


async def main() -> None:
    args = _parse_args()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not configured")

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        # Scripts run outside RLS context; assume the platform super-admin so
        # the location lookup works regardless of the connecting role.
        from sqlalchemy import text as sa_text

        await session.execute(
            sa_text(
                "SELECT set_config('app.context_type','user',false),"
                "set_config('app.role','SUPER_ADMIN',false),"
                "set_config('app.user_id',:uid,false)"
            ),
            {"uid": _SYSTEM_UID},
        )

        institution, location = await _resolve_location(
            session, args.institution_slug, args.location_slug
        )

        if args.clear:
            location.recall_config = None
            await session.commit()
            logger.info("Cleared recall config for %s", location.slug)
            return

        mapping = _appointment_map(args.appointment_map)
        config = {
            "approved_recall_type_ids": list(dict.fromkeys(args.recall_type_id)),
            "due_within_days": max(0, min(args.due_within_days, 365)),
            "include_overdue": not args.exclude_overdue,
            "appointment_type_map": mapping,
            "provider_ids": list(dict.fromkeys(args.provider_id)),
        }
        await _warn_unknown_ids(
            session,
            location,
            list(mapping.values()),
            config["provider_ids"],
        )

        location.recall_config = config
        await _upsert_voice_profile(session, institution, location, args)
        await session.commit()

        parsed = parse_recall_config(config)
        print(
            json.dumps(
                {
                    "institution": institution.slug,
                    "location": location.slug,
                    "recall_config": config,
                    "parsed": {
                        "approved_recall_type_ids": list(
                            parsed.approved_recall_type_ids
                        ),
                        "due_within_days": parsed.due_within_days,
                        "include_overdue": parsed.include_overdue,
                        "appointment_type_map": parsed.appointment_type_map,
                        "provider_ids": list(parsed.provider_ids),
                    },
                },
                indent=2,
            )
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
