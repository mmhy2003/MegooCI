import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import get_current_active_user
from app.core.email import send_password_reset_email
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.models.role import Role, UserRole
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    ResetPasswordRequest,
    SignupRequest,
    TokenResponse,
    UserResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    settings = get_settings()

    user_count = await db.scalar(select(func.count()).select_from(User))
    is_first_user = user_count == 0

    if not is_first_user and not settings.MEGOOCI_SIGNUP_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signup is disabled",
        )

    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=body.email,
        name=body.name,
        hashed_password=hash_password(body.password),
        is_admin=is_first_user,
        is_active=True,
    )
    db.add(user)
    await db.flush()

    role_name = "admin" if is_first_user else settings.MEGOOCI_DEFAULT_ROLE
    role_result = await db.execute(select(Role).where(Role.name == role_name))
    role = role_result.scalar_one_or_none()
    if role:
        db.add(UserRole(user_id=user.id, role_id=role.id, scope_type="global"))

    await db.commit()
    await db.refresh(user)

    if is_first_user:
        settings.MEGOOCI_SIGNUP_ENABLED = False

    token_data = {"sub": str(user.id)}
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is None or user.hashed_password is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    token_data = {"sub": str(user.id)}
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Exchange a refresh token for a new access+refresh pair.

    The token is accepted in the JSON body to match the frontend auth client.
    """
    try:
        payload = decode_token(body.refresh_token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    import uuid

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    token_data = {"sub": str(user.id)}
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: User = Depends(get_current_active_user),
) -> dict:
    from app.core.deps import _collect_permissions, get_user_primary_role_name

    return {
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "is_admin": current_user.is_admin,
        "is_active": current_user.is_active,
        "auth_provider": current_user.auth_provider,
        "created_at": current_user.created_at,
        "role": get_user_primary_role_name(current_user),
        "permissions": sorted(_collect_permissions(current_user)),
    }


@router.put("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    if current_user.hashed_password is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change password for OAuth accounts",
        )

    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    if len(body.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be at least 8 characters",
        )

    current_user.hashed_password = hash_password(body.new_password)
    await db.commit()
    return {"message": "Password changed successfully"}


@router.post("/forgot-password")
async def forgot_password(
    body: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Request a password-reset email.

    Always returns success to prevent email enumeration.
    Uses a short-lived JWT as the reset token (stateless, no DB columns needed).
    """
    settings = get_settings()
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is not None and user.hashed_password is not None:
        token = create_access_token(
            {"sub": str(user.id), "type": "reset"},
            expires_delta=timedelta(hours=1),
        )
        reset_link = f"{settings.MEGOOCI_PUBLIC_URL}/reset-password?token={token}"
        sent = send_password_reset_email(user.email, reset_link)
        if not sent:
            logger.warning("Password-reset email not sent for %s (SMTP not configured?)", user.email)

    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Reset a user's password using the token from the forgot-password email."""
    import uuid as _uuid

    try:
        payload = decode_token(body.token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    if payload.get("type") != "reset":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset token",
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset token",
        )

    result = await db.execute(select(User).where(User.id == _uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    if len(body.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters",
        )

    user.hashed_password = hash_password(body.new_password)
    await db.commit()
    return {"message": "Password has been reset successfully"}
