from fastapi import APIRouter

from app.api.v1 import (
    agents,
    auth,
    builds,
    pipelines,
    projects,
    secrets,
    system,
    websocket,
)

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_v1_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_v1_router.include_router(pipelines.router, prefix="/pipelines", tags=["pipelines"])
api_v1_router.include_router(builds.router, prefix="/builds", tags=["builds"])
api_v1_router.include_router(secrets.router, prefix="/secrets-env", tags=["secrets"])
api_v1_router.include_router(agents.router, prefix="/agents", tags=["agents"])
api_v1_router.include_router(system.router, prefix="/system", tags=["system"])
api_v1_router.include_router(websocket.router, tags=["websocket"])
