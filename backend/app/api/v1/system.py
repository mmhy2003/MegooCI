from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.config import get_settings
from app.core.deps import get_current_active_user, get_current_admin_user
from app.database import get_db
from app.models.system_setting import SystemSetting
from app.models.user import User
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


# Provider default models, used when MEGOOCI_AI_MODEL is left empty.
_PROVIDER_DEFAULT_MODEL: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-5",
    "ollama": "llama3.2",
    "azure_openai": "",  # deployment name must be set explicitly
    "custom": "",  # user must set model explicitly
}

# Providers that require an API key to be considered fully configured.
_PROVIDERS_REQUIRING_KEY = {"openai", "anthropic", "azure_openai"}

# Keys we store in the system_settings table for AI overrides.
_AI_SETTING_KEYS = {
    "ai_enabled",
    "ai_provider",
    "ai_api_key",
    "ai_model",
    "ai_base_url",
}

_MAINTENANCE_KEY = "maintenance_mode"


async def get_ai_overrides(db: AsyncSession) -> dict[str, str]:
    """Fetch AI-related runtime overrides from the DB.

    Returns a dict of key -> value for any AI settings stored in the
    system_settings table.  Missing keys mean "use the env-var default".
    """
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(_AI_SETTING_KEYS))
    )
    rows = result.scalars().all()
    return {row.key: row.value for row in rows}


def resolve_ai_config(
    overrides: dict[str, str] | None = None,
) -> dict[str, object]:
    """Merge env-var defaults with optional DB overrides and return the
    effective AI configuration as a flat dict.

    This is the single source of truth for AI settings at runtime.
    """
    settings = get_settings()
    ov = overrides or {}

    enabled_str = ov.get("ai_enabled")
    if enabled_str is not None:
        enabled = enabled_str.lower() in ("1", "true", "yes")
    else:
        enabled = settings.MEGOOCI_AI_ENABLED

    provider = ov.get("ai_provider") or settings.MEGOOCI_AI_PROVIDER or "openai"
    api_key = ov.get("ai_api_key") or settings.MEGOOCI_AI_API_KEY or ""
    model = ov.get("ai_model") or settings.MEGOOCI_AI_MODEL or ""
    base_url = ov.get("ai_base_url") or settings.MEGOOCI_AI_BASE_URL or ""

    return {
        "enabled": enabled,
        "provider": provider,
        "api_key": api_key,
        "model": model,
        "base_url": base_url,
    }


class AiInfo(BaseModel):
    enabled: bool
    provider: str
    model: str
    base_url: str | None
    has_api_key: bool
    configured: bool
    status: str        # "ready" | "disabled" | "missing_api_key" | "misconfigured"
    status_detail: str


class StorageInfo(BaseModel):
    storage_root: str
    retention_builds: int
    retention_days: int


class AuthInfo(BaseModel):
    signup_enabled: bool
    default_role: str


class RegistryInfo(BaseModel):
    enabled: bool
    host: str
    storage_path: str
    max_upload_mb: int
    gc_cron: str


class GitIntegrationInfo(BaseModel):
    """Runtime state of the Git provider integration (PRD §6.16)."""

    github_oauth_configured: bool
    gitlab_oauth_configured: bool
    webhook_delivery_retention: int
    webhook_rate_limit_per_minute: int


class MaintenanceInfo(BaseModel):
    enabled: bool
    message: str | None = None


class SystemInfo(BaseModel):
    version: str
    public_url: str       # frontend/app URL (MEGOOCI_PUBLIC_URL)
    public_api_url: str   # backend API URL (MEGOOCI_PUBLIC_API_URL)
    log_level: str
    maintenance: MaintenanceInfo
    ai: AiInfo
    storage: StorageInfo
    auth: AuthInfo
    registry: RegistryInfo
    git: GitIntegrationInfo


class AiSettingsUpdate(BaseModel):
    """Body for PUT /system/ai — all fields optional; only supplied
    fields are updated.  Send an empty string to clear an override
    and fall back to the env-var default."""
    enabled: bool | None = None
    provider: str | None = None
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None


def _build_ai_info(overrides: dict[str, str] | None = None) -> AiInfo:
    cfg = resolve_ai_config(overrides)
    enabled: bool = cfg["enabled"]  # type: ignore[assignment]
    provider: str = cfg["provider"]  # type: ignore[assignment]
    api_key: str = cfg["api_key"]  # type: ignore[assignment]
    model_cfg: str = cfg["model"]  # type: ignore[assignment]
    base_url: str = cfg["base_url"]  # type: ignore[assignment]

    default_model = _PROVIDER_DEFAULT_MODEL.get(provider, "")
    model = model_cfg or default_model
    has_api_key = bool(api_key.strip())

    if not enabled or provider == "disabled":
        ai_status = "disabled"
        status_detail = "AI features are disabled."
        configured = False
    elif provider not in _PROVIDER_DEFAULT_MODEL:
        ai_status = "misconfigured"
        status_detail = (
            f"Unknown AI provider '{provider}'. "
            "Expected one of: openai, anthropic, ollama, azure_openai, custom, disabled."
        )
        configured = False
    elif provider in _PROVIDERS_REQUIRING_KEY and not has_api_key:
        ai_status = "missing_api_key"
        status_detail = (
            f"{provider} provider requires an API key."
        )
        configured = False
    elif not model:
        ai_status = "misconfigured"
        status_detail = (
            "No model configured. Set a model name "
            "(or use a provider with a default model)."
        )
        configured = False
    else:
        ai_status = "ready"
        status_detail = f"{provider} / {model} is ready."
        configured = True

    return AiInfo(
        enabled=enabled,
        provider=provider,
        model=model,
        base_url=base_url or None,
        has_api_key=has_api_key,
        configured=configured,
        status=ai_status,
        status_detail=status_detail,
    )


@router.get("/info", response_model=SystemInfo)
async def get_system_info(
    _current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> SystemInfo:
    """Non-sensitive runtime configuration for display in the UI.

    Secret values (API keys, JWT secret, DB URL, master encryption key) are
    never included — only booleans indicating whether they're set.
    """
    settings = get_settings()
    overrides = await get_ai_overrides(db)

    maint = await _get_maintenance_info(db)

    return SystemInfo(
        version="0.1.0",
        public_url=settings.MEGOOCI_PUBLIC_URL,
        public_api_url=settings.MEGOOCI_PUBLIC_API_URL,
        log_level=settings.MEGOOCI_LOG_LEVEL,
        maintenance=maint,
        ai=_build_ai_info(overrides),
        storage=StorageInfo(
            storage_root=settings.MEGOOCI_STORAGE_ROOT,
            retention_builds=settings.MEGOOCI_ARTIFACT_RETENTION_BUILDS,
            retention_days=settings.MEGOOCI_ARTIFACT_RETENTION_DAYS,
        ),
        auth=AuthInfo(
            signup_enabled=settings.MEGOOCI_SIGNUP_ENABLED,
            default_role=settings.MEGOOCI_DEFAULT_ROLE,
        ),
        registry=RegistryInfo(
            enabled=settings.MEGOOCI_REGISTRY_ENABLED,
            host=settings.MEGOOCI_REGISTRY_HOST,
            storage_path=settings.MEGOOCI_REGISTRY_STORAGE_PATH,
            max_upload_mb=settings.MEGOOCI_REGISTRY_MAX_UPLOAD_MB,
            gc_cron=settings.MEGOOCI_REGISTRY_GC_CRON,
        ),
        git=GitIntegrationInfo(
            github_oauth_configured=bool(
                settings.MEGOOCI_GITHUB_OAUTH_CLIENT_ID
                and settings.MEGOOCI_GITHUB_OAUTH_CLIENT_SECRET
            ),
            gitlab_oauth_configured=bool(
                settings.MEGOOCI_GITLAB_OAUTH_CLIENT_ID
                and settings.MEGOOCI_GITLAB_OAUTH_CLIENT_SECRET
            ),
            webhook_delivery_retention=settings.MEGOOCI_WEBHOOK_DELIVERY_RETENTION,
            webhook_rate_limit_per_minute=settings.MEGOOCI_WEBHOOK_RATE_LIMIT_PER_MINUTE,
        ),
    )


@router.put("/ai", response_model=AiInfo)
async def update_ai_settings(
    body: AiSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_admin_user),
) -> AiInfo:
    """Update AI configuration at runtime (admin only).

    Values are persisted in the ``system_settings`` table and take
    precedence over the corresponding ``MEGOOCI_AI_*`` environment
    variables.  Send an empty string for a field to clear the override
    (reverting to the env-var default).
    """
    # Validate provider if supplied.
    valid_providers = set(_PROVIDER_DEFAULT_MODEL.keys()) | {"disabled"}
    if body.provider is not None and body.provider not in valid_providers:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown provider '{body.provider}'. "
                f"Expected one of: {', '.join(sorted(valid_providers))}."
            ),
        )

    # Map request fields to system_settings keys.
    updates: dict[str, str] = {}
    if body.enabled is not None:
        updates["ai_enabled"] = "true" if body.enabled else "false"
    if body.provider is not None:
        updates["ai_provider"] = body.provider
    if body.api_key is not None:
        updates["ai_api_key"] = body.api_key
    if body.model is not None:
        updates["ai_model"] = body.model
    if body.base_url is not None:
        updates["ai_base_url"] = body.base_url

    if not updates:
        # Nothing to change — just return current state.
        overrides = await get_ai_overrides(db)
        return _build_ai_info(overrides)

    # Upsert each key.
    for key, value in updates.items():
        existing = await db.get(SystemSetting, key)
        if existing:
            existing.value = value
        else:
            db.add(SystemSetting(key=key, value=value))

    await db.flush()

    # Re-read to get fresh merged state.
    overrides = await get_ai_overrides(db)
    return _build_ai_info(overrides)


# ──────────────────────────────────────────────────────────────────────
# Maintenance Mode
# ──────────────────────────────────────────────────────────────────────

async def _get_maintenance_info(db: AsyncSession) -> MaintenanceInfo:
    """Read maintenance mode state from the DB."""
    row = await db.get(SystemSetting, _MAINTENANCE_KEY)
    if row is None or row.value.lower() not in ("1", "true", "yes"):
        return MaintenanceInfo(enabled=False)
    # Check for an optional message stored in a companion key.
    msg_row = await db.get(SystemSetting, "maintenance_message")
    return MaintenanceInfo(
        enabled=True,
        message=msg_row.value if msg_row and msg_row.value else None,
    )


async def is_maintenance_mode(db: AsyncSession) -> bool:
    """Quick check used by the build executor / dispatcher."""
    row = await db.get(SystemSetting, _MAINTENANCE_KEY)
    if row is None:
        return False
    return row.value.lower() in ("1", "true", "yes")


class MaintenanceToggle(BaseModel):
    enabled: bool
    message: str | None = None


@router.get("/maintenance", response_model=MaintenanceInfo)
async def get_maintenance(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> MaintenanceInfo:
    """Check whether maintenance mode is active."""
    return await _get_maintenance_info(db)


@router.put("/maintenance", response_model=MaintenanceInfo)
async def set_maintenance(
    body: MaintenanceToggle,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_admin_user),
) -> MaintenanceInfo:
    """Toggle maintenance mode (admin only).

    When enabled, new builds are queued as ``pending`` but will not be
    dispatched to agents until maintenance mode is turned off.
    """
    value = "true" if body.enabled else "false"
    existing = await db.get(SystemSetting, _MAINTENANCE_KEY)
    if existing:
        existing.value = value
    else:
        db.add(SystemSetting(key=_MAINTENANCE_KEY, value=value))

    # Persist the optional message.
    msg_row = await db.get(SystemSetting, "maintenance_message")
    msg_value = (body.message or "").strip()
    if msg_row:
        msg_row.value = msg_value
    elif msg_value:
        db.add(SystemSetting(key="maintenance_message", value=msg_value))

    await db.flush()
    result = await _get_maintenance_info(db)

    # When maintenance mode is turned off, kick the dispatcher so queued
    # builds start executing immediately.
    if not body.enabled:
        try:
            from app.services.agent_dispatcher import dispatch_pending_builds
            await dispatch_pending_builds()
        except Exception:
            pass  # best-effort; builds will be picked up on the next cycle

    return result
