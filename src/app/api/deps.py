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

    try:
        # Supabase tokens are HS256 by default. Ensure algorithm matches config.
        # If using Supabase directly, settings.jwt_secret MUST be the Supabase JWT secret.
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm]
        )
        
        # 'sub' is the unique identifier. 
        # In Supabase/GoTrue, it's the UUID. 
        # In our legacy local implementation, it might have been email.
        token_sub: str | None = payload.get("sub")
        token_email: str | None = payload.get("email")
        
        if token_sub is None:
            raise credentials_exception
            
    except JWTError:
        raise credentials_exception

    async with get_db_session() as session:
        # Strategy 1: Try finding by supabase_id (Standard Supabase Token)
        # This is the most reliable for Supabase auth
        stmt = select(User).where(User.supabase_id == token_sub)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        # Strategy 2: If not found, try finding by email (Legacy or Local Token)
        # Check 'email' claim first (Supabase), then fallback to 'sub' if it looks like an email
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
