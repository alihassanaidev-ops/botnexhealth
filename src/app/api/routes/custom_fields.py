"""Custom field definition CRUD for institutions.

Allows institutions to define additional fields on calls (or contacts) that
auto-populate from Retell webhook data via retell_source / retell_source_key.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, field_validator

from src.app.api.deps import get_current_active_user
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.custom_field import EntityType, FieldType, RetellSource
from src.app.models.user import User
from src.app.services.custom_field_service import CustomFieldService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/custom-fields", tags=["Custom Fields"])

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


# ── Request / Response models ──────────────────────────────────────────────


class CreateFieldDefinitionRequest(BaseModel):
    field_name: str
    field_key: str
    field_type: str = FieldType.TEXT.value
    entity_type: str = EntityType.CALL.value
    is_phi: bool = False
    is_required: bool = False
    dropdown_options: list[str] | None = None
    retell_source: str | None = None
    retell_source_key: str | None = None
    display_order: int | None = None

    @field_validator("field_key")
    @classmethod
    def validate_field_key(cls, v: str) -> str:
        if not _KEY_RE.match(v):
            raise ValueError("field_key must match ^[a-z][a-z0-9_]*$")
        return v

    @field_validator("field_type")
    @classmethod
    def validate_field_type(cls, v: str) -> str:
        valid = {ft.value for ft in FieldType}
        if v not in valid:
            raise ValueError(f"field_type must be one of {valid}")
        return v

    @field_validator("retell_source")
    @classmethod
    def validate_retell_source(cls, v: str | None) -> str | None:
        if v is not None:
            valid = {rs.value for rs in RetellSource}
            if v not in valid:
                raise ValueError(f"retell_source must be one of {valid}")
        return v


class UpdateFieldDefinitionRequest(BaseModel):
    field_name: str | None = None
    field_type: str | None = None
    is_phi: bool | None = None
    is_required: bool | None = None
    dropdown_options: list[str] | None = None
    retell_source: str | None = None
    retell_source_key: str | None = None
    display_order: int | None = None


class FieldDefinitionResponse(BaseModel):
    id: str
    institution_id: str
    entity_type: str
    field_name: str
    field_key: str
    field_type: str
    is_phi: bool
    is_required: bool
    dropdown_options: list[str] | None
    retell_source: str | None
    retell_source_key: str | None
    display_order: int
    is_active: bool
    created_at: str


def _defn_to_response(d) -> FieldDefinitionResponse:
    return FieldDefinitionResponse(
        id=d.id,
        institution_id=d.institution_id,
        entity_type=d.entity_type,
        field_name=d.field_name,
        field_key=d.field_key,
        field_type=d.field_type,
        is_phi=d.is_phi,
        is_required=d.is_required,
        dropdown_options=d.dropdown_options,
        retell_source=d.retell_source,
        retell_source_key=d.retell_source_key,
        display_order=d.display_order,
        is_active=d.is_active,
        created_at=d.created_at.isoformat(),
    )


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.get("/definitions", response_model=list[FieldDefinitionResponse])
@limiter.limit(RATE_READ)
async def list_definitions(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    entity_type: str = Query(EntityType.CALL.value),
    include_inactive: bool = Query(False),
) -> list[FieldDefinitionResponse]:
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")

    async with get_db_session() as session:
        svc = CustomFieldService(session)
        definitions = await svc.list_definitions(
            current_user.institution_id, entity_type, include_inactive,
        )
        return [_defn_to_response(d) for d in definitions]


@router.post(
    "/definitions",
    response_model=FieldDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(RATE_WRITE)
async def create_definition(
    request: Request,
    body: CreateFieldDefinitionRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> FieldDefinitionResponse:
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")

    async with get_db_session() as session:
        svc = CustomFieldService(session)
        defn = await svc.create_definition(
            current_user.institution_id,
            field_name=body.field_name,
            field_key=body.field_key,
            field_type=body.field_type,
            entity_type=body.entity_type,
            is_phi=body.is_phi,
            is_required=body.is_required,
            dropdown_options=body.dropdown_options,
            retell_source=body.retell_source,
            retell_source_key=body.retell_source_key,
            display_order=body.display_order,
        )
        await session.commit()
        await session.refresh(defn)
        return _defn_to_response(defn)


@router.patch("/definitions/{definition_id}", response_model=FieldDefinitionResponse)
@limiter.limit(RATE_WRITE)
async def update_definition(
    request: Request,
    definition_id: str,
    body: UpdateFieldDefinitionRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> FieldDefinitionResponse:
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")

    async with get_db_session() as session:
        svc = CustomFieldService(session)
        defn = await svc.get_definition(current_user.institution_id, definition_id)
        if not defn:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Definition not found")

        updates = body.model_dump(exclude_unset=True)
        await svc.update_definition(defn, **updates)
        await session.commit()
        await session.refresh(defn)
        return _defn_to_response(defn)


@router.delete(
    "/definitions/{definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@limiter.limit(RATE_WRITE)
async def delete_definition(
    request: Request,
    definition_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    hard: bool = Query(False),
) -> None:
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")

    async with get_db_session() as session:
        svc = CustomFieldService(session)
        defn = await svc.get_definition(current_user.institution_id, definition_id)
        if not defn:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Definition not found")

        await svc.delete_definition(defn, hard_delete=hard)
        await session.commit()
