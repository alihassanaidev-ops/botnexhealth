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
    Ensure user is an administrator.
    """
    if current_user.role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges"
        )
    return current_user
