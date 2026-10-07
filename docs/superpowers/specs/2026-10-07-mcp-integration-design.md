# MCP Integration — Design

**Date:** 2026-10-07
**Status:** Approved (design)
**Area:** Backend (new `app/mcp` package), API token scopes, Settings → API Tokens

## Problem

MegooCI can only be driven through its web UI or by hand-written REST calls. Coding agents
(Claude Code, Cursor, and similar) have no first-class way to inspect a failed build, edit a
pipeline, or trigger a run on a developer's behalf.

This design adds a Model Context Protocol (MCP) server to the backend so an agent can do the
core CI work a user does in the UI. The agent authenticates with one of the user's existing
Personal Access Tokens (PATs) and is bound by that user's roles and the token's scope — it
can never do more than its owner.

## Goals

- A developer connects an agent with one URL and one PAT, with nothing to install.
- The agent acts as the token owner. Role permissions, project scoping, and token scopes
  apply exactly as they do for REST and UI requests.
- No second copy of authorization or business logic: every tool call runs through the
  existing REST route handlers.
- v1 covers the core CI workflow: projects, pipelines, builds, artifacts, and search.
- A token scope suited to agents, so users do not have to hand an agent a Full-access token.

## Non-goals

- Admin surface: users, roles, invites, secrets and env vars, git connections, notification
  channels, agents (runners), system settings, and the container registry. These follow in a
  later spec.
- Cascade (force) deletes and artifact deletion through MCP.
- A blocking "wait for build" tool and the manual `dispatch` endpoint. Agents poll
  `get_build`.
- MCP resources, prompts, sampling, or server-initiated notifications. Tools only.
- OAuth for MCP clients. Authentication is a PAT in the `Authorization` header.
- A stdio / locally installed MCP package.
- Addressing tools by name instead of ID (see "IDs, not names").
- A separate agent identity or any change to the RBAC role/permission model.

## Approach

Three approaches were considered:

- **A. Embedded server with curated tools (chosen).** The MCP endpoint lives in the backend;
  each hand-written tool calls the matching REST route in-process with the caller's PAT.
- **B. Auto-generated from OpenAPI.** One tool per endpoint. Rejected: 100+ UUID-heavy tools
  with terse descriptions degrade agent performance, and it exposes user and secret
  management by default.
- **C. Standalone stdio package.** Rejected: a second artifact to publish and version, and
  users must install and update it.

A is possible without duplicating logic because of two existing properties of the codebase:

- `get_current_user` (`backend/app/core/deps.py`) already accepts a PAT as a Bearer token.
- Permission enforcement is centralized in `require_permission`, `check_scoped_permission`,
  and `get_current_admin_user`, with project visibility in `backend/app/core/access.py`.

Business logic for CRUD lives in the route handlers, not in a service layer, so the REST
routes themselves are the only safe unit to reuse.

## Architecture

### Endpoint and transport

- The MCP endpoint is served by the existing FastAPI app at exactly `/mcp` (no redirect),
  so the agent-facing URL is `{MEGOOCI_PUBLIC_API_URL}/mcp`.
- Transport: Streamable HTTP, **stateless**, with plain JSON responses (no SSE streams, no
  server-side sessions). Any uvicorn worker can serve any request.
- Only `POST` is served. Other methods get 405: the transport would otherwise answer `GET`
  with an event stream that stays open and so outlives a revoked token.
- Dependency: the official `mcp` Python SDK, pinned `>=2,<3`.
- The SDK's **low-level server API** is used with our own tool registry, because the tool
  list must vary per caller (see "Tool list filtering").

### Wiring into the app

- `backend/app/main.py` calls `mount_mcp(app)`, which builds the MCP app through the factory
  `create_mcp_app(app)` and adds it to the FastAPI app as a plain route at `/mcp`. Passing the
  app in avoids a circular import: the MCP package needs the app object for in-process calls,
  and `main.py` needs the MCP app to mount it.
- The SDK's session manager must be running for mounted requests to work, and a mounted
  sub-app's own lifespan never runs. The existing `lifespan` in `main.py` therefore enters
  the session manager for the life of the process.
- When `MEGOOCI_MCP_ENABLED` is false, nothing is mounted and `/mcp` returns 404.

### Configuration (`backend/app/config.py`)

| Setting | Default | Meaning |
|---|---|---|
| `MEGOOCI_MCP_ENABLED` | `True` | Serve the MCP endpoint. |
| `MEGOOCI_MCP_ALLOWED_HOSTS` | `""` | Extra comma-separated `Host` values to accept. |

The SDK transport rejects requests whose `Host` header is not on its allow-list (421). The
allow-list is the host of `MEGOOCI_PUBLIC_API_URL`, plus `localhost` and `127.0.0.1`, plus
any entries in `MEGOOCI_MCP_ALLOWED_HOSTS` for deployments where a proxy rewrites `Host`.

The transport also rejects a request that carries an `Origin` header not on its origin
allow-list (403). Agents do not send `Origin`; browser-based MCP tools do. The allowed
origins are those of `MEGOOCI_PUBLIC_URL` and `MEGOOCI_PUBLIC_API_URL`, plus localhost.

### Package layout (`backend/app/mcp/`)

| File | Responsibility |
|---|---|
| `__init__.py` | Exports `create_mcp_app` and `mount_mcp`. |
| `server.py` | Builds the low-level MCP server, its `tools/list` and `tools/call` handlers, and the ASGI app wrapped in the auth guard. |
| `auth.py` | ASGI guard: extracts the Bearer token, authenticates the PAT, attaches the caller to the request, or answers 401. |
| `registry.py` | `ToolSpec` (name, description, input schema, required permissions, annotations, handler) and `visible_tools(tools, permissions)`. |
| `client.py` | `ApiClient`: an in-process HTTP client bound to one caller's token, and the REST-error-to-tool-error mapping. |
| `log_view.py` | Pure function that trims build log chunks for agent consumption. |
| `tools/__init__.py` | `ALL_TOOLS`, the assembled catalog. |
| `tools/_common.py` | Helpers shared by the tool modules. |
| `tools/identity.py` | `whoami`, `search`. |
| `tools/projects.py` | Project tools. |
| `tools/pipelines.py` | Pipeline tools. |
| `tools/builds.py` | Build tools. |
| `tools/artifacts.py` | Artifact tools. |

Each tool module only declares `ToolSpec`s and calls `ApiClient`. It never touches the
database or the permission helpers.

## Authentication and request flow

1. The agent sends `Authorization: Bearer megci_pat_…` to `/mcp`.
2. The guard in `auth.py` rejects the request with **401** and a `WWW-Authenticate: Bearer`
   header when the header is missing, the token is not a PAT, the token is unknown, revoked
   or expired, or the owner is inactive. Browser JWTs are not accepted on this endpoint.
3. On success the guard attaches the authenticated `User` (with `active_token_scopes` set)
   and the raw token to the request for the handlers.
4. A tool handler builds an `ApiClient` for that token and calls `/api/v1/...` through
   `httpx.ASGITransport` — in-process, no network hop.
5. The REST handler authenticates the same token again and performs the real permission
   check. Its response is shaped into the tool result.

### Shared PAT lookup

The PAT branch of `get_current_user` is extracted into one helper in `deps.py`:

```python
async def authenticate_pat(db: AsyncSession, token: str) -> User | None:
    """Return the token's owner with active_token_scopes set, or None when the
    token is unknown, inactive, or expired. Touches last_used_at."""
```

`get_current_user` calls it for the PAT path (behavior unchanged), and the MCP guard calls it
directly. There is one implementation of PAT authentication.

### Tool list filtering

Each `ToolSpec` declares `required_permissions` with any-of semantics. `tools/list` returns
only the tools for which the caller holds at least one required permission, computed with
`effective_permissions(user)` — the union across all of the user's role assignments with the
token scope applied. A caller whose set contains the `admin` sentinel sees every tool.

This filtering is **advisory**: it keeps an agent from attempting calls that cannot succeed.
A user whose `builds.manage` is granted on one project still sees `trigger_build`, and gets
the normal 403 from REST when targeting another project. `tools/call` for a hidden tool
returns the same "unknown tool" error as a tool that does not exist.

## Tool catalog

Twenty-two tools. All IDs are UUID strings.

| Tool | REST route | Required permission (any of) |
|---|---|---|
| `whoami` | `GET /auth/me` | — |
| `search` | `GET /search` | `projects.read`, `pipelines.read`, `builds.read`, `artifacts.read` |
| `list_projects` | `GET /projects` | `projects.read` |
| `get_project` | `GET /projects/{id}` | `projects.read` |
| `list_project_repositories` | `GET /projects/{id}/repositories/` | `projects.read` |
| `create_project` | `POST /projects` | `projects.manage` |
| `update_project` | `PUT /projects/{id}` | `projects.manage` |
| `delete_project` | `DELETE /projects/{id}` | `projects.manage` |
| `list_pipelines` | `GET /pipelines` | `pipelines.read` |
| `get_pipeline` | `GET /pipelines/{id}` | `pipelines.read` |
| `validate_pipeline_yaml` | `POST /pipelines/validate` | `pipelines.read` |
| `create_pipeline` | `POST /pipelines` | `pipelines.manage` |
| `update_pipeline` | `PUT /pipelines/{id}` | `pipelines.manage` |
| `delete_pipeline` | `DELETE /pipelines/{id}` | `pipelines.manage` |
| `list_builds` | `GET /builds` | `builds.read` |
| `get_build` | `GET /builds/{id}` | `builds.read` |
| `get_build_logs` | `GET /builds/{id}/logs` | `builds.read` |
| `trigger_build` | `POST /builds/{pipeline_id}/trigger` | `builds.manage` |
| `cancel_build` | `POST /builds/{id}/cancel` | `builds.manage` |
| `retry_build` | `POST /builds/{id}/retry` | `builds.manage` |
| `list_build_artifacts` | `GET /builds/{id}/artifacts` | `artifacts.read` |
| `get_artifact_download_url` | `GET /artifacts/{id}/signed-url` | `artifacts.read` |

### Tool behavior

- **Annotations.** Read tools set `readOnlyHint`. `delete_project`, `delete_pipeline`, and
  `cancel_build` set `destructiveHint`, so clients can prompt before running them.
- **Pagination.** List tools take `skip` and `limit` with the REST defaults and bounds, and
  return `total` where the REST route provides it.
- **Compact rows.** List tools return a reduced row per item:
  - projects: `id`, `name`, `slug`, `description`, `parent_id`
  - pipelines: `id`, `project_id`, `name`, `default_branch`, `enabled` (no `yaml_content`)
  - builds: `id`, `pipeline_id`, `number`, `status`, `branch`, `commit_sha`, `trigger_type`,
    `started_at`, `finished_at`
- **`get_pipeline`** returns the full pipeline including `yaml_content`.
- **`get_build`** returns the build with its stages and steps (name, type, status, exit
  code, timings). Step `config_json` is omitted.
- **Polling guidance.** While a build is pending, queued or running, `get_build`,
  `trigger_build` and `retry_build` add `poll_after_seconds: 60` to their result, and the
  tool descriptions and server instructions tell the agent to wait that long between
  `get_build` calls. This is guidance only: the server cannot make an agent wait.
- **`update_pipeline`** accepts the `PipelineUpdate` fields, including `enabled`, so it also
  serves as enable/disable.
- **Null means "leave unchanged".** On create and update tools, an optional argument passed
  as `null` is not sent to the API, because agents routinely pass null for arguments they do
  not care about. A field therefore cannot be cleared through MCP: update tools also reject
  an empty string. An update with no fields to change is a tool error.
- **`trigger_build`** accepts `pipeline_id`, optional `branch`, `commit_sha`, and `params`.
  When the pipeline YAML is invalid the tool error carries the line-level errors from REST.
- **Deletes never cascade.** `delete_project` and `delete_pipeline` do not expose `force`
  and never send `force=true`. When REST answers 409 because dependents exist, the tool
  error lists what is in the way and states that a cascade delete must be done by a person in
  the UI. The REST hint to retry with `?force=true` is removed from the message.
- **`get_artifact_download_url`** returns a signed, time-limited URL (`ttl` passes through,
  REST bounds apply). The agent downloads the file itself; binaries never pass through MCP.
- **`whoami`** returns the owner's id, email, name, primary role, admin flag, and the
  effective permission list, which already reflects the token scope.

### Build logs

The REST route returns every persisted log chunk for a build as one flat list. `log_view`
reduces that for an agent:

- Inputs: optional `step` (step name, exact match), `tail_lines` (default 200, max 2000).
- Default selection when `step` is not given: if the build has failed steps, only those
  steps' logs; otherwise all steps.
- Output: text grouped under `stage / step` headings, the last `tail_lines` lines of the
  selection, and a note stating how many lines were omitted.
- ANSI escape sequences are removed, and the returned log text is capped at 60,000
  characters (keeping the end), so one very long line cannot flood the agent.

The REST route still loads every chunk into memory, so trimming reduces what the agent
receives, not server cost. Pushing the limit down into the query is a possible later
improvement and is out of scope here.

### IDs, not names

All tools take UUIDs, matching the REST routes. Names are not unique across projects, so
resolving them would need ambiguity handling in every tool. An agent finds an ID with a list
tool or `search`, then acts on it. Tool descriptions say this explicitly.

## Token scope: Coding agent

One new entry in `backend/app/core/token_scopes.py::TOKEN_SCOPES`:

| Label | Stored key | Implied permissions |
|---|---|---|
| **Coding agent** | `coding.agent` | `projects.read`, `projects.manage`, `pipelines.read`, `pipelines.manage`, `builds.read`, `builds.manage`, `artifacts.read` |

- Description: "Lets a coding agent work with projects, pipelines, builds, and artifacts
  through MCP or the API."
- It is an ordinary scope: effective permissions are still `scope ∩ owner ceiling`, it is
  never treated as admin, and it works for REST calls as well as MCP.
- No migration and no dropdown change: `GET /tokens/scopes` and the create-token dialog are
  driven by the catalog.
- MCP does not require this scope. Any PAT works; its scope and the owner's roles decide
  which tools appear and succeed.

## Settings UI

The only frontend change is a setup block on **Settings → API Tokens**.

- `GET /system/info` gains `mcp: { enabled: bool, url: str }`, where `url` is
  `{MEGOOCI_PUBLIC_API_URL}/mcp`. The URL comes from the backend because the frontend and
  API can have different public URLs.
- When `mcp.enabled` is true, the tokens section shows the MCP URL with a copy button and a
  ready-to-paste snippet with a `<your-token>` placeholder:

  ```
  claude mcp add --transport http megooci <url> --header "Authorization: Bearer <your-token>"
  ```

- Copy next to it recommends creating a token with the **Coding agent** scope.
- `frontend/src/lib/api.ts`: add `mcp` to the `SystemInfo` type.

## Error handling

| Situation | Result |
|---|---|
| Missing, malformed, unknown, revoked, or expired token; inactive owner | HTTP 401 from the guard, before any MCP handling. |
| Unknown or hidden tool name | Tool error "Unknown tool". |
| Arguments fail the tool's input schema | Tool error listing the invalid arguments. The server validates with the tool's own input model; the SDK transport does not. Unknown arguments are rejected. |
| REST 400 / 403 / 404 / 409 / 413 / 422 | Tool error (`isError`) whose text is the REST `detail`. A structured `detail` (pipeline validation errors) is rendered as a message plus one line per error with line and column. |
| REST 503 (maintenance mode) | Tool error with the REST `detail`. |
| REST 5xx or an unexpected exception | Tool error "MegooCI internal error"; the exception is logged server-side with its traceback. Internal details are not returned to the agent. |

Each tool call writes one INFO log line: tool name, user id, REST status, duration. Tool
arguments are not logged, since they can contain pipeline YAML and build parameters.

## Security notes

- A token can never exceed its owner, and a scoped token can never act as admin. Both
  properties come from `effective_permissions` and are unchanged.
- Revoking a token or downgrading its owner takes effect on the next request; the transport
  is stateless, so there is no session to outlive the change.
- The in-process client forwards only the caller's own `Authorization` header. There is no
  service credential and no path that skips REST authentication.
- Tools return content authored by other users (pipeline YAML, build logs). Agents must treat
  that as data. This is inherent to the feature and is noted in the server's instructions
  string.

## Testing

Backend tests follow the in-memory SQLite pattern in `backend/tests/_rbac.py`. That helper is
extended so PostgreSQL `ARRAY` columns (role permissions, token scopes) round-trip on SQLite,
which the MCP tests need because they authenticate through the real database path. The
integration tests build a FastAPI app from the individual routers the tools call, because
the local test environment cannot import the full application (it lacks `litellm`).

**Unit**
- `visible_tools`: read-only permissions hide every write tool; the `admin` sentinel shows
  all; a `coding.agent` token owned by a viewer shows only read tools.
- Error mapping: 403, 404, 409, and structured 400 details produce the expected tool error
  text; a 500 does not leak the response body.
- `log_view`: tail limit, omitted-line count, failed-steps default, and step filter.
- `authenticate_pat`: unknown, inactive, and expired tokens return `None`; a valid token
  returns the owner with `active_token_scopes` set.
- `coding.agent` expands to exactly the seven listed permissions.

**Integration (MCP client against the app in-process)**
- No `Authorization` header → 401. A JWT → 401.
- `/mcp` answers without a redirect.
- A `read.only` token's tool list contains no write tools.
- `trigger_build` with a token lacking `builds.manage` → tool error naming the permission.
- A user with a project-scoped role sees only that project from `list_projects`, and gets a
  permission error from `get_pipeline` on another project's pipeline.
- `delete_pipeline` on a pipeline with builds → tool error, and the pipeline still exists.
- With `MEGOOCI_MCP_ENABLED=false`, `/mcp` returns 404.

**Regression**
- The existing PAT and permission tests pass unchanged after the `authenticate_pat`
  extraction.

**Manual**
- Connect Claude Code to a local instance with a Coding-agent token: list projects, trigger
  a build, read the logs of a failed build.
- Confirm the setup block on the Settings page shows the correct URL.

## Files touched

- `backend/pyproject.toml` — add `mcp>=2,<3`.
- `backend/app/config.py` — `MEGOOCI_MCP_ENABLED`, `MEGOOCI_MCP_ALLOWED_HOSTS`.
- `backend/app/main.py` — build and mount the MCP app; run its session manager in `lifespan`.
- `backend/app/core/deps.py` — extract `authenticate_pat`; `get_current_user` calls it.
- `backend/app/core/token_scopes.py` — add the `coding.agent` scope.
- `backend/app/api/v1/system.py` — `mcp` in `SystemInfo`.
- `backend/app/mcp/` — **new** package (see "Package layout").
- `backend/tests/` — new MCP unit and integration tests; `_rbac.py` gains ARRAY-on-SQLite
  support.
- `.env.example`, `README.md` — document the settings and how to connect an agent.
- `frontend/src/lib/api.ts` — `mcp` on `SystemInfo`.
- `frontend/src/app/settings/page.tsx` — MCP setup block in the API Tokens section.

## Rollback

No schema change. Setting `MEGOOCI_MCP_ENABLED=false` removes the endpoint without a deploy
of new code. Reverting the code removes the package; tokens created with the `coding.agent`
scope would then show their raw key as the label and expand to no permissions (deny-all),
which is the existing behavior for unknown scope keys.
