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
    
    Supports both:
    1. Local JWTs (sub=email)
    2. Supabase JWTs (sub=uuid, email=email)
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Strategy 1: Verify using Supabase Client (Supports ECC/HS256 automatically)
    # This calls the Supabase GoTrue API to validate the token.
    from src.app.services.supabase_service import SupabaseService
    
    user = None
    token_sub = None
    token_email = None

    try:
        supabase_service = SupabaseService()
        if supabase_service.client:
            # excessive logging here might be improved, but good for debugging auth
            supabase_user = supabase_service.get_user_by_token(token)
            if supabase_user:
                token_sub = str(supabase_user.id)
                token_email = supabase_user.email
    except Exception:
        # If Supabase verification fails (e.g. invalid token, network issue), 
        # fall back to local decoding or just fail later.
        pass

    # Strategy 2: Fallback to Local JWT Decoding (Legacy/Dev)
    # Only if Supabase client didn't return a user (e.g. local dev token or misconfig)
    if not token_sub:
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm]
            )
            token_sub = payload.get("sub")
            token_email = payload.get("email")
        except JWTError:
            # Both strategies failed
            raise credentials_exception

    if token_sub is None:
        raise credentials_exception

    async with get_db_session() as session:
        # Match Supabase User ID (Preferred)
        stmt = select(User).where(User.supabase_id == token_sub)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        # Match Email (Fallback/Legacy)
        if not user:
            search_email = token_email if token_email else token_sub
            if search_email and "@" in search_email:
                stmt = select(User).where(User.email == search_email)
                result = await session.execute(stmt)
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
