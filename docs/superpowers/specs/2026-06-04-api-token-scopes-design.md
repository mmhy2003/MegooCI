# API Token Scopes — Design

**Date:** 2026-06-04
**Status:** Approved (design)
**Area:** Settings → API Tokens (Personal Access Tokens)

## Problem

Personal Access Tokens (PATs) created in **Settings → API Tokens** currently inherit
*all* of the owner's role permissions. A user cannot create a narrow, purpose-specific
token (for example, a token that can only download artifacts, or only trigger builds).

The `api_tokens.scopes` column already exists but is **dead weight**: the create-token UI
only collects a name and expiry, and the auth layer never reads `scopes`. A PAT
authenticates and is handed the owner's full permission set.

This design lets a user pick a **functional scope** when creating a token and **enforces**
that scope on every request, capped by the owner's role permissions so a token can never do
more than its owner.

## Goals

- Let users choose a scope from a dropdown when creating a token (one scope per token).
- Display the scope on each token row in the list.
- Enforce the scope on every authenticated request, as a subset of the owner's role
  permissions (never an escalation).
- No database migration (reuse the existing `scopes` column).
- Backward compatible: every existing token (all `scopes = NULL`) keeps Full access.

## Non-goals

- Multiple scopes per token (the column is an array and could support it later, but v1 is
  one scope per token via a single-select dropdown).
- Editing a token's scope after creation (immutable — revoke and recreate to change).
- A Registry push/pull scope (deferred; easy to add to the catalog later).
- Changing the RBAC role/permission model itself.

## Security model

A token's **effective permissions** are always:

```
effective = scope_permissions ∩ owner_ceiling
```

- For a non-admin owner, `owner_ceiling` = the union of their role permissions.
- For an admin owner (`is_admin` or the `admin` permission), `owner_ceiling` = all
  permissions — so a scoped admin token equals exactly the scope's permissions.
- **Full access** (stored `NULL`) means *no restriction*: `effective` = the owner's ceiling
  unchanged (today's behavior), including the `admin` sentinel for admins.

Consequences:

- A token can never exceed its owner. If the owner's role is later downgraded, the token
  shrinks with it.
- A **scoped** token is never treated as admin: its effective set never contains the `admin`
  sentinel, so admin-only endpoints reject it even when the owner is an admin.

## Scope catalog

Defined in a new module `backend/app/core/token_scopes.py` as the single source of truth.
Each scope has a stored **key**, a UI **label**, a **description**, and the **set of
permissions** it implies. Permission strings are drawn from
`backend/app/core/permissions.py::VALID_PERMISSIONS`.

| Label | Stored key | Implied permissions |
|---|---|---|
| **Full access** | `NULL` (UI key `full_access`) | — (no restriction; owner's full ceiling) |
| **Artifacts Download** | `artifacts.download` | `artifacts.read`, `builds.read` |
| **Automate Workflows** | `automate.workflows` | `builds.manage`, `builds.read`, `pipelines.read` |
| **Read-only** | `read.only` | `projects.read`, `pipelines.read`, `builds.read`, `artifacts.read`, `agents.read`, `registry.read`, `users.read`, `secrets.read` |

Decisions (confirmed during design):

- **Artifacts Download** includes `builds.read` so a script can list builds to locate an
  artifact, not only `artifacts.read`.
- **Read-only** includes `secrets.read`. It remains protected by the intersection (only
  effective if the owner's role has `secrets.read`).

`full_access` is a synthetic UI-only key. The API maps it to stored `NULL`; storage never
holds the literal string `full_access`.

## Storage (no migration)

Reuse the existing `api_tokens.scopes` column (`ARRAY(String(100))`, nullable):

- **Full access** → `scopes = NULL`.
- A scoped token → `scopes = ["<key>"]`, e.g. `["artifacts.download"]`.

All existing rows are already `NULL`, so they are Full access with no data migration. The
column's documented meaning changes from "raw permission strings (unused)" to "scope keys".

## Backend enforcement (Approach A)

All permission enforcement funnels through three choke points in
`backend/app/core/deps.py`, used across 128 call sites in 19 files:
`require_permission(...)`, `check_scoped_permission(...)`, and `get_current_admin_user`.
Centralizing the scope intersection there covers every endpoint with no per-endpoint change.

### 1. Carry the active scope on the user (transient attribute)

In the **PAT branch** of `get_current_user`, after loading the token, set:

```python
user.active_token_scopes = api_token.scopes   # None for Full access, else ["<key>"]
```

The **JWT branch** leaves the attribute unset (treated as `None`). This is an in-memory,
per-request attribute on the `User` instance — not a mapped column.

### 2. One new function: `effective_permissions`

```python
def effective_permissions(user) -> set[str]:
    role_perms = _role_permissions(user)            # union of user's role.permissions
    is_admin   = user.is_admin or "admin" in role_perms
    scopes     = getattr(user, "active_token_scopes", None)

    if scopes is None:                               # Full access / JWT session
        return role_perms | ({"admin"} if is_admin else set())

    scope_perms = expand_scopes(scopes)              # from token_scopes catalog; never "admin"
    ceiling     = ALL_PERMISSIONS if is_admin else role_perms
    return scope_perms & ceiling                     # capped; never contains "admin"
```

- `ALL_PERMISSIONS` = `VALID_PERMISSIONS - {"admin"}` (every concrete permission).
- `expand_scopes(keys)` = union of the implied permission sets for the given scope keys,
  from the catalog. Unknown keys expand to nothing.
- The existing `_collect_permissions` / `_collect_scoped_permissions` are folded into (or
  call) `effective_permissions` so there is a single source of truth. Resource-scope logic
  in `_collect_scoped_permissions` is preserved, with the scope intersection applied on top.

### 3. Route the three choke points through it

- **`require_permission(perm)`** — remove the `if user.is_admin: return user` early-out;
  admit iff `perm in effective_permissions(user)` or `"admin" in effective_permissions(user)`.
  This is the key fix that caps an admin-owned scoped token.
- **`check_scoped_permission(...)`** — apply the same intersection on top of resource scoping.
- **`get_current_admin_user`** — admit iff `"admin" in effective_permissions(user)`.

## API changes (`backend/app/api/v1/api_tokens.py`)

### `GET /api/v1/tokens/scopes` (new)

Returns the catalog for the dropdown (data-driven UI, no drift):

```json
[
  { "key": "full_access",        "label": "Full access",       "description": "..." },
  { "key": "artifacts.download", "label": "Artifacts Download", "description": "..." },
  { "key": "automate.workflows", "label": "Automate Workflows", "description": "..." },
  { "key": "read.only",          "label": "Read-only",          "description": "..." }
]
```

### `POST /api/v1/tokens` (changed)

`CreateTokenRequest`: replace `scopes: list[str] | None` with `scope: str | None`.

- `null` or `"full_access"` → store `scopes = NULL`.
- a valid catalog key → store `scopes = ["<key>"]`.
- an unknown key → **400 Bad Request**, detail `"Unknown scope '<x>'"`.

### `GET /api/v1/tokens` and create response (changed)

`TokenResponse` gains a derived `scope` object resolved from the stored key:

- stored `NULL` → `{ "key": "full_access", "label": "Full access" }`.
- stored `["<key>"]` → `{ "key": "<key>", "label": "<label>" }`.

The raw `scopes` array is kept for completeness; the UI uses the resolved `scope`.

### Revoke endpoint

Unchanged. Scope is immutable; to change scope, revoke and create a new token.

## Frontend changes (`frontend/src/app/settings/page.tsx`, `frontend/src/lib/api.ts`)

### Create-token dialog

- Add a **Scope** `<select>` between the name and expiry fields, styled like the existing
  AI-provider select on this page, populated from `GET /tokens/scopes` (fetched once on mount
  alongside the token list).
- Default selection: **Full access**.
- Show a one-line muted description of the selected scope (from the catalog).
- Replace the subtitle *"The token will inherit your current role's permissions."* with
  scope-aware copy, e.g. *"Choose what this token can do. Access is always capped by your
  role's permissions."*
- On submit, send `scope: <selectedKey>`.

### Token list rows

- Add a scope **Badge** next to the Active/Revoked badge, reading `scope.label`
  (e.g. `Full access`, `Artifacts Download`).

### API client (`frontend/src/lib/api.ts`)

- Add `scope` to the create payload type.
- Add `scope` to `ApiToken` / `ApiTokenCreated`.
- Add `apiTokensApi.scopes()` to fetch the catalog.

## Error handling & edge cases

- **Unknown scope on create** → 400, validated against the catalog.
- **Admin owner, scoped token** → capped to the scope; 403 on admin-only and out-of-scope
  endpoints.
- **Owner downgraded after token creation** → token shrinks automatically (intersection is
  computed per request from current role permissions).
- **Legacy tokens (`scopes = NULL`)** → Full access, unchanged.
- **JWT (browser) sessions** → `active_token_scopes` is `None`; behavior unchanged.

## Testing

**Backend unit (`effective_permissions`)**
- Full access (`NULL`) returns the owner's role permissions unchanged.
- Scoped non-admin returns `scope ∩ role`.
- Scoped admin is capped to scope permissions and never contains `admin`.
- JWT session (attribute unset) is unaffected.

**Backend integration**
- `artifacts.download` token: `GET` artifacts succeeds; triggering a build → 403.
- `read.only` token: any `*.manage` endpoint → 403.
- Admin-owned scoped token: admin-only endpoint → 403.
- Create with an unknown scope → 400.

**Frontend (manual verification)**
- Create a token of each scope; confirm the row badge renders.
- Confirm the dropdown is populated from the API and the default is Full access.

## Files touched

- `backend/app/core/token_scopes.py` — **new**: catalog + `expand_scopes`.
- `backend/app/core/deps.py` — `effective_permissions`; route the three choke points through
  it; set `active_token_scopes` in the PAT branch of `get_current_user`.
- `backend/app/api/v1/api_tokens.py` — `GET /tokens/scopes`; `scope` in create request;
  resolved `scope` in responses; unknown-scope validation.
- `frontend/src/app/settings/page.tsx` — scope dropdown in the create dialog; scope badge in
  the list.
- `frontend/src/lib/api.ts` — `scope` types and `apiTokensApi.scopes()`.
- Tests for `effective_permissions` and the scoped-token integration cases above.

## Rollback

No schema change, so rollback is code-only. Reverting the code returns PATs to inheriting the
owner's full permissions; stored scope keys become inert (the auth layer would simply stop
reading them).
