"""Shared pagination helpers for SQLAlchemy selects."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


T = TypeVar("T")


@dataclass(frozen=True)
class PaginationQuery(Generic[T]):
    session: AsyncSession
    statement: Select[tuple[T]]


class PageResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int
    pages: int


async def paginate(
    query: PaginationQuery[T], *, page: int, size: int
) -> tuple[Sequence[T], int]:
    count_stmt = select(func.count()).select_from(
        query.statement.order_by(None).subquery()
    )
    total = (await query.session.execute(count_stmt)).scalar() or 0
    result = await query.session.execute(
        query.statement.offset((page - 1) * size).limit(size)
    )
    return result.scalars().all(), int(total)


def page_count(total: int, size: int) -> int:
    return math.ceil(total / size) if size > 0 else 0
