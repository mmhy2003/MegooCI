# Scoped API Tokens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users pick a functional scope (Full access, Artifacts Download, Automate Workflows, Read-only) when creating a Personal Access Token, and enforce that scope on every request as a subset of the owner's role permissions.

**Architecture:** A new `token_scopes` catalog module maps each scope key to a set of RBAC permissions. The PAT branch of `get_current_user` stashes the token's scope onto the in-memory user; a single new `effective_permissions()` helper computes `scope ∩ owner-ceiling` and all permission choke points route through it, so every endpoint inherits scoping with no per-endpoint change. The `api_tokens.scopes` array column already exists — no migration. Frontend adds a scope dropdown to the create dialog and a scope badge to each token row.

**Tech Stack:** FastAPI + SQLAlchemy 2 (async) + Pydantic v2 (backend), Next.js + React + TypeScript (frontend), pytest + pytest-asyncio (new, backend tests).

**Spec:** `docs/superpowers/specs/2026-06-04-api-token-scopes-design.md`

**Branch:** `feature/api-token-scopes`

---

## File Structure

**Backend**
- `backend/app/core/token_scopes.py` — **new**. The scope catalog (single source of truth): keys, labels, descriptions, implied permissions, and helpers (`expand_scopes`, `scope_catalog`, `resolve_scope`).
- `backend/app/core/deps.py` — **modify**. Add `effective_permissions` / `effective_scoped_permissions`; route the three choke points through them; set `active_token_scopes` in `get_current_user`.
- `backend/app/api/v1/api_tokens.py` — **modify**. `GET /tokens/scopes`; `scope` field on create; resolved `scope` in responses; unknown-scope validation.
- `backend/pyproject.toml` — **modify**. Add dev test deps + pytest config.
- `backend/tests/__init__.py`, `backend/tests/conftest.py` — **new**. Test package + `make_user` fixture.
- `backend/tests/test_token_scopes.py`, `test_effective_permissions.py`, `test_permission_enforcement.py` — **new**. Unit tests.

**Frontend**
- `frontend/src/lib/api.ts` — **modify**. `scope` on `ApiToken`, `scope` on create payload, `ApiTokenScope` type, `apiTokensApi.scopes()`.
- `frontend/src/app/settings/page.tsx` — **modify**. Scope dropdown in the create dialog; scope badge on each token row.

---

## Task 1: Backend test scaffolding

No backend test harness exists yet. This task adds a minimal pytest setup. The security-critical logic in later tasks is pure (operates on in-memory objects), so these tests need **no database**.

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`

- [ ] **Step 1: Add dev dependencies and pytest config to `pyproject.toml`**

Append these two sections to the end of `backend/pyproject.toml`:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create a test virtualenv and install dependencies**

The host Python has none of the backend libraries installed, and the dev Docker
stack is not required for these unit tests, so create a local venv. The tests
import `app.models` (SQLAlchemy) and, later, `app.core.deps` → `app.database`
(needs `asyncpg`), so install that subset plus pytest.

Run from `backend/` (the venv lives at `backend/.venv`, already covered by `.gitignore`):

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install --upgrade pip
.venv/Scripts/python -m pip install pytest pytest-asyncio sqlalchemy asyncpg fastapi pydantic pydantic-settings "python-jose[cryptography]" bcrypt cryptography email-validator
```

Expected: all install successfully (wheels, no compilation). If a later step hits
`ModuleNotFoundError` for another module, install that module and continue.

> **All backend `python`/`pytest` commands in this plan use `backend/.venv/Scripts/python`.** `app` is importable because the pytest config sets `pythonpath = ["."]`; the project itself is not installed. The `.venv` is never committed (commits stage explicit files only).

- [ ] **Step 3: Create the test package marker**

Create `backend/tests/__init__.py` as an **empty file**.

- [ ] **Step 4: Create `conftest.py` with the `make_user` fixture**

Create `backend/tests/conftest.py`:

```python
"""Shared test fixtures.

`make_user` builds an in-memory User (no database session) for permission
tests. The functions under test only read attributes, so transient model
instances are sufficient.
"""

import pytest

from app.models.role import Role, UserRole
from app.models.user import User

# Distinguishes "no active token scope at all" (JWT-style session) from an
# explicit Full-access token (active_token_scopes=None).
_UNSET = object()


@pytest.fixture
def make_user():
    def _make(
        *,
        is_admin: bool = False,
        role_permissions: set[str] | None = None,
        active_token_scopes: object = _UNSET,
        scope_type: str = "global",
        scope_id=None,
    ) -> User:
        """Build a transient User.

        active_token_scopes:
          - _UNSET (default): JWT-style session, no active token scope.
          - None: a Full-access token.
          - ["artifacts.download"]: a scoped token.

        scope_type / scope_id only take effect when role_permissions is given.
        """
        user = User(email="t@example.com", name="Test", is_admin=is_admin, is_active=True)
        if role_permissions is not None:
            role = Role(name="role", permissions=list(role_permissions))
            user.user_roles.append(
                UserRole(role=role, scope_type=scope_type, scope_id=scope_id)
            )
        if active_token_scopes is not _UNSET:
            user.active_token_scopes = active_token_scopes
        return user

    return _make
```

- [ ] **Step 5: Verify pytest runs (collects zero tests, no errors)**

Run:

```bash
cd backend && .venv/Scripts/python -m pytest -q
```

Expected: exits cleanly with "no tests ran" (collection succeeds, conftest imports without error).

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/tests/__init__.py backend/tests/conftest.py
git commit -m "test(backend): add minimal pytest scaffolding and make_user fixture" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Scope catalog module

The single source of truth: scope keys, labels, descriptions, and the permissions each implies.

**Files:**
- Create: `backend/app/core/token_scopes.py`
- Test: `backend/tests/test_token_scopes.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_token_scopes.py`:

```python
from app.core.permissions import VALID_PERMISSIONS
from app.core.token_scopes import (
    ALL_PERMISSIONS,
    FULL_ACCESS_KEY,
    TOKEN_SCOPES,
    expand_scopes,
    is_valid_scope_key,
    resolve_scope,
    scope_catalog,
)


def test_all_permissions_excludes_admin_sentinel():
    assert "admin" not in ALL_PERMISSIONS
    assert ALL_PERMISSIONS == VALID_PERMISSIONS - {"admin"}


def test_catalog_permissions_are_all_valid_and_not_admin():
    for scope in TOKEN_SCOPES.values():
        assert scope["permissions"] <= VALID_PERMISSIONS
        assert "admin" not in scope["permissions"]


def test_expand_scopes_unions_permissions():
    assert expand_scopes(["artifacts.download"]) == {"artifacts.read", "builds.read"}


def test_expand_scopes_ignores_unknown_keys():
    assert expand_scopes(["nope"]) == set()


def test_read_only_is_every_read_permission():
    assert expand_scopes(["read.only"]) == {
        p for p in VALID_PERMISSIONS if p.endswith(".read")
    }


def test_scope_catalog_lists_full_access_first():
    catalog = scope_catalog()
    assert [c["key"] for c in catalog] == [
        FULL_ACCESS_KEY,
        "artifacts.download",
        "automate.workflows",
        "read.only",
    ]
    assert catalog[0]["label"] == "Full access"


def test_resolve_scope_null_is_full_access():
    assert resolve_scope(None) == {"key": FULL_ACCESS_KEY, "label": "Full access"}


def test_resolve_scope_known_key():
    assert resolve_scope(["artifacts.download"]) == {
        "key": "artifacts.download",
        "label": "Artifacts Download",
    }


def test_resolve_scope_unknown_key_shows_raw():
    assert resolve_scope(["legacy.thing"]) == {
        "key": "legacy.thing",
        "label": "legacy.thing",
    }


def test_is_valid_scope_key():
    assert is_valid_scope_key(FULL_ACCESS_KEY)
    assert is_valid_scope_key("read.only")
    assert not is_valid_scope_key("bogus")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cd backend && .venv/Scripts/python -m pytest tests/test_token_scopes.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.token_scopes'`.

- [ ] **Step 3: Create the module**

Create `backend/app/core/token_scopes.py`:

```python
"""Functional scopes for Personal Access Tokens (PATs).

A scope is a coarse, user-facing capability bundle. A token's *effective*
permissions are always the scope's permissions intersected with the owner's
role permissions (see `app.core.deps.effective_permissions`), so a scope can
only narrow access — never expand it.

This catalog is the single source of truth: the API exposes it for the UI
dropdown, the create endpoint validates against it, and the auth layer expands
stored scope keys to permissions through it.
"""

from app.core.permissions import VALID_PERMISSIONS

# Synthetic UI-only key meaning "no restriction". Stored as NULL in the DB.
FULL_ACCESS_KEY = "full_access"

# Every concrete permission. "admin" is a sentinel, not a concrete capability,
# so it is never grantable via a scope.
ALL_PERMISSIONS: frozenset[str] = VALID_PERMISSIONS - {"admin"}

_READ_ONLY_PERMISSIONS: frozenset[str] = frozenset(
    p for p in ALL_PERMISSIONS if p.endswith(".read")
)

# key -> {label, description, permissions}. Insertion order is the UI order.
TOKEN_SCOPES: dict[str, dict] = {
    "artifacts.download": {
        "label": "Artifacts Download",
        "description": "Download build artifacts.",
        "permissions": frozenset({"artifacts.read", "builds.read"}),
    },
    "automate.workflows": {
        "label": "Automate Workflows",
        "description": "Trigger and manage builds, and read pipeline definitions.",
        "permissions": frozenset({"builds.manage", "builds.read", "pipelines.read"}),
    },
    "read.only": {
        "label": "Read-only",
        "description": "View-only access across everything your role can see.",
        "permissions": _READ_ONLY_PERMISSIONS,
    },
}


def is_valid_scope_key(key: str) -> bool:
    """True if `key` is the Full-access key or a known scope key."""
    return key == FULL_ACCESS_KEY or key in TOKEN_SCOPES


def expand_scopes(keys: list[str]) -> set[str]:
    """Union of permissions implied by the given scope keys.

    Unknown keys contribute nothing. Never includes the "admin" sentinel.
    """
    perms: set[str] = set()
    for key in keys:
        scope = TOKEN_SCOPES.get(key)
        if scope:
            perms |= scope["permissions"]
    return perms


def scope_catalog() -> list[dict]:
    """The catalog for the UI dropdown, Full access first."""
    items: list[dict] = [
        {
            "key": FULL_ACCESS_KEY,
            "label": "Full access",
            "description": "Full access with all of your role's permissions.",
        }
    ]
    items.extend(
        {"key": key, "label": scope["label"], "description": scope["description"]}
        for key, scope in TOKEN_SCOPES.items()
    )
    return items


def resolve_scope(scopes: list[str] | None) -> dict:
    """Resolve a stored `scopes` array to a `{key, label}` for display.

    NULL / empty -> Full access. An unknown/legacy key is shown verbatim.
    """
    if not scopes:
        return {"key": FULL_ACCESS_KEY, "label": "Full access"}
    key = scopes[0]
    scope = TOKEN_SCOPES.get(key)
    if scope is None:
        return {"key": key, "label": key}
    return {"key": key, "label": scope["label"]}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
cd backend && .venv/Scripts/python -m pytest tests/test_token_scopes.py -q
```

Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/token_scopes.py backend/tests/test_token_scopes.py
git commit -m "feat(tokens): add token scope catalog module" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `effective_permissions` helpers

The brains of enforcement: compute the permission set a request actually has, accounting for an active PAT scope. Pure functions — fully unit-testable without a DB.

**Files:**
- Modify: `backend/app/core/deps.py`
- Test: `backend/tests/test_effective_permissions.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_effective_permissions.py`:

```python
import uuid

from app.core.deps import effective_permissions, effective_scoped_permissions


def test_full_access_non_admin_returns_role_permissions(make_user):
    user = make_user(
        role_permissions={"artifacts.read", "builds.manage"},
        active_token_scopes=None,
    )
    assert effective_permissions(user) == {"artifacts.read", "builds.manage"}


def test_jwt_session_unaffected(make_user):
    # active_token_scopes left unset -> behaves like Full access.
    user = make_user(role_permissions={"artifacts.read"})
    assert effective_permissions(user) == {"artifacts.read"}


def test_full_access_admin_includes_admin_sentinel(make_user):
    user = make_user(is_admin=True, active_token_scopes=None)
    assert "admin" in effective_permissions(user)


def test_scoped_non_admin_is_intersected_with_role(make_user):
    # scope perms {artifacts.read, builds.read} ∩ role {artifacts.read}
    user = make_user(
        role_permissions={"artifacts.read"},
        active_token_scopes=["artifacts.download"],
    )
    assert effective_permissions(user) == {"artifacts.read"}


def test_scoped_admin_is_capped_to_scope_without_admin(make_user):
    user = make_user(is_admin=True, active_token_scopes=["artifacts.download"])
    perms = effective_permissions(user)
    assert perms == {"artifacts.read", "builds.read"}
    assert "admin" not in perms


def test_scoped_resource_permissions_match_only_their_resource(make_user):
    pid = uuid.uuid4()
    user = make_user(
        role_permissions={"builds.read"},
        active_token_scopes=None,
        scope_type="project",
        scope_id=pid,
    )
    assert "builds.read" in effective_scoped_permissions(user, "project", pid)
    assert effective_scoped_permissions(user, "project", uuid.uuid4()) == set()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cd backend && .venv/Scripts/python -m pytest tests/test_effective_permissions.py -q
```

Expected: FAIL — `ImportError: cannot import name 'effective_permissions'`.

- [ ] **Step 3: Add the helpers to `deps.py`**

In `backend/app/core/deps.py`, add this import near the other `app.core` imports (top of file, after `from app.core.security import decode_token, hash_pat, is_pat`):

```python
from app.core.token_scopes import ALL_PERMISSIONS, expand_scopes
```

Then add these functions immediately after the existing `_collect_scoped_permissions` definition (right after the `return perms` near line 40). Paste verbatim:

```python
def _all_role_permissions(user: User) -> set[str]:
    """Union of permissions across all of the user's role assignments."""
    perms: set[str] = set()
    for ur in user.user_roles:
        if ur.role and ur.role.permissions:
            perms.update(ur.role.permissions)
    return perms


def _scoped_role_permissions(
    user: User, scope_type: str, scope_id: uuid.UUID | None
) -> set[str]:
    """Role permissions for a resource scope: global roles always apply, plus
    roles assigned to the matching scope."""
    perms: set[str] = set()
    for ur in user.user_roles:
        if ur.role and ur.role.permissions:
            if ur.scope_type == "global" or (
                ur.scope_type == scope_type and ur.scope_id == scope_id
            ):
                perms.update(ur.role.permissions)
    return perms


def _apply_token_scope(role_perms: set[str], is_admin: bool, scopes) -> set[str]:
    """Cap a role-permission set by the active PAT scope.

    - scopes is None  -> Full access / JWT session: role perms (+ "admin" if admin).
    - scopes is a list -> scope perms ∩ ceiling. Ceiling is ALL_PERMISSIONS for
      admins, else the role perms. Result never contains the "admin" sentinel.
    """
    if scopes is None:
        return role_perms | ({"admin"} if is_admin else set())
    scope_perms = expand_scopes(scopes)
    ceiling = set(ALL_PERMISSIONS) if is_admin else role_perms
    return scope_perms & ceiling


def effective_permissions(user: User) -> set[str]:
    """Global permissions a request actually has, accounting for a PAT scope."""
    role_perms = _all_role_permissions(user)
    is_admin = user.is_admin or "admin" in role_perms
    return _apply_token_scope(
        role_perms, is_admin, getattr(user, "active_token_scopes", None)
    )


def effective_scoped_permissions(
    user: User, scope_type: str, scope_id: uuid.UUID | None
) -> set[str]:
    """Resource-scoped permissions a request actually has, accounting for a PAT scope."""
    role_perms = _scoped_role_permissions(user, scope_type, scope_id)
    is_admin = user.is_admin or "admin" in role_perms
    return _apply_token_scope(
        role_perms, is_admin, getattr(user, "active_token_scopes", None)
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
cd backend && .venv/Scripts/python -m pytest tests/test_effective_permissions.py -q
```

Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/deps.py backend/tests/test_effective_permissions.py
git commit -m "feat(auth): add effective_permissions scope-aware helpers" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Wire enforcement into the choke points

Route the three permission checks through the new helpers, and attach the active scope to the user during PAT authentication. After this task, every endpoint enforces token scopes.

**Files:**
- Modify: `backend/app/core/deps.py`
- Test: `backend/tests/test_permission_enforcement.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_permission_enforcement.py`:

```python
import uuid

import pytest
from fastapi import HTTPException

from app.core.deps import (
    check_scoped_permission,
    get_current_admin_user,
    require_permission,
)


async def test_require_permission_allows_when_scope_grants(make_user):
    user = make_user(
        role_permissions={"artifacts.read"},
        active_token_scopes=["artifacts.download"],
    )
    check = require_permission("artifacts.read")
    assert await check(current_user=user) is user


async def test_require_permission_denies_out_of_scope(make_user, monkeypatch):
    async def _noop(**kwargs):
        return None

    # The deny path schedules a fire-and-forget audit record; stub it out.
    monkeypatch.setattr("app.core.audit.record", _noop)
    user = make_user(
        role_permissions={"artifacts.read", "builds.manage"},
        active_token_scopes=["artifacts.download"],  # excludes builds.manage
    )
    check = require_permission("builds.manage")
    with pytest.raises(HTTPException) as exc:
        await check(current_user=user)
    assert exc.value.status_code == 403


async def test_full_access_admin_allowed_on_admin_endpoint(make_user):
    user = make_user(is_admin=True, active_token_scopes=None)
    assert await get_current_admin_user(current_user=user) is user


async def test_scoped_admin_token_denied_on_admin_endpoint(make_user):
    user = make_user(is_admin=True, active_token_scopes=["artifacts.download"])
    with pytest.raises(HTTPException) as exc:
        await get_current_admin_user(current_user=user)
    assert exc.value.status_code == 403


def test_check_scoped_permission_denies_out_of_scope(make_user):
    pid = uuid.uuid4()
    user = make_user(
        role_permissions={"builds.manage"},
        active_token_scopes=["artifacts.download"],
        scope_type="project",
        scope_id=pid,
    )
    with pytest.raises(HTTPException) as exc:
        check_scoped_permission(user, "builds.manage", "project", pid)
    assert exc.value.status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cd backend && .venv/Scripts/python -m pytest tests/test_permission_enforcement.py -q
```

Expected: FAIL — `test_scoped_admin_token_denied_on_admin_endpoint` fails (current `get_current_admin_user` returns early on `is_admin`), and `test_require_permission_denies_out_of_scope` fails (current `require_permission` bypasses on `is_admin` / ignores scope).

- [ ] **Step 3: Rewire `require_permission`**

In `backend/app/core/deps.py`, replace the body of the inner `_check` in `require_permission` (currently lines ~162-181). Find:

```python
    async def _check(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        if current_user.is_admin:
            return current_user
        perms = _collect_permissions(current_user)
        if permission not in perms and "admin" not in perms:
            import asyncio
            from app.core.audit import record as audit_record

            asyncio.ensure_future(audit_record(
                action="permission_denied",
                actor_id=current_user.id,
                metadata={"permission": permission},
            ))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission}' required",
            )
        return current_user
```

Replace with (drops the blanket `is_admin` bypass so scoped admin tokens are capped):

```python
    async def _check(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        perms = effective_permissions(current_user)
        if permission not in perms and "admin" not in perms:
            import asyncio
            from app.core.audit import record as audit_record

            asyncio.ensure_future(audit_record(
                action="permission_denied",
                actor_id=current_user.id,
                metadata={"permission": permission},
            ))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission}' required",
            )
        return current_user
```

- [ ] **Step 4: Rewire `get_current_admin_user`**

In `backend/app/core/deps.py`, replace the body of `get_current_admin_user` (currently lines ~130-145). Find:

```python
    if current_user.is_admin:
        return current_user
    perms = _collect_permissions(current_user)
    if "admin" in perms:
        return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
    )
```

Replace with:

```python
    if "admin" in effective_permissions(current_user):
        return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
    )
```

- [ ] **Step 5: Rewire `check_scoped_permission`**

In `backend/app/core/deps.py`, replace the body of `check_scoped_permission` (currently lines ~196-203). Find:

```python
    if user.is_admin:
        return
    perms = _collect_scoped_permissions(user, scope_type, scope_id)
    if permission not in perms and "admin" not in perms:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission '{permission}' required for this {scope_type}",
        )
```

Replace with:

```python
    perms = effective_scoped_permissions(user, scope_type, scope_id)
    if permission not in perms and "admin" not in perms:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission '{permission}' required for this {scope_type}",
        )
```

- [ ] **Step 6: Attach the active scope during PAT auth**

In `backend/app/core/deps.py`, in `get_current_user`, the PAT branch loads `user` (currently lines ~81-89). Find:

```python
        user_result = await db.execute(
            select(User)
            .options(selectinload(User.user_roles).selectinload(UserRole.role))
            .where(User.id == api_token.user_id)
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            raise credentials_exception
        return user
```

Replace with (stash the token's scope before returning):

```python
        user_result = await db.execute(
            select(User)
            .options(selectinload(User.user_roles).selectinload(UserRole.role))
            .where(User.id == api_token.user_id)
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            raise credentials_exception
        user.active_token_scopes = api_token.scopes
        return user
```

Then, in the JWT branch at the very end of `get_current_user` (currently lines ~115-117), find:

```python
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    return user
```

Replace with (no token scope for browser/JWT sessions):

```python
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    user.active_token_scopes = None
    return user
```

- [ ] **Step 7: Run the enforcement tests and the full suite**

Run:

```bash
cd backend && .venv/Scripts/python -m pytest tests/test_permission_enforcement.py -q && .venv/Scripts/python -m pytest -q
```

Expected: enforcement tests PASS (5 passed), then the full suite PASSES (21 passed total).

- [ ] **Step 8: Commit**

```bash
git add backend/app/core/deps.py backend/tests/test_permission_enforcement.py
git commit -m "feat(auth): enforce PAT scopes across all permission checks" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: API — scope on create, scope catalog endpoint, resolved scope in responses

**Files:**
- Modify: `backend/app/api/v1/api_tokens.py`

This task changes request/response shapes and adds an endpoint. It is verified by the manual API checks in Task 7 (the existing project has no HTTP integration-test harness).

- [ ] **Step 1: Add imports**

In `backend/app/api/v1/api_tokens.py`, add after the existing `from app.models.user import User` import:

```python
from app.core.token_scopes import (
    FULL_ACCESS_KEY,
    TOKEN_SCOPES,
    resolve_scope,
    scope_catalog,
)
```

- [ ] **Step 2: Add the `ScopeInfo` / `ScopeCatalogItem` models and update request/response models**

In the `# ── Schemas ──` section, replace `CreateTokenRequest` and `TokenResponse` and add the two new models. Find:

```python
class CreateTokenRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    expires_in_days: int | None = Field(
        None, ge=1, le=365, description="Days until expiry (null = never)"
    )
    scopes: list[str] | None = Field(
        None, description="Permission scopes (null = inherit user permissions)"
    )


class TokenResponse(BaseModel):
    id: uuid.UUID
    name: str
    token_hint: str
    scopes: list[str] | None
    expires_at: datetime | None
    is_active: bool
    last_used_at: datetime | None
    created_at: datetime
```

Replace with:

```python
class ScopeInfo(BaseModel):
    key: str
    label: str


class ScopeCatalogItem(BaseModel):
    key: str
    label: str
    description: str


class CreateTokenRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    expires_in_days: int | None = Field(
        None, ge=1, le=365, description="Days until expiry (null = never)"
    )
    scope: str | None = Field(
        None,
        description="Scope key from GET /tokens/scopes; null or 'full_access' = full access",
    )


class TokenResponse(BaseModel):
    id: uuid.UUID
    name: str
    token_hint: str
    scopes: list[str] | None
    scope: ScopeInfo
    expires_at: datetime | None
    is_active: bool
    last_used_at: datetime | None
    created_at: datetime
```

(`TokenCreatedResponse(TokenResponse)` below it is unchanged — it inherits the new `scope` field.)

- [ ] **Step 3: Add a response-builder helper**

In `backend/app/api/v1/api_tokens.py`, add this helper right after the `# ── Endpoints ──` comment (before `list_tokens`):

```python
def _token_response(t: ApiToken) -> TokenResponse:
    return TokenResponse(
        id=t.id,
        name=t.name,
        token_hint=t.token_hint,
        scopes=t.scopes,
        scope=ScopeInfo(**resolve_scope(t.scopes)),
        expires_at=t.expires_at,
        is_active=t.is_active,
        last_used_at=t.last_used_at,
        created_at=t.created_at,
    )
```

- [ ] **Step 4: Use the helper in `list_tokens`**

Replace the `return [...]` block in `list_tokens` (currently lines ~67-79). Find:

```python
    rows = result.scalars().all()
    return [
        TokenResponse(
            id=t.id,
            name=t.name,
            token_hint=t.token_hint,
            scopes=t.scopes,
            expires_at=t.expires_at,
            is_active=t.is_active,
            last_used_at=t.last_used_at,
            created_at=t.created_at,
        )
        for t in rows
    ]
```

Replace with:

```python
    rows = result.scalars().all()
    return [_token_response(t) for t in rows]
```

- [ ] **Step 5: Add the `GET /tokens/scopes` endpoint**

Add this endpoint immediately after `list_tokens` (and before `create_token`):

```python
@router.get("/tokens/scopes", response_model=list[ScopeCatalogItem])
async def list_scopes(
    current_user: User = Depends(get_current_user),
) -> list[ScopeCatalogItem]:
    """List the functional scopes available when creating a token."""
    return [ScopeCatalogItem(**item) for item in scope_catalog()]
```

- [ ] **Step 6: Validate the scope and store it in `create_token`**

In `create_token`, replace the body from the `raw_token` line through the `return` (currently lines ~93-123). Find:

```python
    raw_token = generate_pat()

    from datetime import timedelta

    expires_at = None
    if body.expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    api_token = ApiToken(
        user_id=current_user.id,
        name=body.name,
        token_hash=hash_pat(raw_token),
        token_hint=pat_hint(raw_token),
        scopes=body.scopes,
        expires_at=expires_at,
    )
    db.add(api_token)
    await db.commit()
    await db.refresh(api_token)

    return TokenCreatedResponse(
        id=api_token.id,
        name=api_token.name,
        token_hint=api_token.token_hint,
        token=raw_token,
        scopes=api_token.scopes,
        expires_at=api_token.expires_at,
        is_active=api_token.is_active,
        last_used_at=api_token.last_used_at,
        created_at=api_token.created_at,
    )
```

Replace with:

```python
    # Resolve & validate the requested scope.
    if body.scope is None or body.scope == FULL_ACCESS_KEY:
        stored_scopes: list[str] | None = None
    elif body.scope in TOKEN_SCOPES:
        stored_scopes = [body.scope]
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown scope '{body.scope}'",
        )

    raw_token = generate_pat()

    from datetime import timedelta

    expires_at = None
    if body.expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    api_token = ApiToken(
        user_id=current_user.id,
        name=body.name,
        token_hash=hash_pat(raw_token),
        token_hint=pat_hint(raw_token),
        scopes=stored_scopes,
        expires_at=expires_at,
    )
    db.add(api_token)
    await db.commit()
    await db.refresh(api_token)

    return TokenCreatedResponse(
        **_token_response(api_token).model_dump(),
        token=raw_token,
    )
```

- [ ] **Step 7: Verify the module imports cleanly**

Run:

```bash
cd backend && .venv/Scripts/python -c "import app.api.v1.api_tokens"
```

Expected: no output, exit 0 (no syntax/import errors).

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/v1/api_tokens.py
git commit -m "feat(tokens): accept scope on create, expose catalog, return resolved scope" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Frontend — scope dropdown and badge

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/app/settings/page.tsx`

- [ ] **Step 1: Update the API client types and add `scopes()`**

In `frontend/src/lib/api.ts`, replace the API Tokens block (currently lines ~1647-1673). Find:

```typescript
export interface ApiToken {
  id: string;
  name: string;
  token_hint: string;
  scopes: string[] | null;
  expires_at: string | null;
  is_active: boolean;
  last_used_at: string | null;
  created_at: string;
}

export interface ApiTokenCreated extends ApiToken {
  token: string;
}

export const apiTokensApi = {
  list: () => fetchApi<ApiToken[]>("/api/v1/tokens"),

  create: (data: { name: string; expires_in_days?: number | null; scopes?: string[] | null }) =>
    fetchApi<ApiTokenCreated>("/api/v1/tokens", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  revoke: (tokenId: string) =>
    fetchApi<void>(`/api/v1/tokens/${tokenId}`, { method: "DELETE" }),
};
```

Replace with:

```typescript
export interface ApiTokenScope {
  key: string;
  label: string;
  description: string;
}

export interface ApiToken {
  id: string;
  name: string;
  token_hint: string;
  scopes: string[] | null;
  scope: { key: string; label: string };
  expires_at: string | null;
  is_active: boolean;
  last_used_at: string | null;
  created_at: string;
}

export interface ApiTokenCreated extends ApiToken {
  token: string;
}

export const apiTokensApi = {
  list: () => fetchApi<ApiToken[]>("/api/v1/tokens"),

  scopes: () => fetchApi<ApiTokenScope[]>("/api/v1/tokens/scopes"),

  create: (data: { name: string; expires_in_days?: number | null; scope?: string | null }) =>
    fetchApi<ApiTokenCreated>("/api/v1/tokens", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  revoke: (tokenId: string) =>
    fetchApi<void>(`/api/v1/tokens/${tokenId}`, { method: "DELETE" }),
};
```

- [ ] **Step 2: Import the scope type and add state in the settings page**

In `frontend/src/app/settings/page.tsx`, add `ApiTokenScope` to the existing `@/lib/api` import. Find:

```typescript
import { authApi, systemApi, apiTokensApi, type AiInfo, type MaintenanceInfo, type SystemInfo, type ApiToken, type ApiTokenCreated, type AiSettingsUpdate } from "@/lib/api";
```

Replace with:

```typescript
import { authApi, systemApi, apiTokensApi, type AiInfo, type MaintenanceInfo, type SystemInfo, type ApiToken, type ApiTokenCreated, type ApiTokenScope, type AiSettingsUpdate } from "@/lib/api";
```

Then, in the `SettingsPage` component, add scope state next to the other token state (after `const [createdToken, setCreatedToken] = ...`). Find:

```typescript
  const [createdToken, setCreatedToken] = React.useState<ApiTokenCreated | null>(null);
```

Replace with:

```typescript
  const [createdToken, setCreatedToken] = React.useState<ApiTokenCreated | null>(null);
  const [scopeCatalog, setScopeCatalog] = React.useState<ApiTokenScope[]>([]);
  const [newTokenScope, setNewTokenScope] = React.useState<string>("full_access");
```

- [ ] **Step 3: Fetch the scope catalog on mount**

In the `React.useEffect` that loads tokens, add a catalog fetch. Find:

```typescript
    apiTokensApi
      .list()
      .then(setTokens)
      .catch(() => {})
      .finally(() => setTokensLoading(false));
  }, []);
```

Replace with:

```typescript
    apiTokensApi
      .list()
      .then(setTokens)
      .catch(() => {})
      .finally(() => setTokensLoading(false));

    apiTokensApi
      .scopes()
      .then(setScopeCatalog)
      .catch(() => {});
  }, []);
```

- [ ] **Step 4: Add the scope badge to each token row**

In the token list, find the status badge block:

```typescript
                        <Badge
                          variant={t.is_active ? "success" : "cancelled"}
                          className="text-[10px]"
                        >
                          {t.is_active ? "Active" : "Revoked"}
                        </Badge>
```

Replace with (adds a scope badge beside it):

```typescript
                        <Badge
                          variant={t.is_active ? "success" : "cancelled"}
                          className="text-[10px]"
                        >
                          {t.is_active ? "Active" : "Revoked"}
                        </Badge>
                        <Badge variant="outline" className="text-[10px]">
                          {t.scope.label}
                        </Badge>
```

- [ ] **Step 5: Add the scope dropdown to the create dialog and update the subtitle**

In the create-token dialog (the `else` branch with the form), find the description:

```typescript
                <DialogHeader>
                  <DialogTitle>Create API token</DialogTitle>
                  <DialogDescription>
                    The token will inherit your current role&apos;s permissions.
                  </DialogDescription>
                </DialogHeader>
```

Replace with:

```typescript
                <DialogHeader>
                  <DialogTitle>Create API token</DialogTitle>
                  <DialogDescription>
                    Choose what this token can do. Access is always capped by
                    your role&apos;s permissions.
                  </DialogDescription>
                </DialogHeader>
```

Then find the token-name field block:

```typescript
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Token name</label>
                    <Input
                      placeholder="e.g. CI deploy script"
                      value={newTokenName}
                      onChange={(e) => setNewTokenName(e.target.value)}
                      autoFocus
                    />
                  </div>
```

Replace with (adds the Scope `<select>` and a live description after the name field):

```typescript
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Token name</label>
                    <Input
                      placeholder="e.g. CI deploy script"
                      value={newTokenName}
                      onChange={(e) => setNewTokenName(e.target.value)}
                      autoFocus
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Scope</label>
                    <select
                      value={newTokenScope}
                      onChange={(e) => setNewTokenScope(e.target.value)}
                      className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    >
                      {scopeCatalog.map((s) => (
                        <option key={s.key} value={s.key}>
                          {s.label}
                        </option>
                      ))}
                    </select>
                    <p className="text-xs text-muted-foreground">
                      {scopeCatalog.find((s) => s.key === newTokenScope)?.description}
                    </p>
                  </div>
```

- [ ] **Step 6: Send the scope on create and include it in the optimistic row**

In the form's `onSubmit`, find the create call and optimistic insert:

```typescript
                      const result = await apiTokensApi.create({
                        name: newTokenName.trim(),
                        expires_in_days: newTokenExpiry
                          ? parseInt(newTokenExpiry, 10)
                          : null,
                      });
                      setCreatedToken(result);
                      setTokens((prev) => [
                        {
                          id: result.id,
                          name: result.name,
                          token_hint: result.token_hint,
                          scopes: result.scopes,
                          expires_at: result.expires_at,
                          is_active: result.is_active,
                          last_used_at: result.last_used_at,
                          created_at: result.created_at,
                        },
                        ...prev,
                      ]);
                      setNewTokenName("");
                      setNewTokenExpiry("");
```

Replace with (adds `scope` to the payload and the row, and resets the scope):

```typescript
                      const result = await apiTokensApi.create({
                        name: newTokenName.trim(),
                        expires_in_days: newTokenExpiry
                          ? parseInt(newTokenExpiry, 10)
                          : null,
                        scope: newTokenScope,
                      });
                      setCreatedToken(result);
                      setTokens((prev) => [
                        {
                          id: result.id,
                          name: result.name,
                          token_hint: result.token_hint,
                          scopes: result.scopes,
                          scope: result.scope,
                          expires_at: result.expires_at,
                          is_active: result.is_active,
                          last_used_at: result.last_used_at,
                          created_at: result.created_at,
                        },
                        ...prev,
                      ]);
                      setNewTokenName("");
                      setNewTokenExpiry("");
                      setNewTokenScope("full_access");
```

- [ ] **Step 7: Reset the scope when the dialog closes**

In the `Dialog`'s `onOpenChange` reset, find:

```typescript
            if (!open) {
              setShowCreateToken(false);
              setCreatedToken(null);
              setNewTokenName("");
              setNewTokenExpiry("");
            }
```

Replace with:

```typescript
            if (!open) {
              setShowCreateToken(false);
              setCreatedToken(null);
              setNewTokenName("");
              setNewTokenExpiry("");
              setNewTokenScope("full_access");
            }
```

- [ ] **Step 8: Type-check / lint the frontend**

Run:

```bash
cd frontend && npx tsc --noEmit
```

Expected: no type errors. (If the project uses a different check, also run `npm run lint`.)

- [ ] **Step 9: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/app/settings/page.tsx
git commit -m "feat(settings): scope dropdown on token create and scope badge in list" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: End-to-end manual verification

No automated HTTP harness exists, so verify the wired behavior manually against a running stack. (`make dev` / `docker compose -f docker-compose.dev.yml up` per the repo README.)

- [ ] **Step 1: Start the stack and sign in**

Bring up backend + frontend, open the app, log in, and go to **Settings → API Tokens**.

- [ ] **Step 2: Verify the dropdown is data-driven**

Click **Create token**. Confirm the **Scope** dropdown lists: Full access, Artifacts Download, Automate Workflows, Read-only, with a description line that updates as you change selection. Default is **Full access**.

- [ ] **Step 3: Create one token per scope and confirm badges**

Create four tokens (one of each scope). Copy each raw token. Confirm each row shows the correct scope badge (e.g. `Artifacts Download`) next to Active.

- [ ] **Step 4: Verify enforcement with curl**

Replace `<BASE>` with the API base URL and the tokens with the values copied above.

Artifacts-download token can read artifacts but cannot trigger a build:

```bash
# Expect 200 / list:
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer <ARTIFACTS_TOKEN>" <BASE>/api/v1/artifacts
# Expect 403 (builds.manage not in scope) — adjust the trigger path/body to your API:
curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Authorization: Bearer <ARTIFACTS_TOKEN>" <BASE>/api/v1/builds
```

Read-only token is denied any management action:

```bash
# Expect 403:
curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Authorization: Bearer <READONLY_TOKEN>" <BASE>/api/v1/builds
```

Full-access token behaves exactly like the owner (today's behavior):

```bash
# Expect 200:
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer <FULL_ACCESS_TOKEN>" <BASE>/api/v1/artifacts
```

Unknown scope is rejected at creation:

```bash
# Expect 400 "Unknown scope 'bogus'":
curl -s -w "\n%{http_code}\n" -X POST -H "Authorization: Bearer <FULL_ACCESS_TOKEN>" -H "Content-Type: application/json" \
  -d '{"name":"bad","scope":"bogus"}' <BASE>/api/v1/tokens
```

- [ ] **Step 5: Confirm admin capping (if you have an admin account)**

With an admin user, create an `Artifacts Download` token and confirm it is **403** on an admin-only endpoint (e.g. `POST <BASE>/api/v1/system/maintenance` or any `require admin` route), proving a scoped admin token is capped.

- [ ] **Step 6: Confirm browser session is unaffected**

The web UI (JWT session) should work exactly as before — your role permissions are unchanged when not using a PAT.

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Scope catalog (4 scopes + permission bundles) → Task 2. ✅
- Storage, no migration (NULL = full access) → Task 5 Step 6 (`stored_scopes`), reuses existing column. ✅
- `effective_permissions` (scope ∩ ceiling; admin capping; JWT unaffected) → Task 3. ✅
- Threading via Approach A (transient attribute + 3 choke points) → Task 4. ✅
- API: `GET /tokens/scopes`, `scope` on create, resolved `scope` in responses, 400 on unknown → Task 5. ✅
- Immutability (no edit endpoint) → honored (no edit endpoint added). ✅
- Frontend dropdown + badge + data-driven catalog → Task 6. ✅
- Testing: unit tests for catalog/effective_permissions/choke points; manual HTTP verification → Tasks 2–4, 7. ✅ (Integration tests are manual because the repo has no HTTP test harness — called out explicitly, not silently skipped.)

**Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step shows complete code. ✅

**Type/name consistency:** `effective_permissions` / `effective_scoped_permissions` / `_apply_token_scope` / `expand_scopes` / `resolve_scope` / `scope_catalog` / `ScopeInfo` / `ScopeCatalogItem` / `ApiTokenScope` are used consistently across tasks. `active_token_scopes` attribute name matches between `conftest.make_user`, `get_current_user`, and the helpers. Frontend `scope` shape `{key,label}` matches backend `ScopeInfo`. ✅
