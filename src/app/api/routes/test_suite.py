"""Test Suite — call an agent function directly, without Retell.

Why this exists
---------------
Verifying a change to ``lookup_patient`` used to mean placing a real Retell
call, or signing a payload by hand, or opening ECS and reading logs. None of
that tells you anything the function itself couldn't, and all of it is slow.

What it is *not* is a second copy of the handlers. There is one implementation
and one registry (``retell.functions._function_registry``); this is a second
door onto it. A parallel "testable version" would drift from the real one and
the drift would be silent, which is the opposite of a testing tool.

The only thing Retell actually contributes at call time is context: which agent
is speaking, so the handler knows which clinic it is talking about. That lives
in a ContextVar, so setting it here is enough to run any handler exactly as
production runs it.

Safety
------
Six of the fifteen functions write into a live practice's diary and one returns
PHI, so the posture is deliberately closed:

* **Not mounted in production.** ``settings.test_suite_enabled`` requires a key
  *and* a non-production environment. There is no single flag that exposes it.
* **Keyed.** Constant-time comparison, and an unset key means the router does
  not exist rather than being present and open.
* **Read-only by default.** A mutating function is refused unless the deployment
  allows writes *and* the caller opts in per request.
* **Audited.** Every invocation is recorded with the resolved tenant.
* **Rate limited**, so a loop in a test script cannot hammer a PMS.
"""

from __future__ import annotations

import hmac
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.app.api.rate_limit import limiter
from src.app.config import settings
from src.app.database import get_system_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.retell import handlers as _handlers  # noqa: F401 — registers functions
from src.app.retell.functions import (
    _call_context_var,
    _function_registry,
)
from src.app.retell.idempotency import IDEMPOTENT_FUNCTIONS
from src.app.services.audit import log_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/test-suite", tags=["Test Suite"])

#: Generous enough for interactive use, low enough that a runaway loop in a
#: test script cannot flood a clinic's practice software.
RATE = "60/minute"

#: Functions that change something in a real practice. Derived from the
#: idempotency registry rather than re-listed, so a new mutating function is
#: covered the moment it is guarded there — one list to forget, not two.
MUTATING_FUNCTIONS: frozenset[str] = frozenset(IDEMPOTENT_FUNCTIONS)

#: One-line descriptions for discovery. A missing entry is not an error; the
#: handler docstring is used instead.
_HINTS: dict[str, str] = {
    "lookup_patient": "Verify a patient. Needs name + DOB + phone or email.",
    "find_appointment_slots": "Live availability. provider_id, appointment_type_id, start_date, days.",
    "list_providers": "Providers bookable at this location.",
    "list_appointment_types": "Appointment types, with durations where the PMS supplies them.",
    "list_operatories": "Operatories / chairs.",
    "list_locations": "The location this agent is bound to.",
    "get_location_details": "Address, phone and hours for the bound location.",
    "list_insurance_plans": "Insurance answers the agent reads out.",
    "list_transfer_numbers": "Numbers the agent may transfer to.",
    "book_appointment": "WRITES. Books into the practice's diary.",
    "cancel_appointment": "WRITES. Cancels a real appointment.",
    "confirm_appointment": "WRITES. Marks an appointment confirmed.",
    "create_patient": "WRITES. Creates a patient record.",
    "reschedule_appointment": "WRITES. Moves a real appointment.",
    "reschedule_appointment_v2": "WRITES. Moves a real appointment.",
}


# ── Auth ──────────────────────────────────────────────────────────────


async def require_test_suite_key(request: Request) -> str:
    """Authorise on ``X-Test-Suite-Key``.

    Compared in constant time. The same answer is given for a missing and a
    wrong key so the endpoint cannot be used to probe whether a key is close.
    """
    configured = settings.test_suite_api_key
    if not configured or settings.is_production:
        # Should be unreachable — the router is not mounted — but a route that
        # can serve PHI does not lean on its caller having wired it correctly.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    supplied = request.headers.get("x-test-suite-key") or ""
    if not hmac.compare_digest(supplied, configured):
        logger.warning("Test Suite: rejected a request with a bad or missing key")
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid or missing X-Test-Suite-Key"
        )
    return supplied


_Auth = Depends(require_test_suite_key)


def _admin_session():
    """A session that can see every tenant.

    The point of this tool is to inspect any clinic, so it reads across tenants
    deliberately. A bare ``context_type="test_suite"`` matches no RLS policy and
    silently returns zero rows — the endpoint answers 200 with an empty list and
    looks like a data problem rather than a permissions one. ``app_rls_is_super_admin``
    requires context_type "user" *and* role SUPER_ADMIN, so both are set.

    Safe here only because the router is key-gated and never mounts in
    production; do not copy this into a tenant-facing route.
    """
    return get_system_db_session("user", role="SUPER_ADMIN")


# ── Schemas ───────────────────────────────────────────────────────────


class FunctionInfo(BaseModel):
    name: str
    mutating: bool = Field(description="Writes into the practice's own software")
    callable_now: bool = Field(
        description="False when this deployment refuses writes and the function mutates"
    )
    summary: str


class FunctionListResponse(BaseModel):
    environment: str
    writes_allowed: bool
    count: int
    functions: list[FunctionInfo]


class TargetResponse(BaseModel):
    """Which clinic a call would be routed to."""

    agent_id: str
    institution: str | None = None
    institution_slug: str | None = None
    location: str | None = None
    location_slug: str | None = None
    pms: str | None = None
    timezone: str | None = None
    resolved: bool


class CallRequest(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)
    #: Either is accepted. ``location`` is the slug and is the friendlier one —
    #: nobody wants to keep a Retell agent id to hand. Location slugs are unique
    #: only *within* an institution, so ``institution`` disambiguates when two
    #: practices both have a "main".
    location: str | None = None
    institution: str | None = None
    agent_id: str | None = None
    allow_writes: bool = Field(
        default=False,
        description="Required, in addition to the deployment setting, to run a mutating function",
    )


class CallResponse(BaseModel):
    function: str
    ok: bool
    mutating: bool
    duration_ms: int
    target: TargetResponse
    result: Any = None
    error: str | None = None


# ── Target resolution ─────────────────────────────────────────────────


async def _resolve_target(body: CallRequest) -> TargetResponse:
    """Find the agent id to run as, and describe the clinic behind it.

    Resolving up front means a mistyped slug fails with "unknown location"
    rather than surfacing later as a handler-level "could not resolve
    institution", which reads like a product bug when it is a typo.
    """
    if not body.location and not body.agent_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Supply either 'location' (slug) or 'agent_id' so the call can be routed to a clinic.",
        )

    async with _admin_session() as session:
        if body.location:
            # Slugs are unique per institution, not globally: two practices can
            # each have a "main". Picking the first match would silently test
            # the wrong clinic, so ambiguity is refused rather than guessed.
            matches = (
                (
                    await session.execute(
                        select(InstitutionLocation).where(
                            InstitutionLocation.slug == body.location
                        )
                    )
                )
                .scalars()
                .all()
            )
            owners = {
                str(loc.id): await session.get(Institution, loc.institution_id)
                for loc in matches
            }
            if body.institution:
                matches = [
                    loc
                    for loc in matches
                    if getattr(owners.get(str(loc.id)), "slug", None) == body.institution
                ]
            if len(matches) > 1:
                candidates = sorted(
                    getattr(owners.get(str(loc.id)), "slug", "?") or "?"
                    for loc in matches
                )
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"'{body.location}' exists in more than one institution "
                    f"({', '.join(candidates)}). Add \"institution\" to say which.",
                )
            location = matches[0] if matches else None
        else:
            location = (
                (
                    await session.execute(
                        select(InstitutionLocation).where(
                            InstitutionLocation.retell_agent_id == body.agent_id
                        )
                    )
                )
                .scalars()
                .first()
            )

        if location is None:
            if body.location:
                scope = f" in institution '{body.institution}'" if body.institution else ""
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    f"No location with slug '{body.location}'{scope}. "
                    "GET /api/v1/test-suite/targets lists what is available.",
                )
            # An unknown agent id is still runnable — the handler will fail the
            # same way production would, which is itself worth being able to test.
            return TargetResponse(agent_id=body.agent_id or "", resolved=False)

        agent_id = location.retell_agent_id or body.agent_id
        if not agent_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Location '{location.slug}' has no Retell agent bound, so there is "
                "nothing to route as. Pass an explicit agent_id to test anyway.",
            )
        institution = await session.get(Institution, location.institution_id)
        return TargetResponse(
            agent_id=agent_id,
            institution=getattr(institution, "name", None),
            institution_slug=getattr(institution, "slug", None),
            location=location.name,
            location_slug=location.slug,
            pms=getattr(institution, "pms_type", None),
            timezone=location.timezone,
            resolved=True,
        )


# ── Routes ────────────────────────────────────────────────────────────


@router.get("/health")
@limiter.limit(RATE)
async def health(request: Request, _: str = _Auth) -> dict[str, Any]:
    """Confirm the suite is reachable and say what it will and will not do."""
    return {
        "ok": True,
        "environment": settings.app_env,
        "functions": len(_function_registry),
        "writes_allowed": settings.test_suite_allow_writes,
    }


@router.get("/functions", response_model=FunctionListResponse)
@limiter.limit(RATE)
async def list_functions(request: Request, _: str = _Auth) -> FunctionListResponse:
    """Every function that can be called, and whether this deployment will."""
    writes = settings.test_suite_allow_writes
    functions = []
    for name in sorted(_function_registry):
        mutating = name in MUTATING_FUNCTIONS
        summary = _HINTS.get(name) or (
            (_function_registry[name].__doc__ or "").strip().split("\n")[0]
        )
        functions.append(
            FunctionInfo(
                name=name,
                mutating=mutating,
                callable_now=writes or not mutating,
                summary=summary or "—",
            )
        )
    return FunctionListResponse(
        environment=settings.app_env,
        writes_allowed=writes,
        count=len(functions),
        functions=functions,
    )


@router.get("/targets")
@limiter.limit(RATE)
async def list_targets(request: Request, _: str = _Auth) -> dict[str, Any]:
    """The clinics available to call against, so nobody has to guess a slug."""
    async with _admin_session() as session:
        locations = (
            (await session.execute(select(InstitutionLocation)))
            .scalars()
            .all()
        )
        institutions = {
            str(inst.id): inst
            for inst in (await session.execute(select(Institution))).scalars().all()
        }
    return {
        "count": len(locations),
        "targets": [
            {
                "location": loc.slug,
                "name": loc.name,
                "institution": getattr(
                    institutions.get(str(loc.institution_id)), "slug", None
                ),
                "pms": getattr(
                    institutions.get(str(loc.institution_id)), "pms_type", None
                ),
                "timezone": loc.timezone,
                "agent_bound": bool(loc.retell_agent_id),
            }
            for loc in sorted(locations, key=lambda row: row.slug or "")
        ],
    }


@router.post("/functions/{function_name}", response_model=CallResponse)
@limiter.limit(RATE)
async def call_function(
    request: Request,
    function_name: str,
    body: CallRequest,
    _: str = _Auth,
) -> CallResponse:
    """Run one agent function and return exactly what the agent would receive.

    The handler executes through the production registry with the production
    call context, so a green result here means the real path is green — the
    whole point of not building a parallel implementation.
    """
    handler = _function_registry.get(function_name)
    if handler is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No function '{function_name}'. "
            "GET /api/v1/test-suite/functions lists them.",
        )

    mutating = function_name in MUTATING_FUNCTIONS
    if mutating and not (settings.test_suite_allow_writes and body.allow_writes):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"'{function_name}' writes into the practice's own software. "
            "It needs TEST_SUITE_ALLOW_WRITES on the deployment and "
            '"allow_writes": true on the request.',
        )

    target = await _resolve_target(body)
    call_id = f"test-suite-{uuid.uuid4().hex[:12]}"

    token = _call_context_var.set(
        {
            "call_id": call_id,
            "agent_id": target.agent_id,
            "agent_id_source": "test_suite",
            "args": body.args,
        }
    )
    started = time.perf_counter()
    result: Any = None
    error: str | None = None
    try:
        result = await handler(body.args)
    except Exception as exc:  # noqa: BLE001 — the error IS the result here
        # A raising handler is a legitimate outcome to inspect, so it is
        # reported rather than turned into a 500. The type and message are what
        # you would otherwise dig out of CloudWatch.
        error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "Test Suite: %s raised %s", function_name, type(exc).__name__
        )
    finally:
        _call_context_var.reset(token)
        duration_ms = int((time.perf_counter() - started) * 1000)

    # A handler can also report failure in-band rather than by raising.
    ok = error is None and not (
        isinstance(result, dict) and result.get("error") is not None
    )

    await log_audit(
        actor=AuditActor.SYSTEM,
        action=AuditAction.SEARCH_PATIENTS if not mutating else AuditAction.BOOK_APPOINTMENT,
        target_resource=f"test_suite:{function_name}",
        outcome=AuditOutcome.SUCCESS if ok else AuditOutcome.FAILURE_EXTERNAL_API,
        metadata={
            "source": "test_suite",
            "function": function_name,
            "mutating": mutating,
            "call_id": call_id,
            "location_slug": target.location_slug,
            "duration_ms": duration_ms,
            "arg_keys": sorted(body.args),
        },
    )

    return CallResponse(
        function=function_name,
        ok=ok,
        mutating=mutating,
        duration_ms=duration_ms,
        target=target,
        result=result,
        error=error,
    )
