"""
API dependencies for authentication and authorization.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select

from src.app.database import (
    RlsContext,
    get_db_session,
    set_current_rls_context,
    use_rls_context,
)
from src.app.models.user import User, UserRole
from src.app.services.auth import AuthService
from src.app.services.refresh_token_service import RefreshTokenService

# OAuth2 scheme for Swagger UI
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    request: Request = None,  # type: ignore[assignment]
) -> User:
    """Validate JWT token and return current user."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = AuthService.decode_access_token(token)
        user_id = payload.get("sub")
        jti = payload.get("jti")
    except JWTError:
        raise credentials_exception

    if not user_id or not jti:
        raise credentials_exception

    try:
        if await RefreshTokenService.is_access_token_jti_revoked(jti):
            raise credentials_exception
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication session store is unavailable",
        ) from exc

    with use_rls_context(RlsContext.system("auth", user_id=str(user_id))):
        async with get_db_session() as session:
            result = await session.execute(
                select(User).where(
                    User.id == user_id,
                    User.deleted_at.is_(None),
                )
            )
            user = result.scalar_one_or_none()

            if user is None:
                raise credentials_exception

    if current_user_requires_mfa(user) and payload.get("mfa") is not True:
        raise credentials_exception

    set_current_rls_context(RlsContext.for_user(user))
    if request is not None:
        request.state.rls_context = RlsContext.for_user(user)
    return user


def current_user_requires_mfa(user: User) -> bool:
    """Return True for interactive roles that can access app data."""
    return user.role in (
        UserRole.SUPER_ADMIN.value,
        UserRole.INSTITUTION_ADMIN.value,
        UserRole.LOCATION_ADMIN.value,
        UserRole.STAFF.value,
        # GROUP_ADMIN is an interactive cross-practice oversight login; it must
        # enrol/verify MFA like every other human role (no exemption).
        UserRole.GROUP_ADMIN.value,
    )


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)]
) -> User:
    """
    Ensure user is active.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )
    return current_user


async def get_current_admin(
    current_user: Annotated[User, Depends(get_current_active_user)]
) -> User:
    """
    Backwards-compatible alias for super admin checks.
    """
    if current_user.role != UserRole.SUPER_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges"
        )
    return current_user


async def get_current_super_admin(
    current_user: Annotated[User, Depends(get_current_active_user)]
) -> User:
    """
    Ensure user is a SUPER_ADMIN.
    """
    if current_user.role != UserRole.SUPER_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires SUPER_ADMIN role",
        )
    return current_user


async def get_current_institution_user(
    current_user: Annotated[User, Depends(get_current_active_user)]
) -> User:
    """
    Backwards-compatible alias for institution admin checks.
    """
    if current_user.role != UserRole.INSTITUTION_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires INSTITUTION_ADMIN role"
        )
    return current_user


async def get_current_institution_admin(
    current_user: Annotated[User, Depends(get_current_active_user)]
) -> User:
    """
    Ensure user has INSTITUTION_ADMIN role.
    """
    if current_user.role != UserRole.INSTITUTION_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires INSTITUTION_ADMIN role",
        )
    return current_user


async def get_current_institution_or_super_admin(
    current_user: Annotated[User, Depends(get_current_active_user)]
) -> User:
    """
    Ensure user has INSTITUTION_ADMIN role, or SUPER_ADMIN acting on a named
    institution.

    Used by the per-institution email surfaces (campaign templates, sending
    identity), where a platform administrator legitimately administers any
    tenant. A super admin carries no ``institution_id`` of their own, so routes
    behind this boundary must resolve the target through
    :func:`resolve_target_institution` rather than reading it off the user.
    """
    if current_user.role not in (
        UserRole.INSTITUTION_ADMIN.value,
        UserRole.SUPER_ADMIN.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires INSTITUTION_ADMIN or SUPER_ADMIN role",
        )
    return current_user


async def get_current_institution_location_or_super_admin(
    current_user: Annotated[User, Depends(get_current_active_user)],
    location_id: str | None = None,
) -> User:
    """Allow platform, institution, or location admins for location-owned setup.

    Location admins are pinned here, before a handler opens a session. This is
    intentionally a separate boundary from sending-domain administration:
    receiving controls are operational settings for one clinic, while DNS and
    sender verification remain institution/platform responsibilities.
    """
    if current_user.role not in (
        UserRole.SUPER_ADMIN.value,
        UserRole.INSTITUTION_ADMIN.value,
        UserRole.LOCATION_ADMIN.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires SUPER_ADMIN, INSTITUTION_ADMIN, or LOCATION_ADMIN role",
        )
    if current_user.role == UserRole.LOCATION_ADMIN.value:
        if not current_user.location_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Location-scoped account is missing location assignment",
            )
        if location_id and str(location_id) != str(current_user.location_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot administer another location",
            )
    return current_user


def resolve_target_institution(user: User, institution_id: str | None) -> str:
    """Which institution this request acts on.

    A tenant admin is pinned to their own institution: an explicit id is
    accepted only when it matches, so a stray query parameter can never widen
    the request. A super admin has no institution of their own and must name
    one — that keeps a platform-wide account from silently acting on nothing,
    or on whichever tenant happened to be first.
    """
    if user.role == UserRole.SUPER_ADMIN.value:
        if not institution_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="institution_id is required for platform administrators",
            )
        return institution_id

    if not user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No institution"
        )
    if institution_id and institution_id != str(user.institution_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot act on another institution",
        )
    return str(user.institution_id)


async def get_current_location_admin(
    current_user: Annotated[User, Depends(get_current_active_user)]
) -> User:
    """
    Ensure user has LOCATION_ADMIN role.
    """
    if current_user.role != UserRole.LOCATION_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires LOCATION_ADMIN role",
        )
    if not current_user.location_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Location-scoped account is missing location assignment",
        )
    return current_user


async def get_current_institution_or_location_admin(
    current_user: Annotated[User, Depends(get_current_active_user)]
) -> User:
    """
    Ensure user has INSTITUTION_ADMIN or LOCATION_ADMIN role.
    """
    if current_user.role not in (UserRole.INSTITUTION_ADMIN.value, UserRole.LOCATION_ADMIN.value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires INSTITUTION_ADMIN or LOCATION_ADMIN role",
        )
    return current_user


async def get_current_location_staff_or_admin(
    current_user: Annotated[User, Depends(get_current_active_user)]
) -> User:
    """
    Ensure user has LOCATION_ADMIN or STAFF role.
    """
    if current_user.role not in (UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires LOCATION_ADMIN or STAFF role",
        )
    if not current_user.location_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Location-scoped account is missing location assignment",
        )
    return current_user


async def get_current_institution_or_location_user(
    current_user: Annotated[User, Depends(get_current_active_user)]
) -> User:
    """
    Ensure user has any institution-scoped role.
    """
    if current_user.role not in (
        UserRole.INSTITUTION_ADMIN.value,
        UserRole.LOCATION_ADMIN.value,
        UserRole.STAFF.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires INSTITUTION_ADMIN, LOCATION_ADMIN, or STAFF role"
        )
    return current_user


async def get_current_group_admin(
    current_user: Annotated[User, Depends(get_current_active_user)]
) -> User:
    """
    Ensure user is a GROUP_ADMIN with a group assignment.

    GROUP_ADMIN is a read-only oversight role scoped to one InstitutionGroup.
    It is intentionally accepted ONLY here (the /group/* routes); every
    institution/location/PHI dependency excludes it, so a group user can never
    reach per-patient PHI, setup, or write endpoints.
    """
    if current_user.role != UserRole.GROUP_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires GROUP_ADMIN role",
        )
    if not current_user.group_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Group-scoped account is missing group assignment",
        )
    return current_user
