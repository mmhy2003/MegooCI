import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import get_current_active_user
from app.core.email import send_password_reset_email
from app.core.rate_limit import rate_limit
from app.core.security import (
    create_reset_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.models.role import Role, UserRole
from app.models.user import User
from app.services.administrators import set_administrator
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    ResetPasswordRequest,
    SignupRequest,
    SignupStatus,
    TokenResponse,
    UpdateProfileRequest,
    UserResponse,
)
from app.schemas.two_step import SignInResponse
from app.services.two_step import clock, sign_in
from app.services.two_step import status as two_step_status

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/signup",
    response_model=SignInResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
async def signup(
    body: SignupRequest,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(rate_limit("signup", max_requests=5, window_seconds=300)),
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
        is_active=True,
    )
    db.add(user)
    await db.flush()

    if is_first_user:
        await set_administrator(db, user, True, actor_id=None)
    else:
        role_result = await db.execute(
            select(Role).where(Role.name == settings.MEGOOCI_DEFAULT_ROLE)
        )
        role = role_result.scalar_one_or_none()
        if role:
            db.add(UserRole(user_id=user.id, role_id=role.id, scope_type="global"))

    await db.commit()
    await db.refresh(user)

    if is_first_user:
        settings.MEGOOCI_SIGNUP_ENABLED = False

    # On a server that requires two-step verification, a new account sets
    # it up before it gets a session.
    return await sign_in.decide_and_store(db, user, clock.now())


@router.get("/signup-status", response_model=SignupStatus)
async def signup_status(db: AsyncSession = Depends(get_db)) -> dict:
    """Whether a new account can be created right now.

    Public: the login page asks before anyone is signed in, to decide whether
    to offer "Create an account". Follows the rule ``signup`` applies: the
    first account can always be created, whatever the setting says.
    """
    if get_settings().MEGOOCI_SIGNUP_ENABLED:
        return {"signup_enabled": True}
    anyone = await db.scalar(select(User.id).limit(1))
    return {"signup_enabled": anyone is None}


@router.post("/login", response_model=SignInResponse, response_model_exclude_none=True)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(rate_limit("login", max_requests=10, window_seconds=60)),
) -> dict:
    from app.core.audit import record as audit_record

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is None or user.hashed_password is None:
        await audit_record(
            action="login_failed",
            metadata={"email": body.email, "reason": "invalid_credentials"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not verify_password(body.password, user.hashed_password):
        await audit_record(
            action="login_failed",
            actor_id=user.id,
            metadata={"reason": "wrong_password"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        await audit_record(
            action="login_failed",
            actor_id=user.id,
            metadata={"reason": "account_deactivated"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    # The password is right. Whether that is enough depends on the account:
    # with two-step verification the session comes after the second step,
    # and that is when the sign-in is recorded.
    outcome = await sign_in.decide_and_store(db, user, clock.now())
    if outcome["status"] == sign_in.SIGNED_IN:
        await audit_record(action="login_success", actor_id=user.id)
    return outcome


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

    if not await sign_in.may_renew(db, user.id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Two-step verification is required. Sign in again to set it up.",
        )

    return sign_in.session(user)


async def _two_step_fields(db: AsyncSession, user_id) -> dict:
    """Whether the account has two-step verification on, and whether it still
    owes the server a setup (the app shows a banner for that)."""
    enabled = await two_step_status.is_on(db, user_id)
    return {
        "two_step_enabled": enabled,
        "two_step_setup_required": not enabled and await two_step_status.is_required(db),
    }


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
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
        **await _two_step_fields(db, current_user.id),
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


@router.put("/update-profile", response_model=UserResponse)
async def update_profile(
    body: UpdateProfileRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Update the current user's name and/or email."""
    if body.name is not None:
        stripped = body.name.strip()
        if not stripped:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Name cannot be empty",
            )
        current_user.name = stripped

    if body.email is not None:
        normalised = body.email.strip().lower()
        if normalised != current_user.email:
            # A password reset goes to this address, so whoever may change it
            # may as well change the password: it asks for the current one.
            if not body.current_password:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Enter your current password to change your email.",
                )
            if not current_user.hashed_password or not verify_password(
                body.current_password, current_user.hashed_password
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Wrong password."
                )
            existing = await db.execute(
                select(User).where(User.email == normalised)
            )
            if existing.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A user with this email already exists",
                )
            current_user.email = normalised

    # db.refresh reloads mapped columns and drops the transient PAT scope marker;
    # capture and restore it so the returned permissions stay scope-aware.
    token_scopes = getattr(current_user, "active_token_scopes", None)
    await db.commit()
    await db.refresh(current_user)
    current_user.active_token_scopes = token_scopes

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
        **await _two_step_fields(db, current_user.id),
    }


@router.post("/forgot-password")
async def forgot_password(
    body: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(rate_limit("forgot_password", max_requests=5, window_seconds=300)),
) -> dict:
    """Request a password-reset email.

    Always returns success to prevent email enumeration.
    Uses a short-lived JWT as the reset token (stateless, no DB columns needed).
    """
    settings = get_settings()
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is not None and user.hashed_password is not None:
        token = create_reset_token(str(user.id), timedelta(hours=1))
        frontend_base = settings.MEGOOCI_PUBLIC_URL.rstrip("/")
        reset_link = f"{frontend_base}/reset-password?token={token}"
        sent = await send_password_reset_email(db, user.email, reset_link)
        if not sent:
            logger.warning("Password-reset email not sent for %s (SMTP not configured?)", user.email)

    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(rate_limit("reset_password", max_requests=5, window_seconds=300)),
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
