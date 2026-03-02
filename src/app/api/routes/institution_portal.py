"""
Institution portal routes.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.app.api.deps import get_current_active_user
from src.app.api.models import InstitutionResponse, AuditLogResponse, AuditLogPaginatedResponse
from src.app.database import get_db_session
from src.app.models.user import User, UserRole
from src.app.models.audit_log import AuditLog
from src.app.services.institution_service import InstitutionService

router = APIRouter(prefix="/institution", tags=["Institution Portal"])


@router.get("/me", response_model=InstitutionResponse)
async def get_my_institution_config(
    current_user: Annotated[User, Depends(get_current_active_user)]
):
    """
    Get configuration for the current user's institution.

    Returns masked credentials (booleans only).
    """
    # Verify user is an INSTITUTION user or at least has an institution_id
    if not current_user.institution_id:
        # Admins or users without institution_id cannot use this endpoint
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with an institution"
        )

    async with get_db_session() as session:
        service = InstitutionService(session)
        institution = await service.get_by_id(current_user.institution_id)

        if not institution:
            # Should not happen unless referential integrity is broken
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Institution not found"
            )

        from src.app.models.institution_location import InstitutionLocation
        from sqlalchemy import select
        retell_result = await session.execute(
            select(InstitutionLocation.institution_id)
            .where(InstitutionLocation.institution_id == institution.id)
            .where(InstitutionLocation.retell_agent_id.is_not(None))
            .where(InstitutionLocation.retell_agent_id != "")
            .limit(1)
        )
        has_retell = retell_result.scalar_one_or_none() is not None

        return InstitutionResponse.from_institution(institution, current_user, has_retell_secret=has_retell)


@router.get("/audit-logs", response_model=AuditLogPaginatedResponse)
async def get_my_audit_logs(
    current_user: Annotated[User, Depends(get_current_active_user)],
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100)
):
    """
    Get audit logs for the current user's institution.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with an institution"
        )

    from sqlalchemy import select, func

    async with get_db_session() as session:
        # Get total count
        count_result = await session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.institution_id == current_user.institution_id)
        )
        total = count_result.scalar() or 0

        # Get paginated data
        result = await session.execute(
            select(AuditLog)
            .where(AuditLog.institution_id == current_user.institution_id)
            .order_by(AuditLog.timestamp.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        items = result.scalars().all()

        import math
        pages = math.ceil(total / size) if size > 0 else 0

        return AuditLogPaginatedResponse(
            items=items,
            total=total,
            page=page,
            size=size,
            pages=pages
        )
