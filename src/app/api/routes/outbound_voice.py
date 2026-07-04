"""FastAPI routes for Outbound Voice (Plan 03 / V-8).

Two resources over the V-4 data model:
  * ``/outbound-voice/profiles`` — per-location outbound config CRUD (which Retell
    agent + from-number a location dials with). Institution-scoped; writes gated to
    institution/location admins. At most one ACTIVE profile per location.
  * ``/outbound-voice/attempts`` — read-only drill-down over placed call attempts
    (status, dial outcome, masked numbers) for a run/location. Any institution user.

Tenant isolation is enforced by RLS (institution_id/location_id on both tables);
routes add an explicit institution filter as defense-in-depth and never trust an
institution id from the request body.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.app.api.deps import (
    get_current_institution_or_location_admin,
    get_current_institution_or_location_user,
)
from src.app.database import get_db_session
from src.app.models.outbound_voice import OutboundVoiceProfile, WorkflowVoiceAttempt
from src.app.models.user import User
from src.app.services.automation.voice_attempt_recorder import list_voice_attempts

router = APIRouter(prefix="/outbound-voice", tags=["Outbound Voice"])

# Managing a location's outbound-voice config is an admin action; the read-only
# attempt drill-down is visible to any institution-scoped user (incl. STAFF).
_Admin = Annotated[User, Depends(get_current_institution_or_location_admin)]
_Reader = Annotated[User, Depends(get_current_institution_or_location_user)]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class OutboundVoiceProfileCreate(BaseModel):
    location_id: str
    retell_agent_id: str | None = Field(None, max_length=255)
    retell_from_number: str | None = Field(None, max_length=32)
    retell_llm_id: str | None = Field(None, max_length=255)
    display_name: str | None = Field(None, max_length=120)
    is_active: bool = True
    config: dict[str, Any] | None = None


class OutboundVoiceProfileUpdate(BaseModel):
    retell_agent_id: str | None = Field(None, max_length=255)
    retell_from_number: str | None = Field(None, max_length=32)
    retell_llm_id: str | None = Field(None, max_length=255)
    display_name: str | None = Field(None, max_length=120)
    is_active: bool | None = None
    config: dict[str, Any] | None = None


class OutboundVoiceProfileResponse(BaseModel):
    id: str
    institution_id: str
    location_id: str
    retell_agent_id: str | None
    retell_from_number: str | None
    retell_llm_id: str | None
    display_name: str | None
    is_active: bool
    config: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, p: Any) -> "OutboundVoiceProfileResponse":
        return cls(
            id=str(p.id),
            institution_id=str(p.institution_id),
            location_id=str(p.location_id),
            retell_agent_id=p.retell_agent_id,
            retell_from_number=p.retell_from_number,
            retell_llm_id=p.retell_llm_id,
            display_name=p.display_name,
            is_active=p.is_active,
            config=p.config,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )


class WorkflowVoiceAttemptResponse(BaseModel):
    id: str
    workflow_run_id: str
    step_execution_id: str | None
    step_id: str
    attempt_number: int
    retell_call_id: str | None
    from_number_masked: str | None
    to_number_masked: str | None
    status: str
    dial_outcome: str | None
    disconnection_reason: str | None
    error_message: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, a: Any) -> "WorkflowVoiceAttemptResponse":
        return cls(
            id=str(a.id),
            workflow_run_id=str(a.workflow_run_id),
            step_execution_id=str(a.step_execution_id) if a.step_execution_id else None,
            step_id=a.step_id,
            attempt_number=a.attempt_number,
            retell_call_id=a.retell_call_id,
            from_number_masked=a.from_number_masked,
            to_number_masked=a.to_number_masked,
            status=a.status,
            dial_outcome=a.dial_outcome,
            disconnection_reason=a.disconnection_reason,
            error_message=a.error_message,
            created_at=a.created_at,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _institution_id(user: User) -> str:
    if not user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No institution context"
        )
    return str(user.institution_id)


_ACTIVE_PROFILE_CONFLICT = "An active outbound-voice profile already exists for this location"


# ---------------------------------------------------------------------------
# Outbound-voice profiles CRUD
# ---------------------------------------------------------------------------


@router.post("/profiles", response_model=OutboundVoiceProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_profile(
    data: OutboundVoiceProfileCreate,
    current_user: _Admin,
) -> OutboundVoiceProfileResponse:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        profile = OutboundVoiceProfile(
            institution_id=inst_id,
            location_id=data.location_id,
            retell_agent_id=data.retell_agent_id,
            retell_from_number=data.retell_from_number,
            retell_llm_id=data.retell_llm_id,
            display_name=data.display_name,
            is_active=data.is_active,
            config=data.config,
            created_by_user_id=str(current_user.id),
        )
        session.add(profile)
        try:
            await session.flush()
        except IntegrityError:
            # Partial-unique index: at most one active profile per location.
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_ACTIVE_PROFILE_CONFLICT)
        await session.refresh(profile)
        return OutboundVoiceProfileResponse.from_model(profile)


@router.get("/profiles", response_model=list[OutboundVoiceProfileResponse])
async def list_profiles(
    current_user: _Admin,
    location_id: str | None = Query(None),
    is_active: bool | None = Query(None),
) -> list[OutboundVoiceProfileResponse]:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        query = select(OutboundVoiceProfile).where(
            OutboundVoiceProfile.institution_id == inst_id
        )
        if location_id is not None:
            query = query.where(OutboundVoiceProfile.location_id == location_id)
        if is_active is not None:
            query = query.where(OutboundVoiceProfile.is_active.is_(is_active))
        query = query.order_by(OutboundVoiceProfile.created_at.desc())
        profiles = (await session.execute(query)).scalars().all()
    return [OutboundVoiceProfileResponse.from_model(p) for p in profiles]


async def _get_owned_profile_or_404(session, profile_id: str, inst_id: str) -> OutboundVoiceProfile:
    profile = await session.get(OutboundVoiceProfile, profile_id)
    if profile is None or str(profile.institution_id) != inst_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voice profile not found")
    return profile


@router.get("/profiles/{profile_id}", response_model=OutboundVoiceProfileResponse)
async def get_profile(
    profile_id: str,
    current_user: _Admin,
) -> OutboundVoiceProfileResponse:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        profile = await _get_owned_profile_or_404(session, profile_id, inst_id)
        return OutboundVoiceProfileResponse.from_model(profile)


@router.patch("/profiles/{profile_id}", response_model=OutboundVoiceProfileResponse)
async def update_profile(
    profile_id: str,
    data: OutboundVoiceProfileUpdate,
    current_user: _Admin,
) -> OutboundVoiceProfileResponse:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        profile = await _get_owned_profile_or_404(session, profile_id, inst_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(profile, field, value)
        try:
            await session.flush()
        except IntegrityError:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_ACTIVE_PROFILE_CONFLICT)
        await session.refresh(profile)
        return OutboundVoiceProfileResponse.from_model(profile)


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    profile_id: str,
    current_user: _Admin,
) -> None:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        profile = await _get_owned_profile_or_404(session, profile_id, inst_id)
        await session.delete(profile)


# ---------------------------------------------------------------------------
# Voice-attempt drill-down (read-only)
# ---------------------------------------------------------------------------


@router.get("/attempts", response_model=list[WorkflowVoiceAttemptResponse])
async def list_attempts(
    current_user: _Reader,
    workflow_run_id: str | None = Query(None),
    location_id: str | None = Query(None),
    call_status: str | None = Query(None, description="Filter by attempt lifecycle status"),
    limit: int = Query(50, ge=1, le=500),
) -> list[WorkflowVoiceAttemptResponse]:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        attempts = await list_voice_attempts(
            session,
            inst_id,
            workflow_run_id=workflow_run_id,
            location_id=location_id,
            status=call_status,
            limit=limit,
        )
    return [WorkflowVoiceAttemptResponse.from_model(a) for a in attempts]
