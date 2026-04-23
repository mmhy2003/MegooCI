import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.database import init_db, async_session

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()

    # Meilisearch: create indexes then bulk-sync existing data.
    try:
        from app.services.search import ensure_indexes, sync_all

        await ensure_indexes()
        async with async_session() as db:
            await sync_all(db)
    except Exception:
        logger.warning("Meilisearch init failed — search will be unavailable", exc_info=True)

    yield


settings = get_settings()

app = FastAPI(
    title="MegooCI API",
    description="A simpler, modern open-source alternative to Jenkins",
    version="0.1.0",
    lifespan=lifespan,
)

class NoCacheAPIMiddleware(BaseHTTPMiddleware):
    """Prevent browsers from caching mutable API responses."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response: Response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response


app.add_middleware(NoCacheAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

from app.api.v1.router import api_v1_router
from app.api.v1.registry_oci import router as registry_oci_router

app.include_router(api_v1_router)
app.include_router(registry_oci_router, tags=["registry-oci"])
