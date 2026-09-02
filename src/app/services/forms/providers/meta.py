"""Meta (Facebook) Lead Ads.

The flow Meta imposes, and why each step exists:

1. The clinic authorises our app and we get a short-lived *user* token.
2. We exchange it for a long-lived one, because the short one dies in an hour
   and a lead that arrives tomorrow needs a token that still works.
3. ``/me/accounts`` lists the Pages they administer, each with its own *page*
   token. Lead forms belong to a Page, not to a person, so the page token is
   what we store — a user token would stop working the moment that individual
   left the practice.
4. Leads arrive as a webhook on the app, not on the Page: one URL for every
   clinic we have, identified only by the Page id in the body. The webhook body
   carries a ``leadgen_id`` and nothing else useful, so the answers have to be
   fetched back with the page token.

Signature verification uses the *app* secret, since the delivery is the app's.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
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

PROVIDER = "meta"

#: Read the Pages a person administers, and read the leads on those Pages.
#: ``leads_retrieval`` is the one that needs app review before production.
OAUTH_SCOPES = (
    "pages_show_list",
    "pages_manage_metadata",
    "leads_retrieval",
    "business_management",
)

_TIMEOUT = httpx.Timeout(20.0)


def _graph_base() -> str:
    return f"https://graph.facebook.com/{settings.meta_graph_version}"


def is_configured() -> bool:
    """Whether the platform has a Meta app to authorise against at all."""
    return bool(settings.meta_app_id and settings.meta_app_secret)


def authorization_url(*, redirect_uri: str, state: str) -> str:
    """Where to send the clinic to authorise. ``state`` is signed by the caller."""
    if not is_configured():
        raise FormProviderError("Meta is not configured on this deployment")
    params = httpx.QueryParams(
        {
            "client_id": settings.meta_app_id or "",
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "scope": ",".join(OAUTH_SCOPES),
        }
    )
    return f"https://www.facebook.com/{settings.meta_graph_version}/dialog/oauth?{params}"


def verify_webhook_signature(raw_body: bytes, header: str | None) -> bool:
    """Constant-time check of ``X-Hub-Signature-256`` over the exact bytes.

    Fails closed when the app secret is unset: an unverifiable delivery is
    indistinguishable from a forged one, and this endpoint creates contacts.
    """
    secret = settings.meta_app_secret
    if not secret or not header:
        return False
    candidate = header.strip()
    if candidate.startswith("sha256="):
        candidate = candidate[len("sha256=") :]
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, candidate)


async def _get(
    client: httpx.AsyncClient, path: str, *, token: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = await client.get(
        f"{_graph_base()}{path}",
        params={**(params or {}), "access_token": token},
    )
    if response.status_code in (400, 401, 403):
        # Meta answers an expired or revoked grant with 400 as often as 401, so
        # the code alone does not separate "reconnect" from "bad request". The
        # error subcode does; anything in the OAuth family means reconnect.
        detail = _error_message(response)
        raise FormProviderError(detail, reauth_required=_is_auth_error(response))
    if response.status_code >= 400:
        raise FormProviderError(_error_message(response))
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _error_message(response: httpx.Response) -> str:
    try:
        error = (response.json() or {}).get("error") or {}
        message = str(error.get("message") or "").strip()
        if message:
            return message[:400]
    except Exception:  # noqa: BLE001 — a non-JSON error body is still an error
        pass
    return f"Meta returned {response.status_code}"


#: Meta's OAuth-family error codes: session expired, invalid token, permission
#: withdrawn. Any of them means the clinic has to reconnect; every other code is
#: a transient or request problem that reconnecting would not fix.
_REAUTH_ERROR_CODES = frozenset({102, 190, 200, 463})


def _is_auth_error(response: httpx.Response) -> bool:
    if response.status_code == 401:
        return True
    try:
        error = (response.json() or {}).get("error") or {}
        return int(error.get("code") or 0) in _REAUTH_ERROR_CODES
    except Exception:  # noqa: BLE001 — a non-JSON body tells us nothing either way
        return response.status_code == 403


async def exchange_code(*, code: str, redirect_uri: str) -> str:
    """Short-lived user token from the authorisation code."""
    if not is_configured():
        raise FormProviderError("Meta is not configured on this deployment")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"{_graph_base()}/oauth/access_token",
            params={
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        if response.status_code >= 400:
            raise FormProviderError(_error_message(response))
        token = str((response.json() or {}).get("access_token") or "")
        if not token:
            raise FormProviderError("Meta returned no access token")
        return token


async def exchange_long_lived(short_lived_token: str) -> tuple[str, datetime | None]:
    """Trade the hour-long token for the ~60-day one."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"{_graph_base()}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": short_lived_token,
            },
        )
        if response.status_code >= 400:
            raise FormProviderError(_error_message(response))
        payload = response.json() or {}
        token = str(payload.get("access_token") or "")
        if not token:
            raise FormProviderError("Meta returned no long-lived token")
        expires_in = payload.get("expires_in")
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
            if expires_in
            else None
        )
        return token, expires_at


async def list_pages(user_token: str) -> list[ProviderAccount]:
    """The Pages this person administers, each with its own page token.

    A page token derived from a long-lived user token does not itself expire,
    which is the only reason a clinic is not reconnecting every two months.
    """
    accounts: list[ProviderAccount] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        payload = await _get(
            client,
            "/me/accounts",
            token=user_token,
            params={"fields": "id,name,access_token", "limit": 100},
        )
        for row in payload.get("data") or []:
            if not isinstance(row, dict):
                continue
            page_id = str(row.get("id") or "").strip()
            page_token = str(row.get("access_token") or "").strip()
            if not page_id or not page_token:
                continue
            accounts.append(
                ProviderAccount(
                    account_ref=page_id,
                    account_name=str(row.get("name") or "") or None,
                    access_token=page_token,
                    granted_scopes=",".join(OAUTH_SCOPES),
                )
            )
    if not accounts:
        raise FormProviderError(
            "No Facebook Pages were shared with this app. Grant access to the "
            "Page that runs your lead ads and try again."
        )
    return accounts


class MetaFormClient:
    """Reads lead forms and leads for one authorised Page."""

    provider = PROVIDER

    async def list_forms(self, account: ProviderAccount) -> list[ProviderForm]:
        forms: list[ProviderForm] = []
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            payload = await _get(
                client,
                f"/{account.account_ref}/leadgen_forms",
                token=account.access_token,
                params={"fields": "id,name,status,questions", "limit": 100},
            )
            for row in payload.get("data") or []:
                if not isinstance(row, dict):
                    continue
                # An archived form cannot receive submissions, so offering it
                # in the builder would be offering a trigger that never fires.
                if str(row.get("status") or "").upper() == "ARCHIVED":
                    continue
                forms.append(_form_from_payload(row))
        return forms

    async def fetch_form(
        self, account: ProviderAccount, external_form_id: str
    ) -> ProviderForm:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            payload = await _get(
                client,
                f"/{external_form_id}",
                token=account.access_token,
                params={"fields": "id,name,questions"},
            )
        return _form_from_payload(payload)

    async def fetch_lead(
        self, account: ProviderAccount, leadgen_id: str
    ) -> NormalizedSubmission:
        """Pull one lead's answers. The webhook body carries only the id."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            payload = await _get(
                client,
                f"/{leadgen_id}",
                token=account.access_token,
                params={"fields": "id,created_time,field_data,form_id"},
            )
        return _submission_from_lead(
            payload,
            account_ref=account.account_ref,
            fallback_id=leadgen_id,
        )

    async def list_leads(
        self,
        account: ProviderAccount,
        external_form_id: str,
        *,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[NormalizedSubmission]:
        """Recent leads on one form, for the reconciliation sweep.

        Unlike the webhook path this returns the answers directly, so a lead
        missed while we were unreachable can be landed without a second call.
        """
        params: dict[str, Any] = {
            "fields": "id,created_time,field_data",
            "limit": min(limit, 500),
        }
        if since is not None:
            params["filtering"] = json.dumps(
                [
                    {
                        "field": "time_created",
                        "operator": "GREATER_THAN",
                        "value": int(since.timestamp()),
                    }
                ]
            )
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            payload = await _get(
                client,
                f"/{external_form_id}/leads",
                token=account.access_token,
                params=params,
            )
        leads: list[NormalizedSubmission] = []
        for row in payload.get("data") or []:
            if isinstance(row, dict):
                leads.append(
                    _submission_from_lead(
                        row, account_ref=account.account_ref,
                        form_external_id=external_form_id,
                    )
                )
        return leads

    async def register_webhook(
        self,
        account: ProviderAccount,
        external_form_id: str,
        *,
        callback_url: str,
        secret: str | None,
    ) -> None:
        """Subscribe the *Page* to leadgen events.

        Meta has no per-form subscription: the app is subscribed to a callback
        URL once, in the app dashboard, and each Page is then subscribed to
        ``leadgen``. ``external_form_id`` and ``callback_url`` are accepted for
        interface symmetry and deliberately unused.
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{_graph_base()}/{account.account_ref}/subscribed_apps",
                params={
                    "subscribed_fields": "leadgen",
                    "access_token": account.access_token,
                },
            )
            if response.status_code >= 400:
                raise FormProviderError(
                    _error_message(response),
                    reauth_required=_is_auth_error(response),
                )

    async def unregister_webhook(
        self, account: ProviderAccount, external_form_id: str
    ) -> None:
        """Unsubscribe the Page.

        Only called when the *connection* goes away. Disabling one form does
        not unsubscribe, because the Page's other forms would go silent with
        it — a disabled form is dropped on arrival instead.
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            await client.delete(
                f"{_graph_base()}/{account.account_ref}/subscribed_apps",
                params={"access_token": account.access_token},
            )


def _submission_from_lead(
    payload: dict[str, Any],
    *,
    account_ref: str | None,
    fallback_id: str | None = None,
    form_external_id: str | None = None,
) -> NormalizedSubmission:
    """One Meta lead reduced to answers keyed by the question's key."""
    answers: dict[str, Any] = {}
    for entry in payload.get("field_data") or []:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("name") or "").strip()
        if not key:
            continue
        values = entry.get("values") or []
        if not isinstance(values, list) or not values:
            continue
        # Meta always sends a list. One value is the overwhelmingly common
        # case; several means a multi-select, which is kept as a list so a
        # workflow condition can test membership rather than string soup.
        answers[key] = values[0] if len(values) == 1 else list(values)
    return NormalizedSubmission(
        external_submission_id=str(payload.get("id") or fallback_id or ""),
        answers=answers,
        submitted_at=_parse_time(payload.get("created_time")),
        form_external_id=str(payload.get("form_id") or form_external_id or "") or None,
        account_ref=account_ref,
    )


def _form_from_payload(row: dict[str, Any]) -> ProviderForm:
    fields: list[ProviderFormField] = []
    for question in row.get("questions") or []:
        if not isinstance(question, dict):
            continue
        key = str(question.get("key") or question.get("id") or "").strip()
        if not key:
            continue
        options = [
            str(option.get("value") or option.get("key") or "")
            for option in (question.get("options") or [])
            if isinstance(option, dict)
        ]
        fields.append(
            ProviderFormField(
                key=key,
                label=str(question.get("label") or key),
                type=str(question.get("type") or "custom").lower(),
                options=[option for option in options if option],
            )
        )
    return ProviderForm(
        external_id=str(row.get("id") or ""),
        name=str(row.get("name") or "Untitled form"),
        fields=fields,
    )


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
