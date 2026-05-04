"""Idempotent functions must have a usable call_id — never silently bypass."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.app.retell.idempotency import IDEMPOTENT_FUNCTIONS, run_with_idempotency


@pytest.mark.parametrize("function_name", sorted(IDEMPOTENT_FUNCTIONS))
@pytest.mark.parametrize("call_id", ["", None, "unknown_call_id"])
@pytest.mark.asyncio
async def test_idempotent_function_rejects_missing_call_id(function_name, call_id):
    handler = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await run_with_idempotency(
            handler,
            function_name=function_name,
            call_id=call_id or "",
            args={"x": 1},
        )

    assert exc.value.status_code == 400
    assert function_name in exc.value.detail
    handler.assert_not_awaited()
