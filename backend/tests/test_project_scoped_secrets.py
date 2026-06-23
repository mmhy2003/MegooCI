import os, uuid
import pytest, pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, make_role, make_user, seed_project, seed_user

DEV = ["secrets.read", "secrets.manage"]


async def _seed_secret(db, scope_type, scope_id, name):
    from app.models.secret import Secret
    creator_id = await seed_user(db)
    db.add(Secret(id=uuid.uuid4(), scope_type=scope_type, scope_id=scope_id,
                  name=name, secret_type="text", encrypted_payload=b"x",
                  created_by=creator_id))


async def _seed_env_var(db, scope_type, scope_id, name):
    from app.models.secret import EnvVar
    creator_id = await seed_user(db)
    db.add(EnvVar(id=uuid.uuid4(), scope_type=scope_type, scope_id=scope_id,
                  name=name, value="v", created_by=creator_id))


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


# ── Secrets: list ────────────────────────────────────────────────────────────

async def test_list_secrets_project_scope_member_sees_only_their_project(sf):
    from app.api.v1.secrets import list_secrets
    async with sf() as db:
        a = await seed_project(db, "A")
        await _seed_secret(db, "project", a, "S_A")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    async with sf() as db:
        rows = await list_secrets(scope_type="project", scope_id=a, db=db, _current_user=user)
    assert {s.name for s in rows} == {"S_A"}


async def test_list_secrets_project_scope_nonmember_empty(sf):
    from app.api.v1.secrets import list_secrets
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        await _seed_secret(db, "project", b, "S_B")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    async with sf() as db:
        rows = await list_secrets(scope_type="project", scope_id=b, db=db, _current_user=user)
    assert rows == []


async def test_list_global_secrets_hidden_from_nonadmin(sf):
    from app.api.v1.secrets import list_secrets
    async with sf() as db:
        a = await seed_project(db, "A")
        await _seed_secret(db, "global", None, "S_GLOBAL")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    async with sf() as db:
        rows = await list_secrets(scope_type="global", scope_id=None, db=db, _current_user=user)
    assert rows == []


async def test_list_global_secrets_visible_to_global_admin(sf):
    from app.api.v1.secrets import list_secrets
    async with sf() as db:
        await _seed_secret(db, "global", None, "S_GLOBAL")
        await db.commit()
    user = make_user(global_role=make_role("admin", DEV + ["admin"]))
    async with sf() as db:
        rows = await list_secrets(scope_type="global", scope_id=None, db=db, _current_user=user)
    assert {s.name for s in rows} == {"S_GLOBAL"}


# ── Secrets: create ──────────────────────────────────────────────────────────

async def test_create_secret_project_scope_member_ok(sf):
    from app.api.v1.secrets import create_secret
    from app.schemas.secret import SecretCreate
    from app.models.user import User
    async with sf() as db:
        a = await seed_project(db, "A")
        # Persist the test user so `created_by` FK is satisfied.
        u = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@e.com", name="T",
                 is_admin=False, is_active=True)
        db.add(u)
        await db.flush()
        user_id = u.id
        await db.commit()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    user.id = user_id  # align transient user id with persisted row
    body = SecretCreate(scope_type="project", scope_id=a, name="NEW_S",
                        secret_type="text", value="val")
    async with sf() as db:
        result = await create_secret(body=body, db=db, current_user=user)
    assert result.name == "NEW_S"


async def test_create_secret_project_scope_nonmember_403(sf):
    from app.api.v1.secrets import create_secret
    from app.schemas.secret import SecretCreate
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    body = SecretCreate(scope_type="project", scope_id=b, name="FAIL_S",
                        secret_type="text", value="val")
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await create_secret(body=body, db=db, current_user=user)
    assert exc.value.status_code == 403


async def test_create_global_secret_nonadmin_403(sf):
    from app.api.v1.secrets import create_secret
    from app.schemas.secret import SecretCreate
    async with sf() as db:
        a = await seed_project(db, "A")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    body = SecretCreate(scope_type="global", scope_id=None, name="GLOBAL_S",
                        secret_type="text", value="val")
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await create_secret(body=body, db=db, current_user=user)
    assert exc.value.status_code == 403


# ── Secrets: update ──────────────────────────────────────────────────────────

async def test_update_secret_project_scope_nonmember_403(sf):
    from app.api.v1.secrets import update_secret
    from app.schemas.secret import SecretUpdate
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        await _seed_secret(db, "project", b, "S_B")
        await db.commit()
        from sqlalchemy import select
        from app.models.secret import Secret
        sid = (await db.execute(select(Secret.id).where(Secret.name == "S_B"))).scalar_one()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await update_secret(secret_id=sid, body=SecretUpdate(name="X"),
                                db=db, _current_user=user)
    assert exc.value.status_code == 403


# ── Secrets: delete ──────────────────────────────────────────────────────────

async def test_delete_secret_project_scope_nonmember_403(sf):
    from app.api.v1.secrets import delete_secret
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        await _seed_secret(db, "project", b, "S_B")
        await db.commit()
        from sqlalchemy import select
        from app.models.secret import Secret
        sid = (await db.execute(select(Secret.id).where(Secret.name == "S_B"))).scalar_one()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await delete_secret(secret_id=sid, db=db, _current_user=user)
    assert exc.value.status_code == 403


# ── Env vars: list ───────────────────────────────────────────────────────────

async def test_list_env_vars_project_scope_member_sees_only_their_project(sf):
    from app.api.v1.secrets import list_env_vars
    async with sf() as db:
        a = await seed_project(db, "A")
        await _seed_env_var(db, "project", a, "EV_A")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    async with sf() as db:
        rows = await list_env_vars(scope_type="project", scope_id=a, db=db, _current_user=user)
    assert {e.name for e in rows} == {"EV_A"}


async def test_list_env_vars_project_scope_nonmember_empty(sf):
    from app.api.v1.secrets import list_env_vars
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        await _seed_env_var(db, "project", b, "EV_B")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    async with sf() as db:
        rows = await list_env_vars(scope_type="project", scope_id=b, db=db, _current_user=user)
    assert rows == []


async def test_list_global_env_vars_hidden_from_nonadmin(sf):
    from app.api.v1.secrets import list_env_vars
    async with sf() as db:
        a = await seed_project(db, "A")
        await _seed_env_var(db, "global", None, "EV_GLOBAL")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    async with sf() as db:
        rows = await list_env_vars(scope_type="global", scope_id=None, db=db, _current_user=user)
    assert rows == []


# ── Env vars: create ─────────────────────────────────────────────────────────

async def test_create_env_var_project_scope_member_ok(sf):
    from app.api.v1.secrets import create_env_var
    from app.schemas.secret import EnvVarCreate
    from app.models.user import User
    async with sf() as db:
        a = await seed_project(db, "A")
        # Persist the test user so `created_by` FK is satisfied.
        u = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@e.com", name="T",
                 is_admin=False, is_active=True)
        db.add(u)
        await db.flush()
        user_id = u.id
        await db.commit()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    user.id = user_id  # align transient user id with persisted row
    body = EnvVarCreate(scope_type="project", scope_id=a, name="NEW_EV", value="v")
    async with sf() as db:
        result = await create_env_var(body=body, db=db, current_user=user)
    assert result.name == "NEW_EV"


async def test_create_env_var_project_scope_nonmember_403(sf):
    from app.api.v1.secrets import create_env_var
    from app.schemas.secret import EnvVarCreate
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    body = EnvVarCreate(scope_type="project", scope_id=b, name="FAIL_EV", value="v")
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await create_env_var(body=body, db=db, current_user=user)
    assert exc.value.status_code == 403


async def test_create_global_env_var_nonadmin_403(sf):
    from app.api.v1.secrets import create_env_var
    from app.schemas.secret import EnvVarCreate
    async with sf() as db:
        a = await seed_project(db, "A")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    body = EnvVarCreate(scope_type="global", scope_id=None, name="GLOBAL_EV", value="v")
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await create_env_var(body=body, db=db, current_user=user)
    assert exc.value.status_code == 403


# ── Env vars: update ─────────────────────────────────────────────────────────

async def test_update_env_var_project_scope_nonmember_403(sf):
    from app.api.v1.secrets import update_env_var
    from app.schemas.secret import EnvVarUpdate
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        await _seed_env_var(db, "project", b, "EV_B")
        await db.commit()
        from sqlalchemy import select
        from app.models.secret import EnvVar
        eid = (await db.execute(select(EnvVar.id).where(EnvVar.name == "EV_B"))).scalar_one()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await update_env_var(env_var_id=eid, body=EnvVarUpdate(name="X"),
                                 db=db, _current_user=user)
    assert exc.value.status_code == 403


# ── Env vars: delete ─────────────────────────────────────────────────────────

async def test_delete_env_var_project_scope_nonmember_403(sf):
    from app.api.v1.secrets import delete_env_var
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        await _seed_env_var(db, "project", b, "EV_B")
        await db.commit()
        from sqlalchemy import select
        from app.models.secret import EnvVar
        eid = (await db.execute(select(EnvVar.id).where(EnvVar.name == "EV_B"))).scalar_one()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await delete_env_var(env_var_id=eid, db=db, _current_user=user)
    assert exc.value.status_code == 403
