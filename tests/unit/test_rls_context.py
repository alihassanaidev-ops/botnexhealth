from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.database import (
    RlsContext,
    apply_rls_context,
    current_rls_context,
    use_rls_context,
)


@pytest.mark.asyncio
async def test_apply_rls_context_sets_all_postgres_settings() -> None:
    session = AsyncMock()
    context = RlsContext(
        context_type="user",
        user_id="11111111-1111-1111-1111-111111111111",
        role="LOCATION_ADMIN",
        institution_id="22222222-2222-2222-2222-222222222222",
        location_id="33333333-3333-3333-3333-333333333333",
        external_id="retell-call-1",
    )

    await apply_rls_context(session, context)

    # Now batched into a single round-trip: one execute() with a dict of all
    # six bind params. Asserts both the SQL shape and the bound values.
    assert session.execute.await_count == 1
    call = session.execute.await_args_list[0]
    sql = str(call.args[0])
    for key in (
        "set_config('app.context_type', :context_type, false)",
        "set_config('app.user_id', :user_id, false)",
        "set_config('app.role', :role, false)",
        "set_config('app.institution_id', :institution_id, false)",
        "set_config('app.location_id', :location_id, false)",
        "set_config('app.external_id', :external_id, false)",
        "set_config('app.group_id', :group_id, false)",
    ):
        assert key in sql
    assert call.args[1] == {
        "context_type": "user",
        "user_id": "11111111-1111-1111-1111-111111111111",
        "role": "LOCATION_ADMIN",
        "institution_id": "22222222-2222-2222-2222-222222222222",
        "location_id": "33333333-3333-3333-3333-333333333333",
        "external_id": "retell-call-1",
        "group_id": "",
    }


@pytest.mark.asyncio
async def test_apply_rls_context_clears_missing_values_to_empty_strings() -> None:
    session = AsyncMock()

    await apply_rls_context(session, None)

    assert session.execute.await_count == 1
    call = session.execute.await_args_list[0]
    assert call.args[1] == {
        "context_type": "",
        "user_id": "",
        "role": "",
        "institution_id": "",
        "location_id": "",
        "external_id": "",
        "group_id": "",
    }


def test_rls_context_for_user_maps_role_and_scope() -> None:
    user = SimpleNamespace(
        id="user-1",
        role="STAFF",
        institution_id="inst-1",
        location_id="loc-1",
    )

    context = RlsContext.for_user(user)

    assert context == RlsContext(
        context_type="user",
        user_id="user-1",
        role="STAFF",
        institution_id="inst-1",
        location_id="loc-1",
    )


def test_use_rls_context_resets_previous_context() -> None:
    assert current_rls_context() is None
    with use_rls_context(RlsContext.system("audit", institution_id="inst-1")):
        assert current_rls_context() == RlsContext.system(
            "audit",
            institution_id="inst-1",
        )
    assert current_rls_context() is None
