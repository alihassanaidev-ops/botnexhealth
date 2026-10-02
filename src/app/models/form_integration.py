"""Connected lead-form providers, their forms, the field map, and what landed.

The old shape put the whole burden on the clinic: we issued a token, they went
into Meta or Typeform and wired a webhook to it, and whatever arrived was
guessed at by a parser that read an answer's declared ``type``. Nobody could see
which forms existed, nothing said what a form's questions meant, and a workflow
could not say "when *this* form is submitted" — only "when some enquiry lands".

These four tables are that missing middle.

``FormProviderConnection``
    One authorised account per provider per clinic — a Facebook Page or a
    Typeform user. Holds the access token, encrypted, and the fact of it going
    stale, because a silently expired token looks exactly like a form nobody
    filled in.

``FormDefinition``
    One synced form. ``fields`` is what the provider says the form asks, cached
    so the mapping screen and the workflow builder can both show real question
    labels without a live API call on every render.

``FormFieldMapping``
    What each question means here. A question mapped to nothing is ignored on
    purpose — the alternative, guessing, is what produced leads with a phone
    number lifted out of a free-text box.

``FormSubmission``
    The idempotency claim and the audit trail for one submitted response. The
    raw provider payload is encrypted and expires; only answers mapped to
    non-PHI targets are kept in the clear, because those are the ones a
    workflow's if/else branches on and the ones that are safe to carry there.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value


class FormProvider(str, Enum):
    """Providers a clinic can connect. Both are OAuth; neither is a webhook URL
    the clinic assembles by hand."""

    META = "meta"
    TYPEFORM = "typeform"


class FormConnectionStatus(str, Enum):
    ACTIVE = "active"
    #: The token stopped working. Distinct from ``revoked`` because it is the
    #: clinic's problem to fix by reconnecting, not a choice they made.
    NEEDS_REAUTH = "needs_reauth"
    REVOKED = "revoked"


class FormWebhookStatus(str, Enum):
    #: Nothing registered with the provider yet, so nothing will arrive.
    NONE = "none"
    REGISTERED = "registered"
    FAILED = "failed"


class FormSubmissionStatus(str, Enum):
    RECEIVED = "received"
    PROCESSED = "processed"
    #: Something broke while processing — a provider call, a write.
    FAILED = "failed"
    #: Deliberately not processed: the form is switched off, or nothing on it
    #: maps to a way of reaching the person. Recorded rather than logged,
    #: because a lead nobody can see was lost is what actually costs money.
    DROPPED = "dropped"


class FormFieldTarget(str, Enum):
    """Where a form question lands."""

    #: A first-class column on Contact — name, email, phone, notes.
    CONTACT_FIELD = "contact_field"
    #: An institution-defined custom field on the contact.
    CUSTOM_FIELD = "custom_field"
    #: Deliberately dropped. An unmapped question is *not* silently kept.
    IGNORE = "ignore"


#: Contact columns a question may be mapped onto. Deliberately short: these are
#: the fields intake already knows how to write, and anything else belongs in a
#: custom field where the clinic has named it and declared whether it is PHI.
CONTACT_FIELD_KEYS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "full_name",
    "email",
    "phone",
    "notes",
)

#: Contact targets that carry identifying detail. A question mapped to one of
#: these never reaches a workflow's run context — the Contact already holds it,
#: and merge fields are the audited way to read it back.
PHI_CONTACT_FIELD_KEYS: frozenset[str] = frozenset(CONTACT_FIELD_KEYS)


def generate_webhook_secret() -> str:
    """A per-form shared secret we hand the provider so it can sign its posts."""
    return secrets.token_urlsafe(32)


class FormProviderConnection(Base):
    """One clinic's authorisation to read forms from one provider account."""

    __tablename__ = "form_provider_connections"
    __table_args__ = (
        UniqueConstraint(
            "institution_id",
            "provider",
            "account_ref",
            name="uq_form_provider_connections_account",
        ),
        Index(
            "ix_form_provider_connections_institution",
            "institution_id",
            "provider",
        ),
        # Meta posts every clinic's leads to one platform-wide URL and
        # identifies the clinic only by the Page id in the body, so that id has
        # to be resolvable before any tenant context exists.
        Index("ix_form_provider_connections_account_ref", "provider", "account_ref"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)

    #: The provider's own id for the authorised account: a Facebook Page id, or
    #: a Typeform user id. What an inbound webhook is matched against.
    account_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    #: What the clinic will recognise the account by in the settings list.
    account_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Null means the provider issued a token with no stated expiry (Meta page
    #: tokens derived from a long-lived user token behave this way).
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    granted_scopes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FormConnectionStatus.ACTIVE.value
    )
    #: The last sync failure, in provider wording, so support can act on it.
    #: Never a token and never a submitted answer.
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True
    )
    #: Set instead of deleting the row. Disconnecting must not take the record
    #: of who came in with it — the forms, their field maps and every landed
    #: submission hang off this id.
    disconnected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @property
    def access_token(self) -> str | None:
        return decrypt_value(self.access_token_encrypted)

    @access_token.setter
    def access_token(self, value: str | None) -> None:
        self.access_token_encrypted = encrypt_value(value)

    @property
    def refresh_token(self) -> str | None:
        return decrypt_value(self.refresh_token_encrypted)

    @refresh_token.setter
    def refresh_token(self, value: str | None) -> None:
        self.refresh_token_encrypted = encrypt_value(value)


class FormDefinition(Base):
    """One form synced from a connected provider account."""

    __tablename__ = "form_definitions"
    __table_args__ = (
        UniqueConstraint(
            "institution_id",
            "provider",
            "external_form_id",
            name="uq_form_definitions_external",
        ),
        Index("ix_form_definitions_institution", "institution_id", "provider"),
        Index("ix_form_definitions_connection", "connection_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
    )
    connection_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("form_provider_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    external_form_id: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)

    #: Which location a lead from this form belongs to. Null lets a
    #: single-location practice skip a choice with one answer.
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: The provider's questions as last synced:
    #: ``[{"key", "label", "type", "options": [...]}]``. Cached so the mapping
    #: screen and the builder can show real labels without a live API call.
    fields: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    #: A form only accepts submissions once the clinic has turned it on. The
    #: default is off, so syncing an account cannot start landing leads from
    #: forms nobody has looked at.
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Recorded on every contact this form produces, so lead source stays
    #: legible without inspecting attribution.
    source_name: Mapped[str] = mapped_column(
        String(80), nullable=False, default="external_form"
    )

    #: What this form's own wording obtains. Submitting a form is not consent
    #: to be texted, and nothing here infers it from the submission — the clinic
    #: declares it per form, because they are the ones who wrote the checkbox.
    consent_sms: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consent_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: The wording shown at submission. Stored rather than summarised: it is the
    #: evidence of what the person actually agreed to.
    consent_wording: Mapped[str | None] = mapped_column(Text, nullable=True)

    webhook_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FormWebhookStatus.NONE.value
    )
    webhook_registered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Typeform signs each delivery with a secret we choose per form. Meta signs
    #: with the platform app secret instead, so this stays null there.
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: How a clinic notices a form has gone quiet, which is otherwise invisible
    #: until somebody wonders where the leads went.
    last_submission_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Set when the provider stops listing a form. Kept rather than deleted so
    #: past submissions and the workflows referencing it still resolve a name.
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @property
    def webhook_secret(self) -> str | None:
        return decrypt_value(self.webhook_secret_encrypted)

    @webhook_secret.setter
    def webhook_secret(self, value: str | None) -> None:
        self.webhook_secret_encrypted = encrypt_value(value)


class FormFieldMapping(Base):
    """What one question on one form means in this system."""

    __tablename__ = "form_field_mappings"
    __table_args__ = (
        UniqueConstraint(
            "form_id", "source_key", name="uq_form_field_mappings_source"
        ),
        Index("ix_form_field_mappings_form", "form_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
    )
    form_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("form_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: The provider's field identifier. Typeform gives a ref or id; Meta gives
    #: the question's ``key``.
    source_key: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The question as the person filling the form saw it. Stored so the
    #: mapping screen stays readable after a sync renames nothing.
    source_label: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(60), nullable=True)

    target_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FormFieldTarget.IGNORE.value
    )
    #: The Contact column for ``contact_field``. Null otherwise.
    target_contact_field: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: The custom field definition for ``custom_field``. Null otherwise.
    target_custom_field_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("custom_field_definitions.id", ondelete="CASCADE"),
        nullable=True,
    )
    #: The key this answer appears under in a workflow's run context, when it
    #: appears there at all. Derived from the target, kept explicit so a filter
    #: written against it does not change meaning when a label is edited.
    context_key: Mapped[str | None] = mapped_column(String(120), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class FormSubmission(Base):
    """One submitted response: the idempotency claim and the audit trail."""

    __tablename__ = "form_submissions"
    __table_args__ = (
        UniqueConstraint(
            "institution_id",
            "form_id",
            "external_submission_id",
            name="uq_form_submissions_external",
        ),
        Index("ix_form_submissions_form", "form_id", "received_at"),
        Index("ix_form_submissions_contact", "contact_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="SET NULL"),
        nullable=True,
    )
    form_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("form_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The provider's id for this response — a Typeform response token or a
    #: Meta leadgen id. What makes a redelivered webhook land once.
    external_submission_id: Mapped[str] = mapped_column(String(200), nullable=False)

    contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: Answers mapped to targets that carry no identifying detail. These are
    #: what a workflow's condition branches on, so they are stored in the clear
    #: — and nothing else is.
    context_answers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    #: The provider's body as received, encrypted, for the window in which a
    #: mis-mapped form can still be diagnosed.
    raw_payload_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_retain_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FormSubmissionStatus.RECEIVED.value
    )
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: When the person submitted, per the provider. ``received_at`` is when we
    #: heard about it; a redelivery days later has the original submitted_at.
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    @property
    def raw_payload(self) -> str | None:
        return decrypt_value(self.raw_payload_encrypted)

    @raw_payload.setter
    def raw_payload(self, value: str | None) -> None:
        self.raw_payload_encrypted = encrypt_value(value)
