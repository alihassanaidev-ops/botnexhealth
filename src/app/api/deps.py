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
