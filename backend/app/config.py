from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    MEGOOCI_DATABASE_URL: str = (
        "postgresql+asyncpg://megooci:megooci@localhost:5432/megooci"
    )
    MEGOOCI_REDIS_URL: str = "redis://localhost:6379/0"
    MEGOOCI_SECRET_KEY: str = "change-me-in-production"
    MEGOOCI_JWT_SECRET: str = "change-me-jwt-secret"
    MEGOOCI_JWT_ALGORITHM: str = "HS256"
    # 12h gives a typical workday on a single login while still bounding
    # exposure if a token is leaked. Silent refresh on the client extends
    # this further without requiring re-login.
    MEGOOCI_JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 720
    MEGOOCI_JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    MEGOOCI_SIGNUP_ENABLED: bool = True
    MEGOOCI_DEFAULT_ROLE: str = "viewer"
    MEGOOCI_STORAGE_ROOT: str = "/var/lib/megooci"
    MEGOOCI_ARTIFACT_RETENTION_BUILDS: int = 50
    MEGOOCI_ARTIFACT_RETENTION_DAYS: int = 30
    MEGOOCI_AI_ENABLED: bool = True
    MEGOOCI_AI_PROVIDER: str = "openai"
    MEGOOCI_AI_API_KEY: str = ""
    MEGOOCI_AI_MODEL: str = ""
    MEGOOCI_AI_BASE_URL: str = ""
    MEGOOCI_LOG_LEVEL: str = "INFO"

    # Meilisearch
    MEGOOCI_MEILISEARCH_URL: str = "http://localhost:7700"
    MEGOOCI_MEILISEARCH_API_KEY: str = "megooci-meili-master-key"
    MEGOOCI_PUBLIC_URL: str = "http://localhost:8000"
    MEGOOCI_REGISTRY_ENABLED: bool = True
    MEGOOCI_REGISTRY_HOST: str = "localhost"
    MEGOOCI_REGISTRY_PORT: int = 0
    MEGOOCI_REGISTRY_STORAGE_PATH: str = "/var/lib/megooci/registry"
    MEGOOCI_REGISTRY_MAX_UPLOAD_MB: int = 2048
    MEGOOCI_REGISTRY_ALLOW_ANONYMOUS_PULL: bool = False
    MEGOOCI_REGISTRY_GC_CRON: str = "0 3 * * *"

    # Email / SMTP is now configured via the UI (notification channels).
    MEGOOCI_INVITE_EXPIRY_HOURS: int = 72

    # Git provider integration (PRD §6.16)
    # OAuth client credentials are Phase 2; keep them optional so Phase 1
    # works without any configuration.
    MEGOOCI_GITHUB_OAUTH_CLIENT_ID: str = ""
    MEGOOCI_GITHUB_OAUTH_CLIENT_SECRET: str = ""
    MEGOOCI_GITLAB_OAUTH_CLIENT_ID: str = ""
    MEGOOCI_GITLAB_OAUTH_CLIENT_SECRET: str = ""
    # Max WebhookDelivery rows retained per linked repository.
    MEGOOCI_WEBHOOK_DELIVERY_RETENTION: int = 200
    # Per-slug rate limit on the unauthenticated webhook receiver.
    MEGOOCI_WEBHOOK_RATE_LIMIT_PER_MINUTE: int = 60

    model_config = {"env_prefix": "", "env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
