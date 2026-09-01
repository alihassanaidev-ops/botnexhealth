"""The public endpoint an external form posts a lead to.

Decision C: one authenticated, rate-limited, idempotent endpoint that the
clinic's own site or a form provider they control posts to. No per-source
adapters and no OAuth into third-party CRMs.

**Shape.** A flat contract — name, email, phone — plus tolerant extraction of
the ``answers`` array that hosted form builders send, because insisting a clinic
reshape a Typeform payload before it reaches us would in practice mean they use
something else. Extraction reads the *declared type* of each answer rather than
guessing from the value, so a phone number typed into a free-text box is not
silently promoted to a phone number we would then text.

**Caveats this handles, each of which is a way it goes wrong:**

* The token is a bearer credential in a URL. It is hashed at rest, never logged,
  never echoed, and an optional signing secret lets a provider prove the body as
  well — which a URL token alone cannot do.
* An unknown, revoked or inactive token gets one answer, so the endpoint cannot
  be used to discover which tokens exist.
* A lead with neither phone nor email is refused: there is no way to contact
  them and nothing downstream could ever act.
* Consent is taken only from what the form declares. Submitting a form is not
  consent to be texted, and we will not infer it.
* Bodies are size-capped, because this is unauthenticated in the ordinary sense
  and a form provider can post anything.
* The response never says whether the lead was already known. That is the same
  reasoning as the identity gate: an open endpoint that distinguishes "new" from
  "already on file" tells anyone with a token who a clinic's patients are.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from src.app.api.rate_limit import limiter
from src.app.database import get_system_db_session
from src.app.models.enquiry_intake_source import EnquiryIntakeSource, hash_intake_token
from src.app.services.automation.enquiry_intake_service import intake_enquiry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/enquiries", tags=["Enquiry Intake"])

#: The RLS context a public intake request runs under. Reaches exactly the
#: tables intake needs, in exactly one institution.
INTAKE_CONTEXT = "enquiry_intake"

#: Generous for a form post and far below anything that would strain the worker.
MAX_BODY_BYTES = 64 * 1024

#: Per-IP. A hosted form provider posts from a small pool of addresses, so this
#: is deliberately loose: it is a backstop against a runaway loop, not the
#: primary control. The token is what actually authorises.
RATE = "120/minute"

#: One reply for every rejected credential.
_UNAUTHORISED = {"error": "unauthorised"}


class EnquiryIntakeRequest(BaseModel):
    """The flat contract. Everything is optional except being reachable."""

    model_config = {"extra": "allow"}  # forms send more than they are asked for

    first_name: str | None = None
    last_name: str | None = None
    name: str | None = Field(default=None, description="Used when not split")
    email: str | None = None
    phone: str | None = None
    notes: str | None = None
    #: Controls idempotency. A provider's own submission id is ideal.
    intake_key: str | None = None
    external_ref: str | None = None
    attribution: dict[str, Any] | None = None
    #: What the form actually asked and the person actually agreed to.
    consent_sms: bool = False
    consent_email: bool = False
    consent_wording: str | None = None
    #: Hosted form builders post their answers as a list. Typed loosely on
    #: purpose: the extractor below skips anything it does not recognise, and
    #: one malformed entry should not throw away a lead whose email arrived
    #: perfectly well at the top level.
    answers: list[Any] | None = None


def _json(payload: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code)


def _split_name(full: str | None) -> tuple[str | None, str | None]:
    parts = (full or "").strip().split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _from_answers(answers: list[Any] | None) -> dict[str, str]:
    """Pull email, phone and name out of a hosted form builder's answer list.

    Keyed on the answer's declared ``type``, never on the shape of the value. A
    phone number typed into a free-text question is not a phone number the
    person agreed to be reached on, and treating it as one would text somebody
    on the strength of a guess.
    """
    found: dict[str, str] = {}
    for answer in answers or []:
        if not isinstance(answer, dict):
            continue
        kind = str(answer.get("type") or "").lower()
        if kind == "email" and answer.get("email"):
            found.setdefault("email", str(answer["email"]))
        elif kind in {"phone_number", "phone"} and answer.get("phone_number"):
            found.setdefault("phone", str(answer["phone_number"]))
        elif kind in {"text", "short_text"}:
            ref = str((answer.get("field") or {}).get("ref") or "").lower()
            value = answer.get("text")
            if not value:
                continue
            if "name" in ref:
                found.setdefault("name", str(value))
    return found


def _signature_ok(secret: str, raw_body: bytes, supplied: str | None) -> bool:
    """Constant-time HMAC check over the exact bytes received."""
    if not supplied:
        return False
    candidate = supplied.strip()
    # Providers commonly prefix the algorithm.
    for prefix in ("sha256=", "sha256:"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix):]
            break
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, candidate)


@router.post("/intake/{token}")
@limiter.limit(RATE)
async def intake(
    request: Request,
    token: str = Path(..., min_length=16, max_length=200),
    x_signature: str | None = Header(default=None, alias="X-Signature"),
) -> JSONResponse:
    """Land a lead from an external form."""
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return _json({"error": "payload_too_large"}, 413)

    token_hash = hash_intake_token(token)

    # Resolved before any tenant context exists, so this read runs as the
    # intake context with no institution — the source row is what supplies one.
    async with get_system_db_session(INTAKE_CONTEXT, external_id="lookup") as session:
        source = (
            await session.execute(
                select(EnquiryIntakeSource).where(
                    EnquiryIntakeSource.token_hash == token_hash
                )
            )
        ).scalars().first()
        institution_id = str(source.institution_id) if source else None
        location_id = str(source.location_id) if source and source.location_id else None
        source_name = source.source_name if source else None
        defaults = dict(source.default_attribution or {}) if source else {}
        secret = source.signing_secret if source else None
        active = bool(source and source.is_active)
        source_id = str(source.id) if source else None

    # One answer for unknown, revoked and inactive alike: a different reply for
    # each would let someone with no credential map which ones exist.
    if not source_id or not active or not institution_id:
        logger.info("enquiry intake rejected: token_hash=%s", (token_hash or "")[:12])
        return _json(_UNAUTHORISED, 401)

    if secret and not _signature_ok(secret, raw, x_signature):
        logger.info("enquiry intake signature mismatch: source=%s", source_id)
        return _json(_UNAUTHORISED, 401)

    try:
        body = EnquiryIntakeRequest.model_validate_json(raw or b"{}")
    except ValidationError:
        return _json({"error": "invalid_payload"}, 422)

    extracted = _from_answers(body.answers)
    email = (body.email or extracted.get("email") or "").strip() or None
    phone = (body.phone or extracted.get("phone") or "").strip() or None
    first_name = body.first_name
    last_name = body.last_name
    if not first_name and not last_name:
        first_name, last_name = _split_name(body.name or extracted.get("name"))

    if not email and not phone:
        # Nothing downstream could ever act on this person.
        return _json({"error": "no_contact_method"}, 422)

    # Falls back to something stable so a provider that sends no id still gets
    # idempotency rather than a duplicate on every retry.
    intake_key = (body.intake_key or body.external_ref or "").strip() or (
        hashlib.sha256(f"{source_id}:{email or ''}:{phone or ''}".encode()).hexdigest()
    )

    attribution = {**defaults, **(body.attribution or {})}
    consent_channels = tuple(
        channel
        for channel, agreed in (("sms", body.consent_sms), ("email", body.consent_email))
        if agreed
    )

    async with get_system_db_session(
        INTAKE_CONTEXT, institution_id=institution_id, location_id=location_id,
        external_id=source_id,
    ) as session:
        try:
            await intake_enquiry(
                session,
                institution_id=institution_id,
                location_id=location_id,
                intake_key=intake_key,
                source=source_name or "external_form",
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=phone,
                attribution=attribution or None,
                external_ref=body.external_ref,
                notes=body.notes,
                consent_channels=consent_channels,
                consent_wording=body.consent_wording,
            )
        except Exception:
            # Never echoed: the exception text can repeat the submitted values,
            # which are this person's contact details.
            logger.exception("enquiry intake failed source=%s", source_id)
            return _json({"error": "unavailable"}, 503)

        row = await session.get(EnquiryIntakeSource, source_id)
        if row is not None:
            row.last_used_at = datetime.now(timezone.utc)
        await session.flush()

    # Deliberately says nothing about whether this person was already known.
    return _json({"status": "received"}, 202)
