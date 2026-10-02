"""Enroll a list of people into a campaign from an uploaded CSV.

The manual trigger has always claimed to accept a CSV — the builder's own label
says so — while offering only one-contact-at-a-time enrollment. This is that
path.

Two properties matter more than throughput:

* **Preview before commit.** A CSV is the one enrollment route where a mistake
  reaches hundreds of people at once, so the caller sees exactly who would be
  contacted, who would be created, and who is excluded and why, before anything
  is written.
* **The same gates as every other route.** Rows go through consent,
  do-not-contact and suppression like any other enrollment. A CSV is not a way
  to reach someone who has opted out.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field

import phonenumbers
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflow
from src.app.models.contact import Contact, LeadStatus
from src.app.services.sms_privacy import hash_email, hash_phone

logger = logging.getLogger(__name__)

#: Matches the audience path's ceiling. One upload cannot outrun a clinic's
#: sending capacity or quietly become a mass-messaging tool.
MAX_ROWS = 500

#: Accepted header spellings, so a clinic's export does not have to be reshaped
#: by hand. Compared case-insensitively with spaces and underscores collapsed.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "phone": ("phone", "phonenumber", "mobile", "cell", "cellphone", "telephone"),
    "email": ("email", "emailaddress", "mail"),
    "first_name": ("firstname", "first", "givenname", "fname"),
    "last_name": ("lastname", "last", "surname", "familyname", "lname"),
    "external_ref": ("externalref", "patientid", "id", "reference"),
}


@dataclass
class CsvRow:
    """One parsed line, with what we resolved it to."""

    line: int
    phone: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    external_ref: str | None = None

    contact_id: str | None = None
    #: Set when the row cannot be enrolled. The caller shows it verbatim.
    excluded_reason: str | None = None
    would_create_contact: bool = False


@dataclass
class CsvEnrollmentPreview:
    rows: list[CsvRow] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def eligible(self) -> list[CsvRow]:
        return [row for row in self.rows if row.excluded_reason is None]

    @property
    def excluded(self) -> list[CsvRow]:
        return [row for row in self.rows if row.excluded_reason is not None]


def _normalise_header(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def _map_columns(fieldnames: list[str] | None) -> dict[str, str]:
    """Map this file's headers onto the fields we understand."""
    mapping: dict[str, str] = {}
    for raw in fieldnames or []:
        key = _normalise_header(raw)
        for field_name, aliases in _COLUMN_ALIASES.items():
            if key in aliases and field_name not in mapping:
                mapping[field_name] = raw
    return mapping


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def normalise_phone(raw: str | None, *, region: str = "US") -> str | None:
    """E.164, or None when the number cannot be understood.

    A number we cannot parse is worse than a missing one: it would be stored,
    hashed, and never match the consent records keyed on the real number.
    """
    text = _clean(raw)
    if text is None:
        return None
    try:
        parsed = phonenumbers.parse(text, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def parse_csv(content: bytes, *, region: str = "US") -> CsvEnrollmentPreview:
    """Parse an uploaded file into rows, without touching the database."""
    preview = CsvEnrollmentPreview()

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("latin-1")
        except Exception:
            preview.parse_errors.append("File is not readable as text.")
            return preview

    reader = csv.DictReader(io.StringIO(text))
    columns = _map_columns(reader.fieldnames)
    if not columns.get("phone") and not columns.get("email"):
        preview.parse_errors.append(
            "No phone or email column found. Expected a header row containing "
            "at least one of: phone, email."
        )
        return preview

    for index, raw_row in enumerate(reader, start=2):  # line 1 is the header
        if len(preview.rows) >= MAX_ROWS:
            preview.truncated = True
            break

        def value(name: str) -> str | None:
            column = columns.get(name)
            return _clean(raw_row.get(column)) if column else None

        row = CsvRow(
            line=index,
            phone=normalise_phone(value("phone"), region=region),
            email=(value("email") or "").lower() or None,
            first_name=value("first_name"),
            last_name=value("last_name"),
            external_ref=value("external_ref"),
        )

        if row.phone is None and row.email is None:
            raw_phone = value("phone")
            row.excluded_reason = (
                "Phone number is not valid"
                if raw_phone
                else "No phone number or email address"
            )
        preview.rows.append(row)

    if not preview.rows and not preview.parse_errors:
        preview.parse_errors.append("No data rows found.")
    return preview


class CsvEnrollmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve(
        self,
        preview: CsvEnrollmentPreview,
        *,
        institution_id: str,
    ) -> CsvEnrollmentPreview:
        """Attach an existing contact to each row, or mark it as a new one.

        Nothing is written here. The caller shows this, and only then commits.
        """
        for row in preview.rows:
            if row.excluded_reason is not None:
                continue
            existing = await self._find_contact(row, institution_id=institution_id)
            if existing is not None:
                row.contact_id = str(existing.id)
            else:
                row.would_create_contact = True
        return preview

    async def _find_contact(
        self, row: CsvRow, *, institution_id: str
    ) -> Contact | None:
        """Match on hashed phone then email, mirroring intake's precedence.

        A merged contact is skipped: matching an alias would attach the
        enrollment to a superseded record.
        """
        for column, value in (
            (Contact.phone_hash, hash_phone(row.phone) if row.phone else None),
            (Contact.email_hash, hash_email(row.email) if row.email else None),
        ):
            if not value:
                continue
            found = (
                await self.session.execute(
                    select(Contact).where(
                        Contact.institution_id == institution_id,
                        column == value,
                        Contact.merged_into_id.is_(None),
                    )
                )
            ).scalars().first()
            if found is not None:
                return found
        return None

    async def create_missing_contacts(
        self,
        preview: CsvEnrollmentPreview,
        *,
        institution_id: str,
        source: str = "csv_import",
    ) -> int:
        """Create a contact for each unmatched row. Returns how many were made."""
        created = 0
        for row in preview.rows:
            if row.excluded_reason is not None or row.contact_id:
                continue
            contact = Contact(
                institution_id=institution_id,
                first_name=row.first_name,
                last_name=row.last_name,
                full_name=" ".join(
                    part for part in (row.first_name, row.last_name) if part
                ).strip()
                or None,
                # No practice-software id: an imported row is a lead until the
                # clinic's system says otherwise.
                nexhealth_patient_id=None,
                is_new_patient=True,
                lead_source=source,
                lead_status=LeadStatus.NEW.value,
                external_ref=row.external_ref,
            )
            # Through the setters, so the hashes are written with the values.
            contact.email = row.email
            contact.phone = row.phone
            self.session.add(contact)
            await self.session.flush()
            row.contact_id = str(contact.id)
            created += 1
        return created


def csv_idempotency_key(workflow_version_id: str, upload_id: str, contact_id: str) -> str:
    """One run per contact per upload, so a retried upload cannot double-enroll."""
    return f"csv:{workflow_version_id}:{upload_id}:{contact_id}"


__all__ = [
    "MAX_ROWS",
    "CsvEnrollmentPreview",
    "CsvEnrollmentService",
    "CsvRow",
    "csv_idempotency_key",
    "normalise_phone",
    "parse_csv",
]
