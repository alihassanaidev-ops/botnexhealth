"""The shape every form provider is reduced to before anything else sees it.

Two providers, two very different APIs: Meta's lead ads sit behind Pages and a
Graph edge, Typeform's behind a workspace and a REST collection. Everything
downstream — sync, mapping, the webhook, the trigger — works on the normalised
records here, so adding a third provider is a new adapter and nothing else.

The normalisation deliberately keeps a field's *declared* type. That is what
lets the mapping screen offer sensible defaults and, more importantly, what
stops a phone number typed into a free-text box from being treated as a phone
number somebody then gets texted on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class FormProviderError(RuntimeError):
    """A provider call failed in a way the clinic may be able to act on.

    ``reauth_required`` separates "your authorisation expired, reconnect" from
    "the provider is having a bad day" — the first needs a person, the second
    needs a retry, and telling a clinic to reconnect when nothing is wrong is
    how a working integration gets taken apart.
    """

    def __init__(self, message: str, *, reauth_required: bool = False) -> None:
        super().__init__(message)
        self.reauth_required = reauth_required


@dataclass(frozen=True)
class ProviderAccount:
    """An account the clinic authorised us to read forms from."""

    #: The provider's id: a Facebook Page id, or a Typeform user id.
    account_ref: str
    account_name: str | None
    access_token: str
    token_expires_at: datetime | None = None
    refresh_token: str | None = None
    granted_scopes: str | None = None


@dataclass(frozen=True)
class ProviderFormField:
    """One question, as the provider describes it."""

    key: str
    label: str
    #: The provider's own type string, normalised to lower case. Kept verbatim
    #: rather than mapped to our own enum: the mapping screen shows it to a
    #: human, and a lossy translation would hide exactly the distinctions
    #: (phone vs. short text) that matter.
    type: str
    #: Present for multiple-choice questions; the values a submission can carry.
    options: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProviderForm:
    external_id: str
    name: str
    fields: list[ProviderFormField] = field(default_factory=list)


@dataclass(frozen=True)
class NormalizedSubmission:
    """One submitted response, reduced to answers keyed by field key."""

    external_submission_id: str
    #: Keys match :class:`ProviderFormField.key`, so a mapping row written from
    #: a synced field always finds its answer.
    answers: dict[str, Any]
    submitted_at: datetime | None = None
    #: Which form and account it came from, for the tenant/form resolution the
    #: webhook does before it trusts anything else in the payload.
    form_external_id: str | None = None
    account_ref: str | None = None


class FormProviderClient(Protocol):
    """What sync, mapping and the webhook need from a provider."""

    provider: str

    async def list_forms(self, account: ProviderAccount) -> list[ProviderForm]:
        """Every form on the account, with its questions."""

    async def fetch_form(
        self, account: ProviderAccount, external_form_id: str
    ) -> ProviderForm:
        """One form, refreshed."""

    async def register_webhook(
        self,
        account: ProviderAccount,
        external_form_id: str,
        *,
        callback_url: str,
        secret: str | None,
    ) -> None:
        """Ask the provider to start delivering this form's submissions."""

    async def unregister_webhook(
        self, account: ProviderAccount, external_form_id: str
    ) -> None:
        """Stop delivery. Called when a clinic disables or disconnects a form."""
