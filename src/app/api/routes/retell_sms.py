"""Tenant-scoped Retell SMS chat-profile management."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.app.api.deps import get_current_active_user, get_current_admin
from src.app.api.deps_scope import bind_active_location
from src.app.database import get_db_session
from src.app.models.institution_location import InstitutionLocation
from src.app.models.retell_sms import RetellSmsChatProfile
from src.app.models.user import User, UserRole

router = APIRouter(prefix="/retell-sms", tags=["Retell SMS"])

_SuperAdmin = Annotated[User, Depends(get_current_admin)]
_Reader = Annotated[User, Depends(get_current_active_user)]


class RetellSmsChatProfileCreate(BaseModel):
    location_id: str
    retell_agent_id: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=120)
    allowed_tools: list[str] = Field(default_factory=list, max_length=30)
    is_active: bool = True
    config: dict[str, Any] | None = None


class RetellSmsChatProfileUpdate(BaseModel):
    retell_agent_id: str | None = Field(default=None, min_length=1, max_length=255)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    allowed_tools: list[str] | None = Field(default=None, max_length=30)
    is_active: bool | None = None
    config: dict[str, Any] | None = None

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> "RetellSmsChatProfileUpdate":
        for field in ("retell_agent_id", "display_name"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class RetellSmsChatProfileResponse(BaseModel):
    id: str
    institution_id: str
    location_id: str
    retell_agent_id: str | None
    agent_version: int | None
    display_name: str
    purpose: str | None
    allowed_tools: list[str]
    is_active: bool
    config: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(
        cls, profile: RetellSmsChatProfile, *, include_sensitive: bool
    ) -> "RetellSmsChatProfileResponse":
        return cls(
            id=str(profile.id),
            institution_id=str(profile.institution_id),
            location_id=str(profile.location_id),
            retell_agent_id=profile.retell_agent_id if include_sensitive else None,
            agent_version=profile.agent_version if include_sensitive else None,
            display_name=profile.display_name,
            purpose=profile.purpose,
            allowed_tools=profile.allowed_tools if include_sensitive else [],
            is_active=profile.is_active,
            config=profile.config if include_sensitive else None,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )


def _institution_id(user: User) -> str:
    if not user.institution_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No institution context")
    return str(user.institution_id)


def _profile_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="An active profile already uses this Retell Chat Agent",
    )


@router.post(
    "/profiles",
    response_model=RetellSmsChatProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_profile(
    data: RetellSmsChatProfileCreate, current_user: _SuperAdmin
) -> RetellSmsChatProfileResponse:
    async with get_db_session() as session:
        location = await session.get(InstitutionLocation, data.location_id)
        if location is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
        profile = RetellSmsChatProfile(
            institution_id=str(location.institution_id),
            location_id=str(location.id),
            retell_agent_id=data.retell_agent_id.strip(),
            agent_version=None,
            display_name=data.display_name.strip(),
            purpose=None,
            allowed_tools=sorted(set(data.allowed_tools)),
            is_active=data.is_active,
            config=data.config,
            created_by_user_id=str(current_user.id),
        )
        session.add(profile)
        try:
            await session.flush()
        except IntegrityError:
            raise _profile_conflict()
        await session.refresh(profile)
        return RetellSmsChatProfileResponse.from_model(profile, include_sensitive=True)


@router.get("/profiles", response_model=list[RetellSmsChatProfileResponse])
async def list_profiles(
    current_user: _Reader,
    location_id: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
) -> list[RetellSmsChatProfileResponse]:
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN.value
    if not is_super_admin and current_user.role not in (
        UserRole.INSTITUTION_ADMIN.value,
        UserRole.LOCATION_ADMIN.value,
        UserRole.STAFF.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires a platform or institution user",
        )

    if is_super_admin and location_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="location_id is required for platform administrators",
        )

    if (
        not is_super_admin
        and current_user.location_id is not None
        and location_id is not None
        and location_id not in current_user.allowed_location_ids
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Location is outside the user's assigned scope",
        )

    effective_location_id = (
        location_id
        if is_super_admin or current_user.location_id is None
        else (location_id or str(current_user.location_id))
    )
    if not is_super_admin and current_user.location_id is not None:
        bind_active_location(current_user, str(effective_location_id))
    async with get_db_session() as session:
        query = select(RetellSmsChatProfile)
        if not is_super_admin:
            query = query.where(
                RetellSmsChatProfile.institution_id == _institution_id(current_user)
            )
        if effective_location_id is not None:
            query = query.where(RetellSmsChatProfile.location_id == effective_location_id)
        if is_active is not None:
            query = query.where(RetellSmsChatProfile.is_active.is_(is_active))
        query = query.order_by(RetellSmsChatProfile.display_name, RetellSmsChatProfile.created_at)
        profiles = (await session.execute(query)).scalars().all()
    return [
        RetellSmsChatProfileResponse.from_model(profile, include_sensitive=is_super_admin)
        for profile in profiles
    ]


@router.patch("/profiles/{profile_id}", response_model=RetellSmsChatProfileResponse)
async def update_profile(
    profile_id: str,
    data: RetellSmsChatProfileUpdate,
    current_user: _SuperAdmin,
) -> RetellSmsChatProfileResponse:
    async with get_db_session() as session:
        profile = await session.get(RetellSmsChatProfile, profile_id)
        if profile is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
        for field, value in data.model_dump(exclude_unset=True).items():
            if field == "retell_agent_id" and value is not None:
                value = value.strip()
            elif field == "display_name" and value is not None:
                value = value.strip()
            elif field == "allowed_tools" and value is not None:
                value = sorted(set(value))
            setattr(profile, field, value)
        # Purpose and version pinning are legacy profile fields. SMS profiles
        # always follow the Chat Agent's latest Retell version.
        profile.purpose = None
        profile.agent_version = None
        try:
            await session.flush()
        except IntegrityError:
            raise _profile_conflict()
        await session.refresh(profile)
        return RetellSmsChatProfileResponse.from_model(profile, include_sensitive=True)


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: str, current_user: _SuperAdmin) -> None:
    async with get_db_session() as session:
        profile = await session.get(RetellSmsChatProfile, profile_id)
        if profile is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
        # Keep historic sessions referentially intact; deletion is only allowed
        # when the profile has never been used. Operators can deactivate otherwise.
        try:
            await session.delete(profile)
            await session.flush()
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Profile is in use; deactivate it instead",
            )
