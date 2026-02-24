"""Service for custom field CRUD and Retell webhook value extraction."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.custom_field import (
    CustomFieldDefinition,
    CustomFieldValue,
    EntityType,
    RetellSource,
)

logger = logging.getLogger(__name__)

# Values to treat as absent / not collected
_SKIP_VALUES = frozenset({"None", "N/A", "n/a", "null", ""})


class CustomFieldService:
    """Manages custom field definitions and values."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Definition CRUD ────────────────────────────────────────────────

    async def list_definitions(
        self,
        tenant_id: str,
        entity_type: str = EntityType.CALL.value,
        include_inactive: bool = False,
    ) -> list[CustomFieldDefinition]:
        stmt = (
            select(CustomFieldDefinition)
            .where(
                CustomFieldDefinition.tenant_id == tenant_id,
                CustomFieldDefinition.entity_type == entity_type,
            )
            .order_by(CustomFieldDefinition.display_order)
        )
        if not include_inactive:
            stmt = stmt.where(CustomFieldDefinition.is_active.is_(True))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_definition(
        self, tenant_id: str, definition_id: str
    ) -> CustomFieldDefinition | None:
        result = await self.session.execute(
            select(CustomFieldDefinition).where(
                CustomFieldDefinition.id == definition_id,
                CustomFieldDefinition.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def create_definition(
        self,
        tenant_id: str,
        *,
        field_name: str,
        field_key: str,
        field_type: str = "text",
        entity_type: str = EntityType.CALL.value,
        is_phi: bool = False,
        is_required: bool = False,
        dropdown_options: list[str] | None = None,
        retell_source: str | None = None,
        retell_source_key: str | None = None,
        display_order: int | None = None,
    ) -> CustomFieldDefinition:
        if display_order is None:
            # Auto-assign: max existing + 1
            existing = await self.list_definitions(tenant_id, entity_type, include_inactive=True)
            display_order = max((d.display_order for d in existing), default=-1) + 1

        defn = CustomFieldDefinition(
            id=str(uuid4()),
            tenant_id=tenant_id,
            entity_type=entity_type,
            field_name=field_name,
            field_key=field_key,
            field_type=field_type,
            is_phi=is_phi,
            is_required=is_required,
            dropdown_options=dropdown_options,
            retell_source=retell_source,
            retell_source_key=retell_source_key,
            display_order=display_order,
        )
        self.session.add(defn)
        await self.session.flush()
        return defn

    async def update_definition(
        self, defn: CustomFieldDefinition, **updates: Any
    ) -> CustomFieldDefinition:
        allowed = {
            "field_name", "field_type", "is_phi", "is_required",
            "dropdown_options", "retell_source", "retell_source_key",
            "display_order", "is_active",
        }
        for key, value in updates.items():
            if key in allowed:
                setattr(defn, key, value)
        await self.session.flush()
        return defn

    async def delete_definition(
        self, defn: CustomFieldDefinition, hard_delete: bool = False,
    ) -> None:
        if hard_delete:
            await self.session.delete(defn)
        else:
            defn.is_active = False
        await self.session.flush()

    # ── Value retrieval ────────────────────────────────────────────────

    async def get_values_for_entity(
        self,
        tenant_id: str,
        entity_type: str,
        entity_id: str,
    ) -> list[tuple[CustomFieldDefinition, CustomFieldValue]]:
        """Return (definition, value) pairs for an entity, ordered by display_order."""
        stmt = (
            select(CustomFieldDefinition, CustomFieldValue)
            .join(
                CustomFieldValue,
                CustomFieldValue.field_definition_id == CustomFieldDefinition.id,
            )
            .where(
                CustomFieldDefinition.tenant_id == tenant_id,
                CustomFieldDefinition.entity_type == entity_type,
                CustomFieldDefinition.is_active.is_(True),
                CustomFieldValue.entity_id == entity_id,
                CustomFieldValue.entity_type == entity_type,
            )
            .order_by(CustomFieldDefinition.display_order)
        )
        result = await self.session.execute(stmt)
        return list(result.tuples().all())

    # ── Webhook extraction ─────────────────────────────────────────────

    async def extract_and_save_from_webhook(
        self,
        tenant_id: str,
        call_id: str,
        custom_analysis_data: dict[str, Any],
        collected_dynamic_variables: dict[str, Any],
    ) -> int:
        """Extract custom field values from Retell webhook dicts and upsert.

        Returns the number of values saved.
        """
        source_dicts = {
            RetellSource.CUSTOM_ANALYSIS_DATA.value: custom_analysis_data,
            RetellSource.COLLECTED_DYNAMIC_VARIABLES.value: collected_dynamic_variables,
        }

        definitions = await self.list_definitions(tenant_id, EntityType.CALL.value)
        # Only those with a retell_source_key mapping
        mapped = [d for d in definitions if d.retell_source_key]

        if not mapped:
            return 0

        # Pre-load existing values for this call so we can upsert
        existing_stmt = select(CustomFieldValue).where(
            CustomFieldValue.tenant_id == tenant_id,
            CustomFieldValue.entity_type == EntityType.CALL.value,
            CustomFieldValue.entity_id == call_id,
        )
        existing_rows = (await self.session.execute(existing_stmt)).scalars().all()
        existing_by_defn = {row.field_definition_id: row for row in existing_rows}

        saved = 0
        for defn in mapped:
            # Look up value from the configured source, fallback to the other
            primary = source_dicts.get(defn.retell_source or "", {})
            fallback_key = (
                RetellSource.COLLECTED_DYNAMIC_VARIABLES.value
                if defn.retell_source == RetellSource.CUSTOM_ANALYSIS_DATA.value
                else RetellSource.CUSTOM_ANALYSIS_DATA.value
            )
            fallback = source_dicts.get(fallback_key, {})

            raw = primary.get(defn.retell_source_key)
            if raw is None:
                raw = fallback.get(defn.retell_source_key)
            if raw is None:
                continue

            str_val = str(raw).strip()
            if str_val in _SKIP_VALUES:
                continue

            # Upsert
            cfv = existing_by_defn.get(defn.id)
            if cfv:
                cfv.set_value(str_val, is_phi=defn.is_phi)
            else:
                cfv = CustomFieldValue(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    field_definition_id=defn.id,
                    entity_type=EntityType.CALL.value,
                    entity_id=call_id,
                )
                cfv.set_value(str_val, is_phi=defn.is_phi)
                self.session.add(cfv)

            saved += 1

        if saved:
            await self.session.flush()
            logger.info(
                "Saved %d custom field values for call %s (tenant %s)",
                saved, call_id, tenant_id,
            )

        return saved
