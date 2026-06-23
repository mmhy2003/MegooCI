"""Shared RBAC test scaffolding: in-memory DB + seeding helpers."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool


@compiles(UUID, "sqlite")
def _uuid_sqlite(element, compiler, **kw):  # pragma: no cover
    return "CHAR(32)"


@compiles(JSONB, "sqlite")
@compiles(PG_JSON, "sqlite")
def _json_sqlite(element, compiler, **kw):  # pragma: no cover
    return "JSON"


@compiles(ARRAY, "sqlite")
def _array_sqlite(element, compiler, **kw):  # pragma: no cover
    return "JSON"


async def build_inmemory_factory():
    import app.models  # noqa: F401 — registers all tables on Base.metadata
    from app.models.base import Base

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def make_role(name: str, permissions: list[str]):
    from app.models.role import Role
    return Role(id=uuid.uuid4(), name=name, permissions=list(permissions))


def make_user(*, is_admin: bool = False, global_role=None, project_roles=()):
    """Transient User with attached UserRole objects (role eager-set)."""
    from app.models.role import UserRole
    from app.models.user import User
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@e.com", name="T",
                is_admin=is_admin, is_active=True)
    user.user_roles = []
    if global_role is not None:
        ur = UserRole(id=uuid.uuid4(), user_id=user.id, role_id=global_role.id,
                      scope_type="global", scope_id=None)
        ur.role = global_role
        user.user_roles.append(ur)
    for scope_id, role in project_roles:
        ur = UserRole(id=uuid.uuid4(), user_id=user.id, role_id=role.id,
                      scope_type="project", scope_id=scope_id)
        ur.role = role
        user.user_roles.append(ur)
    return user


async def seed_project(db, name="P", created_by=None) -> uuid.UUID:
    from app.models.project import Project
    from app.models.user import User
    if created_by is None:
        u = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@e.com", name="creator")
        db.add(u)
        await db.flush()
        created_by = u.id
    p = Project(id=uuid.uuid4(), name=f"{name}-{uuid.uuid4().hex[:6]}",
                slug=f"{name.lower()}-{uuid.uuid4().hex[:6]}", created_by=created_by)
    db.add(p)
    await db.flush()
    return p.id


async def seed_pipeline(db, project_id) -> uuid.UUID:
    from app.models.pipeline import Pipeline
    from app.models.project import Project
    # Pipeline.created_by is non-nullable — resolve the project creator's user id.
    project = await db.get(Project, project_id)
    created_by = project.created_by
    pl = Pipeline(id=uuid.uuid4(), name="pl", project_id=project_id, created_by=created_by)
    db.add(pl)
    await db.flush()
    return pl.id


async def seed_build(db, pipeline_id, status="success") -> uuid.UUID:
    from app.models.build import Build
    b = Build(id=uuid.uuid4(), pipeline_id=pipeline_id, number=1, status=status,
              trigger_type="manual")
    db.add(b)
    await db.flush()
    return b.id
