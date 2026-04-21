import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

app.include_router(api_v1_router)
