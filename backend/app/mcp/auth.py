"""Authentication guard for the MCP endpoint.

Runs before any MCP handling: a request without a valid Personal Access Token
never reaches the protocol layer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.deps import authenticate_pat, effective_permissions
from app.core.security import is_pat
from app.models.user import User

CALLER_STATE_KEY = "mcp_caller"


@dataclass(frozen=True)
class McpCaller:
    """The authenticated caller of one MCP request."""

    user: User
    token: str
    # Union across all of the user's role assignments, capped by the token scope.
    permissions: frozenset[str]


def _bearer_token(headers: Headers) -> str | None:
    value = headers.get("authorization")
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


class PatAuthGuard:
    """ASGI wrapper that admits only requests carrying a valid PAT."""

    def __init__(self, app: ASGIApp, session_factory: Callable[[], Any]) -> None:
        self.app = app
        self._session_factory = session_factory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        token = _bearer_token(Headers(scope=scope))
        user: User | None = None
        # Browser JWTs are deliberately not accepted here: only PATs.
        if token is not None and is_pat(token):
            async with self._session_factory() as db:
                user = await authenticate_pat(db, token)

        if token is None or user is None or not user.is_active:
            response = JSONResponse(
                {"detail": "A valid MegooCI API token is required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        scope.setdefault("state", {})[CALLER_STATE_KEY] = McpCaller(
            user=user,
            token=token,
            permissions=frozenset(effective_permissions(user)),
        )
        await self.app(scope, receive, send)
