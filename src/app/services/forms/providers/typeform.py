"""Typeform.

Simpler than Meta in every respect: one OAuth grant covers the account, forms
are a flat collection, and a webhook is registered per form at a URL we choose —
so unlike Meta, the delivery itself tells us which form it belongs to and we can
give each form its own signing secret.

Answers are keyed by the field's ``ref`` when the author set one and by the
field id otherwise. ``ref`` is the stable, human-chosen handle; preferring it
means a mapping survives the author editing question wording, which they will.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from src.app.config import settings
from src.app.services.forms.providers.base import (
    FormProviderError,
    NormalizedSubmission,
    ProviderAccount,
    ProviderForm,
    ProviderFormField,
)

logger = logging.getLogger(__name__)

PROVIDER = "typeform"

#: Read the account's forms, and manage the webhooks that deliver their
#: responses. Nothing that can edit a form.
OAUTH_SCOPES = ("forms:read", "responses:read", "webhooks:read", "webhooks:write")

#: Our webhook registration on a form. Typeform keys webhooks by tag, so a
#: fixed one means re-registering updates ours instead of stacking duplicates.
WEBHOOK_TAG = "nexus-forms"

_TIMEOUT = httpx.Timeout(20.0)

_OAUTH_AUTHORIZE_URL = "https://api.typeform.com/oauth/authorize"
_OAUTH_TOKEN_URL = "https://api.typeform.com/oauth/token"


def is_configured() -> bool:
    return bool(settings.typeform_client_id and settings.typeform_client_secret)


def authorization_url(*, redirect_uri: str, state: str) -> str:
    if not is_configured():
        raise FormProviderError("Typeform is not configured on this deployment")
    params = httpx.QueryParams(
        {
            "client_id": settings.typeform_client_id or "",
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": " ".join(OAUTH_SCOPES),
        }
    )
    return f"{_OAUTH_AUTHORIZE_URL}?{params}"


def verify_webhook_signature(raw_body: bytes, header: str | None, secret: str) -> bool:
    """Constant-time check of ``Typeform-Signature``.

    Typeform sends ``sha256=<base64>`` — base64, not hex, which is the detail
    that silently rejects every delivery if you assume otherwise.
    """
    if not header or not secret:
        return False
    candidate = header.strip()
    if candidate.startswith("sha256="):
        candidate = candidate[len("sha256=") :]
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, candidate)


async def exchange_code(*, code: str, redirect_uri: str) -> ProviderAccount:
    """Authorisation code to a stored account, in one step.

    Typeform's token response has no account identity in it, so this follows up
    with ``/me`` — the connection row needs something the clinic recognises, and
    "Typeform account" with no name is not it.
    """
    if not is_configured():
        raise FormProviderError("Typeform is not configured on this deployment")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            _OAUTH_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.typeform_client_id,
                "client_secret": settings.typeform_client_secret,
                "redirect_uri": redirect_uri,
                "scope": " ".join(OAUTH_SCOPES),
            },
        )
        if response.status_code >= 400:
            raise FormProviderError(_error_message(response))
        payload = response.json() or {}
        token = str(payload.get("access_token") or "")
        if not token:
            raise FormProviderError("Typeform returned no access token")
        refresh_token = str(payload.get("refresh_token") or "") or None

        me = await client.get(
            f"{settings.typeform_api_base_url}/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        account_ref = ""
        account_name = None
        if me.status_code < 400:
            body = me.json() or {}
            account_ref = str(body.get("user_id") or body.get("alias") or "")
            account_name = str(body.get("email") or body.get("alias") or "") or None

    return ProviderAccount(
        # Falls back to the token's own fingerprint rather than an empty string:
        # account_ref is part of a uniqueness constraint, and two accounts that
        # both answer "" would collide into one connection row.
        account_ref=account_ref or hashlib.sha256(token.encode()).hexdigest()[:32],
        account_name=account_name,
        access_token=token,
        refresh_token=refresh_token,
        granted_scopes=" ".join(OAUTH_SCOPES),
    )


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json() or {}
        message = str(
            payload.get("description") or payload.get("message") or ""
        ).strip()
        if message:
            return message[:400]
    except Exception:  # noqa: BLE001 — a non-JSON error body is still an error
        pass
    return f"Typeform returned {response.status_code}"


def _is_auth_error(response: httpx.Response) -> bool:
    return response.status_code in (401, 403)


class TypeformClient:
    provider = PROVIDER

    def _headers(self, account: ProviderAccount) -> dict[str, str]:
        return {"Authorization": f"Bearer {account.access_token}"}

    async def _request(
        self,
        account: ProviderAccount,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.request(
                method,
                f"{settings.typeform_api_base_url}{path}",
                headers=self._headers(account),
                **kwargs,
            )
        if response.status_code >= 400:
            raise FormProviderError(
                _error_message(response), reauth_required=_is_auth_error(response)
            )
        if not response.content:
            return {}
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def list_forms(self, account: ProviderAccount) -> list[ProviderForm]:
        """Every form on the account, each fetched for its questions.

        The list endpoint returns no fields, so a second call per form is
        unavoidable. Page size is capped because a large account would
        otherwise make one "Sync" click into a few hundred requests.
        """
        listing = await self._request(
            account, "GET", "/forms", params={"page_size": 200}
        )
        forms: list[ProviderForm] = []
        for row in listing.get("items") or []:
            if not isinstance(row, dict):
                continue
            form_id = str(row.get("id") or "").strip()
            if not form_id:
                continue
            try:
                forms.append(await self.fetch_form(account, form_id))
            except FormProviderError:
                # One unreadable form must not lose the rest of the sync. It is
                # listed with no fields; the mapping screen then shows nothing
                # to map, which is the honest state.
                logger.warning("typeform: could not read form %s", form_id)
                forms.append(
                    ProviderForm(
                        external_id=form_id,
                        name=str(row.get("title") or "Untitled form"),
                    )
                )
        return forms

    async def fetch_form(
        self, account: ProviderAccount, external_form_id: str
    ) -> ProviderForm:
        payload = await self._request(account, "GET", f"/forms/{external_form_id}")
        fields: list[ProviderFormField] = []
        for entry in payload.get("fields") or []:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("ref") or entry.get("id") or "").strip()
            if not key:
                continue
            choices = [
                str(choice.get("label") or "")
                for choice in ((entry.get("properties") or {}).get("choices") or [])
                if isinstance(choice, dict)
            ]
            fields.append(
                ProviderFormField(
                    key=key,
                    label=str(entry.get("title") or key),
                    type=str(entry.get("type") or "short_text").lower(),
                    options=[choice for choice in choices if choice],
                )
            )
        # Hidden fields carry UTM and campaign context. They are answers like
        # any other and are exactly what a clinic wants to branch a workflow on.
        for hidden in (payload.get("hidden") or []):
            name = str(hidden or "").strip()
            if name:
                fields.append(
                    ProviderFormField(key=name, label=name, type="hidden")
                )
        return ProviderForm(
            external_id=str(payload.get("id") or external_form_id),
            name=str((payload.get("title") or "Untitled form")),
            fields=fields,
        )

    async def list_responses(
        self,
        account: ProviderAccount,
        external_form_id: str,
        *,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[NormalizedSubmission]:
        """Recent responses, for the reconciliation sweep.

        Webhook delivery is the normal path; this exists because a delivery
        missed while we were unreachable is otherwise lost forever. The
        responses endpoint returns the same answer shape the webhook does,
        wrapped differently, so it is re-wrapped and fed through the same
        normaliser rather than parsed twice.
        """
        params: dict[str, Any] = {"page_size": min(limit, 1000)}
        if since is not None:
            # Typeform wants UTC "YYYY-MM-DDTHH:MM:SS".
            params["since"] = (
                since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            )
        payload = await self._request(
            account, "GET", f"/forms/{external_form_id}/responses", params=params
        )
        submissions: list[NormalizedSubmission] = []
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            submissions.append(
                normalize_submission({"form_response": {**item, "form_id": external_form_id}})
            )
        return submissions

    async def register_webhook(
        self,
        account: ProviderAccount,
        external_form_id: str,
        *,
        callback_url: str,
        secret: str | None,
    ) -> None:
        """Create or update our webhook on this form.

        ``PUT`` on a fixed tag, so re-registering after a URL change replaces
        ours rather than adding a second one that also delivers.
        """
        body: dict[str, Any] = {"url": callback_url, "enabled": True}
        if secret:
            body["secret"] = secret
            body["verify_ssl"] = True
        await self._request(
            account,
            "PUT",
            f"/forms/{external_form_id}/webhooks/{WEBHOOK_TAG}",
            json=body,
        )

    async def unregister_webhook(
        self, account: ProviderAccount, external_form_id: str
    ) -> None:
        try:
            await self._request(
                account,
                "DELETE",
                f"/forms/{external_form_id}/webhooks/{WEBHOOK_TAG}",
            )
        except FormProviderError:
            # Already gone, or the grant is dead. Either way there is nothing
            # left to stop delivering, and failing here would block a clinic
            # from disconnecting an integration they have decided to be rid of.
            logger.info(
                "typeform: webhook removal ignored for form=%s", external_form_id
            )


def normalize_submission(payload: dict[str, Any]) -> NormalizedSubmission:
    """Reduce a ``form_response`` webhook body to answers keyed by field key.

    Keys come from ``field.ref`` first so they match what sync stored. The
    value taken depends on the answer's declared ``type``, never on the shape of
    what arrived — the same rule the old intake parser got right and the reason
    a phone number typed into a free-text box stays free text.
    """
    response = payload.get("form_response") or {}
    definition = response.get("definition") or {}
    # The webhook's own definition block names each field by both ref and id;
    # answers reference the id, so this is what maps one to the other.
    ref_by_id = {
        str(field.get("id") or ""): str(field.get("ref") or field.get("id") or "")
        for field in (definition.get("fields") or [])
        if isinstance(field, dict)
    }

    answers: dict[str, Any] = {}
    for answer in response.get("answers") or []:
        if not isinstance(answer, dict):
            continue
        field_meta = answer.get("field") or {}
        key = str(
            field_meta.get("ref")
            or ref_by_id.get(str(field_meta.get("id") or ""))
            or field_meta.get("id")
            or ""
        ).strip()
        if not key:
            continue
        value = _answer_value(answer)
        if value is not None:
            answers[key] = value

    for name, value in (response.get("hidden") or {}).items():
        key = str(name or "").strip()
        if key and value not in (None, ""):
            answers.setdefault(key, value)

    return NormalizedSubmission(
        external_submission_id=str(
            response.get("token") or response.get("response_id") or ""
        ),
        answers=answers,
        submitted_at=_parse_time(response.get("submitted_at")),
        form_external_id=str(response.get("form_id") or "") or None,
    )


def _answer_value(answer: dict[str, Any]) -> Any:
    kind = str(answer.get("type") or "").lower()
    if kind == "choice":
        choice = answer.get("choice") or {}
        return choice.get("label") or choice.get("other")
    if kind == "choices":
        choices = answer.get("choices") or {}
        labels = list(choices.get("labels") or [])
        other = choices.get("other")
        if other:
            labels.append(other)
        return labels or None
    if kind in ("payment", "file_url"):
        # Not answers a clinic qualifies on, and a file URL is a credential in
        # its own right — deliberately dropped rather than stored.
        return None
    value = answer.get(kind) if kind else None
    return value if value not in ("", None) else None


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
