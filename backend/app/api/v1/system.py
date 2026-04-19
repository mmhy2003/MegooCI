from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import get_settings
from app.core.deps import get_current_active_user
from app.models.user import User

router = APIRouter()


# Provider default models, used when MEGOOCI_AI_MODEL is left empty.
_PROVIDER_DEFAULT_MODEL: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-5",
    "ollama": "llama3.2",
    "azure_openai": "",  # deployment name must be set explicitly
}

# Providers that require an API key to be considered fully configured.
_PROVIDERS_REQUIRING_KEY = {"openai", "anthropic", "azure_openai"}


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


class SystemInfo(BaseModel):
    version: str
    public_url: str
    log_level: str
    ai: AiInfo
    storage: StorageInfo
    auth: AuthInfo
    registry: RegistryInfo


def _build_ai_info() -> AiInfo:
    settings = get_settings()
    provider = (settings.MEGOOCI_AI_PROVIDER or "").strip().lower()
    configured_model = (settings.MEGOOCI_AI_MODEL or "").strip()
    default_model = _PROVIDER_DEFAULT_MODEL.get(provider, "")
    model = configured_model or default_model
    has_api_key = bool((settings.MEGOOCI_AI_API_KEY or "").strip())

    if not settings.MEGOOCI_AI_ENABLED or provider == "disabled":
        status = "disabled"
        status_detail = "AI features are disabled via MEGOOCI_AI_ENABLED."
        configured = False
    elif provider not in _PROVIDER_DEFAULT_MODEL:
        status = "misconfigured"
        status_detail = (
            f"Unknown AI provider '{settings.MEGOOCI_AI_PROVIDER}'. "
            "Expected one of: openai, anthropic, ollama, azure_openai, disabled."
        )
        configured = False
    elif provider in _PROVIDERS_REQUIRING_KEY and not has_api_key:
        status = "missing_api_key"
        status_detail = (
            f"{provider} provider requires MEGOOCI_AI_API_KEY to be set."
        )
        configured = False
    elif not model:
        status = "misconfigured"
        status_detail = (
            "No model configured. Set MEGOOCI_AI_MODEL "
            "(or use a provider with a default model)."
        )
        configured = False
    else:
        status = "ready"
        status_detail = f"{provider} / {model} is ready."
        configured = True

    return AiInfo(
        enabled=settings.MEGOOCI_AI_ENABLED,
        provider=settings.MEGOOCI_AI_PROVIDER,
        model=model,
        base_url=settings.MEGOOCI_AI_BASE_URL or None,
        has_api_key=has_api_key,
        configured=configured,
        status=status,
        status_detail=status_detail,
    )


@router.get("/info", response_model=SystemInfo)
async def get_system_info(
    _current_user: User = Depends(get_current_active_user),
) -> SystemInfo:
    """Non-sensitive runtime configuration for display in the UI.

    Secret values (API keys, JWT secret, DB URL, master encryption key) are
    never included — only booleans indicating whether they're set.
    """
    settings = get_settings()

    return SystemInfo(
        version="0.1.0",
        public_url=settings.MEGOOCI_PUBLIC_URL,
        log_level=settings.MEGOOCI_LOG_LEVEL,
        ai=_build_ai_info(),
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
        ),
    )
