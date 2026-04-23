from fastapi import APIRouter

from app.api.v1 import (
    agents,
    agents_ws,
    ai_assistant,
    auth,
    builds,
    gates,
    git_connections,
    invites,
    notifications,
    pipelines,
    project_repositories,
    projects,
    registry,
    roles,
    search,
    secrets,
    system,
    user_notifications,
    users,
    webhooks_git,
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
api_v1_router.include_router(roles.router, prefix="/roles", tags=["roles"])
api_v1_router.include_router(users.router, prefix="/users", tags=["users"])
api_v1_router.include_router(invites.router, prefix="/invites", tags=["invites"])
api_v1_router.include_router(
    git_connections.router, prefix="/git/connections", tags=["git-integration"]
)
api_v1_router.include_router(
    project_repositories.router,
    prefix="/projects/{project_id}/repositories",
    tags=["git-integration"],
)
api_v1_router.include_router(
    notifications.router, prefix="/notifications", tags=["notifications"]
)
api_v1_router.include_router(
    user_notifications.router,
    prefix="/user-notifications",
    tags=["user-notifications"],
)
api_v1_router.include_router(search.router, prefix="/search", tags=["search"])
api_v1_router.include_router(gates.router, prefix="/gates", tags=["gates"])
api_v1_router.include_router(ai_assistant.router, prefix="/ai", tags=["ai"])
api_v1_router.include_router(registry.router, prefix="/registry", tags=["registry"])
api_v1_router.include_router(webhooks_git.router, tags=["git-integration"])
api_v1_router.include_router(websocket.router, tags=["websocket"])
api_v1_router.include_router(agents_ws.router, tags=["agents"])
