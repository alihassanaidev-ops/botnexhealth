"""Unit tests for the institution jurisdiction enum and propagation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from src.app.api.deps import get_current_admin
from src.app.api.routes.admin_institutions import InstitutionCreate, InstitutionUpdate
from src.app.main import app
from src.app.models.institution import DEFAULT_JURISDICTION, Jurisdiction
from src.app.models.user import User, UserRole
from src.app.services.audit import (
    AuditAction,
    AuditActor,
    AuditEntry,
    AuditOutcome,
    PostgresAuditRepository,
)


def test_jurisdiction_enum_covers_all_canadian_provinces() -> None:
    expected = {
        "CA-ON",
        "CA-BC",
        "CA-AB",
        "CA-QC",
        "CA-MB",
        "CA-SK",
        "CA-NS",
        "CA-NB",
        "CA-NL",
        "CA-PE",
        "CA-YT",
        "CA-NT",
        "CA-NU",
    }
    assert {j.value for j in Jurisdiction} == expected


def test_default_jurisdiction_is_ontario() -> None:
    assert DEFAULT_JURISDICTION is Jurisdiction.CA_ON
    assert DEFAULT_JURISDICTION.value == "CA-ON"


def test_institution_create_defaults_to_ontario() -> None:
    body = InstitutionCreate(
        name="Demo",
        slug="demo",
        email="owner@example.com",
    )
    assert body.jurisdiction is Jurisdiction.CA_ON


def test_institution_create_accepts_alternate_jurisdiction() -> None:
    body = InstitutionCreate(
        name="Demo",
        slug="demo",
        email="owner@example.com",
        jurisdiction="CA-QC",
    )
    assert body.jurisdiction is Jurisdiction.CA_QC


def test_institution_create_rejects_unknown_jurisdiction() -> None:
    with pytest.raises(ValueError):
        InstitutionCreate(
            name="Demo",
            slug="demo",
            email="owner@example.com",
            jurisdiction="US-CA",
        )


def test_institution_update_optional_jurisdiction() -> None:
    body = InstitutionUpdate(jurisdiction="CA-BC")
    assert body.jurisdiction is Jurisdiction.CA_BC

    blank = InstitutionUpdate()
    assert blank.jurisdiction is None


@pytest.mark.asyncio
async def test_postgres_audit_repository_injects_jurisdiction_from_institution() -> None:
    """When an entry has institution_id, the repo stamps jurisdiction in metadata."""
    captured: dict[str, object] = {}

    class StubSession:
        def add(self, audit_log: object) -> None:
            captured["audit_log"] = audit_log

        async def execute(self, _stmt: object) -> MagicMock:
            scalar_result = MagicMock()
            scalar_result.scalar_one_or_none.return_value = "CA-QC"
            return scalar_result

    class StubCtx:
        async def __aenter__(self) -> StubSession:
            return StubSession()

        async def __aexit__(self, *args: object) -> None:
            return None

    with patch("src.app.database.get_db_session", return_value=StubCtx()):
        repo = PostgresAuditRepository()
        entry = AuditEntry(
            actor=AuditActor.ADMIN,
            action=AuditAction.INSTITUTION_UPDATE,
            target_resource="institution:abc",
            outcome=AuditOutcome.SUCCESS,
            institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        )
        await repo.save(entry)

    audit_log = captured["audit_log"]
    assert audit_log.audit_metadata["jurisdiction"] == "CA-QC"


@pytest.mark.asyncio
async def test_postgres_audit_repository_skips_lookup_without_institution() -> None:
    """Entries without institution_id never trigger the jurisdiction lookup."""
    captured: dict[str, object] = {}
    execute_calls = 0

    class StubSession:
        def add(self, audit_log: object) -> None:
            captured["audit_log"] = audit_log

        async def execute(self, _stmt: object) -> MagicMock:
            nonlocal execute_calls
            execute_calls += 1
            return MagicMock()

    class StubCtx:
        async def __aenter__(self) -> StubSession:
            return StubSession()

        async def __aexit__(self, *args: object) -> None:
            return None

    with patch("src.app.database.get_db_session", return_value=StubCtx()):
        repo = PostgresAuditRepository()
        entry = AuditEntry(
            actor=AuditActor.API_CLIENT,
            action=AuditAction.LOGIN,
            target_resource="auth:login",
            outcome=AuditOutcome.SUCCESS,
            institution_id=None,
        )
        await repo.save(entry)

    audit_log = captured["audit_log"]
    assert "jurisdiction" not in audit_log.audit_metadata
    assert execute_calls == 0


@pytest.mark.asyncio
async def test_postgres_audit_repository_keeps_explicit_jurisdiction() -> None:
    """Caller-supplied jurisdiction in metadata short-circuits the DB lookup."""
    captured: dict[str, object] = {}
    execute_calls = 0

    class StubSession:
        def add(self, audit_log: object) -> None:
            captured["audit_log"] = audit_log

        async def execute(self, _stmt: object) -> MagicMock:
            nonlocal execute_calls
            execute_calls += 1
            return MagicMock()

    class StubCtx:
        async def __aenter__(self) -> StubSession:
            return StubSession()

        async def __aexit__(self, *args: object) -> None:
            return None

    with patch("src.app.database.get_db_session", return_value=StubCtx()):
        repo = PostgresAuditRepository()
        entry = AuditEntry(
            actor=AuditActor.ADMIN,
            action=AuditAction.INSTITUTION_CREATE,
            target_resource="institution:new",
            outcome=AuditOutcome.SUCCESS,
            institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            metadata={"jurisdiction": "CA-AB"},
        )
        await repo.save(entry)

    audit_log = captured["audit_log"]
    assert audit_log.audit_metadata["jurisdiction"] == "CA-AB"
    assert execute_calls == 0


@pytest.mark.asyncio
async def test_create_institution_passes_jurisdiction_to_service(async_client: AsyncClient) -> None:
    payload = {
        "name": "Quebec Dental",
        "slug": "quebec-dental",
        "email": "owner@example.com",
        "jurisdiction": "CA-QC",
    }
    mock_super_admin = User(
        id="99999999-9999-9999-9999-999999999999",
        email="super@example.com",
        role=UserRole.SUPER_ADMIN.value,
        institution_id=None,
        is_active=True,
    )
    app.dependency_overrides[get_current_admin] = lambda: mock_super_admin

    with patch("src.app.api.routes.admin_institutions.get_db_session") as mock_get_db, patch(
        "src.app.api.routes.admin_institutions.InstitutionService"
    ) as MockInstitutionService, patch(
        "src.app.api.routes.admin_institutions.UserInviteService"
    ) as MockUserInviteService:
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        existing_user_check = MagicMock()
        existing_user_check.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = existing_user_check

        from types import SimpleNamespace

        mock_service = AsyncMock()
        mock_service.get_by_slug.return_value = None
        mock_service.create.return_value = SimpleNamespace(
            id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            name=payload["name"],
            slug=payload["slug"],
            is_active=True,
            location_limit=1,
            jurisdiction="CA-QC",
            nexhealth_api_key_encrypted=None,
        )
        MockInstitutionService.return_value = mock_service

        invited_user = User(
            id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            email=payload["email"],
            role=UserRole.INSTITUTION_ADMIN.value,
            institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            is_active=True,
            invite_status="PENDING",
        )
        mock_invite_service = MagicMock()
        mock_invite_service.create_invited_user = AsyncMock(return_value=invited_user)
        MockUserInviteService.return_value = mock_invite_service
        MockUserInviteService.normalize_email.side_effect = lambda email: email.strip().lower()

        try:
            response = await async_client.post("/api/admin/institutions", json=payload)
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 201
    assert response.json()["jurisdiction"] == "CA-QC"
    mock_service.create.assert_called_once()
    assert mock_service.create.call_args.kwargs["jurisdiction"] == "CA-QC"


@pytest.mark.asyncio
async def test_create_institution_rejects_invalid_jurisdiction(async_client: AsyncClient) -> None:
    mock_super_admin = User(
        id="99999999-9999-9999-9999-999999999999",
        email="super@example.com",
        role=UserRole.SUPER_ADMIN.value,
        institution_id=None,
        is_active=True,
    )
    app.dependency_overrides[get_current_admin] = lambda: mock_super_admin

    try:
        response = await async_client.post(
            "/api/admin/institutions",
            json={
                "name": "Bad",
                "slug": "bad",
                "email": "owner@example.com",
                "jurisdiction": "US-CA",
            },
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 422
