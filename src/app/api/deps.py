"""
API dependencies for authentication and authorization.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select

from src.app.config import settings
from src.app.database import get_db_session
from src.app.models.user import User, UserRole

# OAuth2 scheme for Swagger UI
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> User:
    """
    Validate JWT token and return current user.

    Strategy 1 (primary): Decode local JWT — sub = user UUID.
    Strategy 2 (fallback): Verify via Supabase API — for raw Supabase tokens
                           (e.g. before token exchange on /auth/users/me).
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    user_id: str | None = None

    # Strategy 1: Decode local JWT (sub = UUID)
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm]
        )
        user_id = payload.get("sub")
    except JWTError:
        pass

    # Strategy 2: Fallback to Supabase API verification
    if not user_id:
        from src.app.services.supabase_service import SupabaseService
        try:
            supabase_service = SupabaseService()
            if supabase_service.client:
                supabase_user = supabase_service.get_user_by_token(token)
                if supabase_user:
                    user_id = str(supabase_user.id)
        except Exception:
            pass

    if not user_id:
        raise credentials_exception

    # Look up by UUID (user.id = auth.users.id)
    async with get_db_session() as session:
        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if user is None:
            raise credentials_exception

        return user


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
