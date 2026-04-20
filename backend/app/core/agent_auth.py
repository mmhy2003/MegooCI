"""Agent authentication (PRD §6.3 / F-3.4).

An agent authenticates to the controller with a persistent bearer token
issued at registration (or via /rotate-token). The plaintext token is shown
to the human operator exactly once; the server stores a bcrypt hash and a
12-char prefix.

We support three carrier styles because different callers have different
options:

- ``Authorization: Bearer <token>`` header   — for REST (e.g. /heartbeat).
- ``?token=<token>`` query parameter         — for WebSocket upgrade from
  browsers / clients that can't set custom headers on the upgrade request.
- ``X-MegooCI-Agent-Token`` header           — for WebSocket clients that
  can set custom headers (the Go agent).
"""

from __future__ import annotations

import uuid

from fastapi import Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_agent_token
from app.database import get_db
from app.models.agent import Agent


class AgentAuthError(Exception):
    """Raised by `authenticate_agent_token` when validation fails."""


async def authenticate_agent_token(
    db: AsyncSession, agent_id: uuid.UUID, token: str | None
) -> Agent:
    """Verify a plaintext token against the stored bcrypt hash for an agent.

    Returns the Agent on success. Raises :class:`AgentAuthError` with a short
    reason on failure; the caller decides whether to turn that into a 401, a
    WebSocket close code, etc.
    """
    if not token:
        raise AgentAuthError("missing agent token")

    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise AgentAuthError("unknown agent")
    if not agent.token_hash:
        raise AgentAuthError("agent has no registered token")
    if not verify_agent_token(token, agent.token_hash):
        raise AgentAuthError("invalid agent token")
    return agent


async def get_current_agent(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_megooci_agent_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> Agent:
    """FastAPI dependency that resolves the Agent from a request.

    Use this on REST endpoints where the ``{agent_id}`` is a path parameter.
    """

    plaintext = _extract_token(authorization, x_megooci_agent_token, token)
    try:
        return await authenticate_agent_token(db, agent_id, plaintext)
    except AgentAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _extract_token(
    authorization: str | None,
    x_header: str | None,
    query_token: str | None,
) -> str | None:
    """Resolve a bearer token from the three accepted carriers."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip() or None
    if x_header:
        return x_header.strip() or None
    if query_token:
        return query_token.strip() or None
    return None


def extract_ws_agent_token(websocket_request) -> str | None:
    """Pull the token out of a ``WebSocket`` request for manual auth.

    FastAPI's ``Depends`` doesn't apply to WebSocket endpoints in the same
    way as HTTP endpoints, so WS handlers call this helper directly.
    """
    headers = {k.decode().lower(): v.decode() for k, v in websocket_request.headers.raw}
    auth = headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip() or None

    xhdr = headers.get("x-megooci-agent-token")
    if xhdr:
        return xhdr.strip() or None

    qtoken = websocket_request.query_params.get("token")
    if qtoken:
        return qtoken.strip() or None

    return None
