import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_auth import get_current_agent
from app.core.deps import get_current_active_user, get_current_admin_user
from app.core.security import (
    agent_token_prefix,
    generate_agent_token,
    hash_agent_token,
)
from app.database import get_db
from app.models.agent import Agent
from app.models.user import User
from app.schemas.agent import (
    AgentCreate,
    AgentRegistrationResponse,
    AgentResponse,
    AgentUpdate,
    HeartbeatRequest,
)

router = APIRouter()


# A stale agent is one we haven't heard from in this many seconds. Kept
# short in v1 so UI feedback after a crash is fast; the Go agent sends a
# heartbeat every ~15s so one missed beat is fine.
_AGENT_STALE_SECONDS = 60


def _normalize_status(agent: Agent) -> Agent:
    """Mark agents as offline if they haven't sent a heartbeat recently."""
    if agent.last_seen_at is None:
        agent.status = "offline"
        return agent

    last_seen = agent.last_seen_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    delta = (datetime.now(timezone.utc) - last_seen).total_seconds()

    if delta > _AGENT_STALE_SECONDS and agent.status != "offline":
        agent.status = "offline"
    return agent


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    status_filter: str | None = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> list[Agent]:
    query = select(Agent).order_by(Agent.name).offset(skip).limit(limit)
    if status_filter:
        query = query.where(Agent.status == status_filter)

    result = await db.execute(query)
    agents = list(result.scalars().all())
    for agent in agents:
        _normalize_status(agent)
    return agents


@router.post(
    "",
    response_model=AgentRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_agent(
    body: AgentCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_admin_user),
) -> dict:
    existing = await db.execute(select(Agent).where(Agent.name == body.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent with name '{body.name}' already exists",
        )

    # Generate + persist the token. Plaintext is returned to the admin once;
    # the server keeps only a bcrypt hash plus a short display prefix.
    token = generate_agent_token()
    agent = Agent(
        name=body.name,
        labels=body.labels,
        os=body.os,
        arch=body.arch,
        capacity=body.capacity,
        status="offline",
        token_hash=hash_agent_token(token),
        token_prefix=agent_token_prefix(token),
        token_issued_at=datetime.now(timezone.utc),
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    return {
        "id": agent.id,
        "name": agent.name,
        "labels": agent.labels,
        "os": agent.os,
        "arch": agent.arch,
        "capacity": agent.capacity,
        "last_seen_at": agent.last_seen_at,
        "status": agent.status,
        "token_prefix": agent.token_prefix,
        "token_issued_at": agent.token_issued_at,
        "agent_version": agent.agent_version,
        "connected_at": agent.connected_at,
        "created_at": agent.created_at,
        "updated_at": agent.updated_at,
        "registration_token": token,
    }


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> Agent:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )
    return _normalize_status(agent)


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_admin_user),
) -> Agent:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(agent, field, value)

    await db.commit()
    await db.refresh(agent)
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_admin_user),
) -> None:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )
    await db.delete(agent)
    await db.commit()


@router.post(
    "/{agent_id}/rotate-token",
    response_model=AgentRegistrationResponse,
)
async def rotate_agent_token(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_admin_user),
) -> dict:
    """Issue a fresh token for an agent.

    The previous token is invalidated immediately. Use this if you suspect a
    token has been exposed, or when bringing a replacement host online.
    """
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )

    token = generate_agent_token()
    agent.token_hash = hash_agent_token(token)
    agent.token_prefix = agent_token_prefix(token)
    agent.token_issued_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(agent)

    return {
        "id": agent.id,
        "name": agent.name,
        "labels": agent.labels,
        "os": agent.os,
        "arch": agent.arch,
        "capacity": agent.capacity,
        "last_seen_at": agent.last_seen_at,
        "status": agent.status,
        "token_prefix": agent.token_prefix,
        "token_issued_at": agent.token_issued_at,
        "agent_version": agent.agent_version,
        "connected_at": agent.connected_at,
        "created_at": agent.created_at,
        "updated_at": agent.updated_at,
        "registration_token": token,
    }


@router.post("/{agent_id}/heartbeat", response_model=AgentResponse)
async def agent_heartbeat(
    body: HeartbeatRequest | None = None,
    db: AsyncSession = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> Agent:
    """Called by a running agent to report it's alive.

    Authenticated with the agent's bearer token (no user JWT). Optionally
    accepts a version string so the UI can show what agent binary version is
    connected.
    """
    agent.last_seen_at = datetime.now(timezone.utc)
    if body and body.version:
        agent.agent_version = body.version[:64]
    if agent.status == "offline":
        agent.status = "online"

    await db.commit()
    await db.refresh(agent)
    return agent
