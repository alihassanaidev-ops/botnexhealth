"""Backfill historical Retell calls into a tenant from the markdown export.

One-off importer for **no-PMS** institutions whose call history predates the
dashboard. It reads the ``Phase*.md`` export, rebuilds the Retell post-call
payload for each call, and writes ``Call`` + ``Contact`` rows using the same
field mapping as :meth:`PostCallService.process_call_analyzed_event`.

Usage (dry run is the default — nothing is written without ``--commit``)::

    python -m src.app.scripts.import_calls_from_export \\
        --institution-slug test-no-pms \\
        --location-slug no-pms-loc-1 \\
        --export-dir Call_Details

    # ...then, once the dry-run report looks right:
    python -m src.app.scripts.import_calls_from_export ... --commit

Idempotency: ``calls.retell_call_id`` is UNIQUE, and already-imported calls are
skipped, so re-running is safe.

PHI note: this export is **not** PII-redacted — it carries real names, DOBs and
phone numbers. The raw summary/transcript always go to the AES-256-GCM
encrypted columns. By default they are *also* written to the plaintext
``scrubbed_*`` columns so the dashboard list view can render them; those
columns are normally reserved for Retell's redacted variants, so pass
``--encrypted-only`` to leave them NULL and keep all PHI encrypted (the UI then
shows "—" in the list and requires an audited reveal).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.app.config import settings
from src.app.models.call import Call, PatientStatus
from src.app.models.contact import Contact
from src.app.models.contact_location_access import ContactLocationAccess
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.services.custom_field_service import CustomFieldService
from src.app.services.post_call_service import RETELL_STATUS_MAP
from src.app.services.retention_policy import (
    clinical_record_retain_until,
    default_recording_retain_until,
    retention_profile_for,
)
from src.app.services.sms_privacy import hash_phone

logger = logging.getLogger(__name__)

# The export renders wall-clock timestamps in the clinic's local zone.
EXPORT_TZ = ZoneInfo("America/Toronto")

# Placeholder strings the export uses for "we didn't capture this".
_NULLISH = {
    "", "-", "—", "n/a", "na", "none", "unknown", "not provided", "not specified",
    "not mentioned", "not applicable", "not available", "not discussed",
}

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], start=1)
}


# ─────────────────────────────────────────────────────────────────────────────
# Parsing (pure — unit tested in tests/unit/test_import_calls_from_export.py)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ParsedCall:
    """One ``### N. `call_x`` block from the export."""

    index: int
    retell_call_id: str
    meta: dict[str, str] = field(default_factory=dict)
    variables: dict[str, str] = field(default_factory=dict)
    summary: str | None = None
    recording_url: str | None = None
    transcript: str | None = None

    # -- convenience accessors -------------------------------------------------
    @property
    def direction(self) -> str:
        return (self.meta.get("Direction") or "inbound").strip().lower()

    @property
    def from_number(self) -> str | None:
        return _clean(self.meta.get("From"))

    @property
    def sentiment(self) -> str | None:
        return _clean(self.meta.get("User Sentiment"))

    @property
    def disconnection_reason(self) -> str | None:
        """Retell's ``Ended Reason`` — rendered in backticks by the export."""
        return _clean(self.meta.get("Ended Reason"))

    @property
    def call_status_raw(self) -> str | None:
        """The post-call classification (Extracted Variables), not the
        lifecycle value that shares the label in the metadata table."""
        return _clean(self.variables.get("Call Status"))

    @property
    def started_at(self) -> datetime | None:
        return _parse_export_dt(self.meta.get("Date / Time (EDT)"))

    @property
    def duration_seconds(self) -> int | None:
        return _parse_duration(self.meta.get("Duration"))

    @property
    def is_new_patient(self) -> bool:
        return _parse_bool(self.variables.get("New Patient?"))

    @property
    def is_emergency(self) -> bool:
        return _parse_bool(self.variables.get("Emergency"))

    @property
    def first_name(self) -> str | None:
        return _clean(self.variables.get("First Name"))

    @property
    def last_name(self) -> str | None:
        return _clean(self.variables.get("Last Name"))

    @property
    def full_name(self) -> str | None:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) or None

    @property
    def date_of_birth(self) -> str | None:
        return _parse_export_dob(self.variables.get("Date Of Birth"))

    @property
    def availability(self) -> str | None:
        return _clean(self.variables.get("Availability"))


def _clean(value: str | None) -> str | None:
    """Normalize an export cell, mapping placeholder text to ``None``."""
    if value is None:
        return None
    stripped = value.strip().strip("`")
    return None if stripped.lower() in _NULLISH else stripped


def _parse_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"yes", "true", "y"}


def _parse_duration(value: str | None) -> int | None:
    """``"4m 11s"`` / ``"32s"`` / ``"0s"`` -> seconds."""
    if not value:
        return None
    minutes = re.search(r"(\d+)\s*m", value)
    seconds = re.search(r"(\d+)\s*s", value)
    if not minutes and not seconds:
        return None
    total = (int(minutes.group(1)) * 60 if minutes else 0) + (
        int(seconds.group(1)) if seconds else 0
    )
    return total


def _parse_export_dt(value: str | None) -> datetime | None:
    """``"2026-08-28 18:19:41"`` (clinic-local) -> tz-aware UTC datetime."""
    cleaned = _clean(value)
    if not cleaned:
        return None
    try:
        naive = datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=EXPORT_TZ).astimezone(timezone.utc)


def _parse_export_dob(value: str | None) -> str | None:
    """``"August-29-1961"`` -> ``"1961-08-29"``. Returns None if unparseable."""
    cleaned = _clean(value)
    if not cleaned:
        return None
    match = re.match(r"^([A-Za-z]+)-(\d{1,2})-(\d{4})$", cleaned)
    if not match:
        return None
    month = _MONTHS.get(match.group(1).lower())
    if not month:
        return None
    return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(2)):02d}"


def transcript_to_turns(transcript: str | None) -> list[dict] | None:
    """Rebuild Retell's turn-by-turn structure from the flattened export.

    The export collapses ``transcript_with_tool_calls`` to ``Agent:``/``User:``
    lines, so tool invocations are unrecoverable — this reconstructs speaker
    turns only, which is what the transcript view renders.
    """
    if not transcript:
        return None
    text = transcript.strip()
    if not text or text.startswith("(no transcript"):
        return None

    turns: list[dict] = []
    role: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if role and buffer:
            content = " ".join(line.strip() for line in buffer).strip()
            if content:
                turns.append({"role": role, "content": content})

    for line in text.splitlines():
        match = re.match(r"^(Agent|User):\s*(.*)$", line.strip())
        if match:
            flush()
            role = "agent" if match.group(1) == "Agent" else "user"
            buffer = [match.group(2)]
        elif role:
            buffer.append(line)
    flush()
    return turns or None


def parse_markdown_export(text: str) -> list[ParsedCall]:
    """Parse one ``Phase*.md`` file into its call blocks."""
    text = text.replace("\r\n", "\n")
    chunks = re.split(r"(?m)^### (\d+)\. `(call_\w+)`$", text)

    calls: list[ParsedCall] = []
    # chunks == [preamble, idx, call_id, body, idx, call_id, body, ...]
    for i in range(1, len(chunks), 3):
        index, call_id, body = int(chunks[i]), chunks[i + 1], chunks[i + 2]
        parsed = ParsedCall(index=index, retell_call_id=call_id)

        meta_part, _, rest = body.partition("**Extracted Variables**")
        for key, value in re.findall(r"^\| \*\*(.+?)\*\* \| (.*?) \|$", meta_part, re.M):
            parsed.meta[key.strip()] = value.strip()

        vars_part = rest.split("**Summary:**")[0].split("**Recording")[0]
        for key, value in re.findall(r"^\| ([^*|][^|]*?) \| (.*?) \|$", vars_part, re.M):
            key = key.strip()
            if key in {"Variable", "Field", "-"} or key.startswith("---"):
                continue
            parsed.variables[key] = value.strip()

        summary = re.search(r"^\*\*Summary:\*\* (.+?)(?=\n\n)", body, re.M | re.S)
        if summary:
            parsed.summary = summary.group(1).strip()

        recording = re.search(r"^\*\*Recording URL:\*\* (\S+)$", body, re.M)
        if recording and recording.group(1) != "-":
            parsed.recording_url = recording.group(1)

        transcript = re.search(r"\*\*Transcript\*\*\n+```\n(.*?)\n?```", body, re.S)
        if transcript:
            parsed.transcript = transcript.group(1)

        calls.append(parsed)
    return calls


def load_export_dir(export_dir: Path) -> list[ParsedCall]:
    """Parse every ``Phase*.md`` in a directory, ordered by call index."""
    files = sorted(export_dir.glob("Phase*.md"))
    if not files:
        raise SystemExit(f"No Phase*.md files found in {export_dir}")
    calls: list[ParsedCall] = []
    for path in files:
        parsed = parse_markdown_export(path.read_text(encoding="utf-8"))
        logger.info("Parsed %-32s %3d calls", path.name, len(parsed))
        calls.extend(parsed)
    calls.sort(key=lambda c: c.index)
    return calls


def normalize_status(raw: str | None) -> tuple[str | None, str | None]:
    """Map the export's ``Call Status`` cell to ``(call_status, call_tags)``.

    Mirrors ``PostCallService._parse_call_tags`` so imported rows filter and
    display exactly like webhook-written ones.
    """
    if not raw:
        return None, None
    tags: list[str] = []
    for part in raw.split(","):
        mapped = RETELL_STATUS_MAP.get(part.strip().lower())
        if mapped:
            if mapped not in tags:
                tags.append(mapped)
        else:
            logger.warning("Unrecognized Call Status token: %r — skipping", part.strip())
    if not tags:
        return None, None
    return tags[0], ",".join(tags)


def build_custom_analysis_data(call: ParsedCall) -> dict:
    """Rebuild the agent's ``custom_analysis_data`` payload.

    Keys match the live no-PMS agent exactly — including the trailing space on
    ``"Availability "`` — so ``CustomFieldService`` resolves the same
    ``retell_source_key`` definitions it does for real webhooks.
    """
    return {
        "First Name": call.first_name,
        "Last Name": call.last_name,
        "Emergency": call.is_emergency,
        "Availability ": call.availability,
        "Date of birth": call.date_of_birth,
        "New Patient?": call.is_new_patient,
        "Call Status": call.call_status_raw,
    }


def parse_agent_id(text: str) -> str | None:
    """Pull the Retell agent id out of an export file's header."""
    match = re.search(r"\*\*Agent:\*\*.*?\(`(agent_\w+)`\)", text)
    return match.group(1) if match else None


# ─────────────────────────────────────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ImportReport:
    parsed: int = 0
    imported: int = 0
    skipped_existing: int = 0
    contacts_created: int = 0
    contacts_matched: int = 0
    unmapped_status: int = 0
    custom_field_values: int = 0
    by_status: dict[str, int] = field(default_factory=dict)

    def render(self) -> str:
        lines = [
            f"  parsed              {self.parsed}",
            f"  imported            {self.imported}",
            f"  skipped (existing)  {self.skipped_existing}",
            f"  contacts created    {self.contacts_created}",
            f"  contacts matched    {self.contacts_matched}",
            f"  custom field values {self.custom_field_values}",
            f"  unmapped status     {self.unmapped_status}",
            "  by primary status:",
        ]
        for status, count in sorted(self.by_status.items(), key=lambda kv: -kv[1]):
            lines.append(f"      {status or '<none>':24} {count}")
        return "\n".join(lines)


async def _resolve_target(
    session: AsyncSession, institution_slug: str, location_slug: str
) -> tuple[Institution, InstitutionLocation]:
    institution = (
        await session.execute(
            select(Institution).where(Institution.slug == institution_slug)
        )
    ).scalar_one_or_none()
    if institution is None:
        raise SystemExit(f"No institution with slug {institution_slug!r}")
    if institution.has_pms:
        raise SystemExit(
            f"Institution {institution_slug!r} has a PMS. This importer writes the "
            "no-PMS request vocabulary (needs_booking/…) and must not run against "
            "a PMS tenant."
        )

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
            f"No location {location_slug!r} under institution {institution_slug!r}"
        )
    return institution, location


async def _get_or_create_contact(
    session: AsyncSession,
    *,
    institution_id: str,
    call: ParsedCall,
    agent_id: str | None,
    report: ImportReport,
) -> Contact | None:
    """No-PMS identity: reuse a contact only when phone AND name both agree.

    Same rule as ``PostCallService`` — a shared phone with a different name
    (parent calling for a child) stays a separate contact.
    """
    phone, full_name = call.from_number, call.full_name
    if not full_name:
        return None  # unknown caller — the call stays unlinked, as in production

    existing = None
    if phone:
        existing = (
            await session.execute(
                select(Contact).where(
                    Contact.institution_id == institution_id,
                    Contact.phone_hash == hash_phone(phone),
                    func.lower(func.trim(Contact.full_name)) == full_name.strip().lower(),
                    Contact.merged_into_id.is_(None),
                    Contact.anonymized_at.is_(None),
                )
            )
        ).scalars().first()

    if existing is not None:
        report.contacts_matched += 1
        contact = existing
        contact.last_agent_interaction_id = agent_id
        if call.date_of_birth and not contact.date_of_birth:
            contact.date_of_birth = call.date_of_birth
        return contact

    report.contacts_created += 1
    contact = Contact(
        institution_id=institution_id,
        first_name=call.first_name,
        last_name=call.last_name,
        full_name=full_name,
        is_new_patient=call.is_new_patient,
        last_agent_interaction_id=agent_id,
    )
    if phone:
        contact.phone = phone
    if call.date_of_birth:
        contact.date_of_birth = call.date_of_birth
    session.add(contact)
    await session.flush()
    return contact


async def import_calls(
    session: AsyncSession,
    calls: list[ParsedCall],
    *,
    institution_slug: str,
    location_slug: str,
    agent_id: str | None,
    encrypted_only: bool,
    resolve_callbacks: bool,
) -> ImportReport:
    """Write the parsed export into the tenant. Caller controls the commit."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    institution, location = await _resolve_target(session, institution_slug, location_slug)
    profile = retention_profile_for(institution)
    report = ImportReport(parsed=len(calls))

    existing_ids = set(
        (
            await session.execute(
                select(Call.retell_call_id).where(
                    Call.retell_call_id.in_([c.retell_call_id for c in calls])
                )
            )
        ).scalars().all()
    )
    if existing_ids:
        logger.info("%d of %d calls already present — skipping", len(existing_ids), len(calls))

    cf_service = CustomFieldService(session)

    for parsed in calls:
        if parsed.retell_call_id in existing_ids:
            report.skipped_existing += 1
            continue

        primary_status, all_tags = normalize_status(parsed.call_status_raw)
        if parsed.call_status_raw and not primary_status:
            report.unmapped_status += 1
        report.by_status[primary_status] = report.by_status.get(primary_status, 0) + 1

        contact = await _get_or_create_contact(
            session,
            institution_id=institution.id,
            call=parsed,
            agent_id=agent_id,
            report=report,
        )
        if contact is not None:
            await session.execute(
                pg_insert(ContactLocationAccess)
                .values(
                    institution_id=institution.id,
                    contact_id=contact.id,
                    location_id=location.id,
                )
                .on_conflict_do_nothing(index_elements=["contact_id", "location_id"])
            )

        started_at = parsed.started_at or datetime.now(timezone.utc)
        turns = transcript_to_turns(parsed.transcript)

        call = Call(
            institution_id=institution.id,
            contact_id=contact.id if contact else None,
            location_id=location.id,
            retell_call_id=parsed.retell_call_id,
            call_direction=parsed.direction,
            agent_used=agent_id,
            disconnection_reason=parsed.disconnection_reason,
            recording_url=parsed.recording_url,
            patient_sentiment=parsed.sentiment,
            call_status=primary_status,
            call_tags=all_tags,
            patient_status=(
                PatientStatus.CONTACTED.value
                if parsed.direction == "outbound"
                else PatientStatus.NOT_CONTACTED.value
            ),
            call_duration_seconds=parsed.duration_seconds,
            is_new_patient=parsed.is_new_patient,
            is_complaint=bool(all_tags and "complaint" in all_tags),
            is_insurance_billing=bool(all_tags and "insurance_and_billing" in all_tags),
            # The no-PMS agent has no "Appointment Detail" field, so next_action
            # stays NULL; availability has its own column.
            requested_availability=parsed.availability,
            call_date=started_at.date(),
            call_time=started_at.timetz(),
            callback_resolved=(
                resolve_callbacks and bool(all_tags and "needs_callback" in all_tags)
            ),
        )
        # Encrypted setters — raw PHI never touches a plaintext column here.
        call.summary = parsed.summary
        call.transcript_with_tool_calls = turns

        if not encrypted_only:
            # Deliberate: this export is un-redacted, so these normally-plaintext
            # "already scrubbed by Retell" columns receive real PHI. Enables the
            # dashboard list view; see --encrypted-only to opt out.
            call.scrubbed_summary = parsed.summary
            call.scrubbed_transcript_with_tool_calls = turns
            call.scrubbed_recording_url = parsed.recording_url

        call.retain_until = clinical_record_retain_until(
            started_at,
            date_of_birth=parsed.date_of_birth,
            days=profile.clinical_record_days if profile else None,
            apply_minor_extension=profile.apply_minor_extension if profile else True,
        )
        if call.recording_url:
            call.recording_retain_until = default_recording_retain_until(
                started_at, days=profile.recording_days if profile else None
            )

        session.add(call)
        await session.flush()

        report.custom_field_values += await cf_service.extract_and_save_from_webhook(
            institution_id=institution.id,
            call_id=call.id,
            custom_analysis_data=build_custom_analysis_data(parsed),
            collected_dynamic_variables={},
        )
        report.imported += 1

    return report


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="import_calls_from_export",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--institution-slug", required=True)
    parser.add_argument("--location-slug", required=True)
    parser.add_argument(
        "--export-dir", type=Path, default=Path("Call_Details"),
        help="directory holding the Phase*.md files (default: Call_Details)",
    )
    parser.add_argument(
        "--agent-id",
        help="Retell agent id to record as agent_used "
             "(default: parsed from the export header)",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="actually write. Without this the transaction is rolled back.",
    )
    parser.add_argument(
        "--encrypted-only", action="store_true",
        help="leave the plaintext scrubbed_* columns NULL so no un-redacted PHI "
             "is stored outside the encrypted columns (list view shows '—')",
    )
    parser.add_argument(
        "--resolve-callbacks", action="store_true",
        help="mark imported needs_callback rows resolved so this historical "
             "backfill does not flood the Callback Queue",
    )
    return parser


async def run(args: argparse.Namespace) -> ImportReport:
    calls = load_export_dir(args.export_dir)

    agent_id = args.agent_id
    if not agent_id:
        first = sorted(args.export_dir.glob("Phase*.md"))[0]
        agent_id = parse_agent_id(first.read_text(encoding="utf-8"))
        if agent_id:
            logger.info("Agent id from export header: %s", agent_id)
        else:
            logger.warning("No agent id in export header; agent_used will be NULL")

    admin_url = os.getenv("DATABASE_ADMIN_URL") or settings.database_url
    if not admin_url:
        raise SystemExit("DATABASE_URL/DATABASE_ADMIN_URL is not set")

    engine = create_async_engine(admin_url, poolclass=NullPool)
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionFactory() as session:
            report = await import_calls(
                session,
                calls,
                institution_slug=args.institution_slug,
                location_slug=args.location_slug,
                agent_id=agent_id,
                encrypted_only=args.encrypted_only,
                resolve_callbacks=args.resolve_callbacks,
            )
            if args.commit:
                await session.commit()
                logger.info("COMMITTED")
            else:
                await session.rollback()
                logger.info("DRY RUN — rolled back, nothing written")
        return report
    finally:
        await engine.dispose()


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = _build_parser().parse_args()
    try:
        report = asyncio.run(run(args))
    except SystemExit:
        raise
    except Exception:
        logger.exception("Call import failed")
        return 1

    mode = "COMMIT" if args.commit else "DRY RUN"
    print(f"\n=== Call import ({mode}) ===\n{report.render()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
