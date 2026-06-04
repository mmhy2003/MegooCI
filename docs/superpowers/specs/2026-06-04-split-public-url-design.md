# Design: Split `MEGOOCI_PUBLIC_URL` into App URL and API URL

**Date:** 2026-06-04
**Status:** Approved

## Problem

The Settings page (System info card) shows a "Public URL" row and an "API URL
(browser)" row with identical values. They draw from two different settings that
both, by default, point at the backend:

- "Public URL" = backend `MEGOOCI_PUBLIC_URL` (the backend's external base URL,
  used for webhooks, registry tokens, artifact links, and email-link fallback)
- "API URL (browser)" = `NEXT_PUBLIC_API_URL` (what the browser uses to call the API)

Both are the backend's URL, so in any single-domain deployment — and in the
current split-domain deployment where `MEGOOCI_PUBLIC_URL=https://ci-api.mohamjoe.xyz`
— they render the same string. The "Public URL" label implies "where the app
lives," but it actually shows the API URL again, which is misleading.

The root cause is that `MEGOOCI_PUBLIC_URL` conflates two distinct concepts: the
public **app/frontend** URL and the public **API/backend** URL.

## Decision

Split the single `MEGOOCI_PUBLIC_URL` into two settings with clear meanings:

- `MEGOOCI_PUBLIC_URL` — the **frontend/app** public URL (where users access
  MegooCI in a browser). Default `http://localhost:3000`.
- `MEGOOCI_PUBLIC_API_URL` — **new**. The externally-reachable **API/backend**
  URL (webhooks, registry, artifact links, agent controller URL). Default
  `http://localhost:8000`.

The existing `MEGOOCI_FRONTEND_URL` (previously the frontend URL for email links,
falling back to `MEGOOCI_PUBLIC_URL`) is **removed** — it is now redundant because
`MEGOOCI_PUBLIC_URL` already means the frontend URL.

This is a **breaking semantic change** with a clean cutover (no silent fallback).
Deployments must update their environment files.

### Resolved questions

1. **`MEGOOCI_FRONTEND_URL`** → removed; email links use `MEGOOCI_PUBLIC_URL`
   directly.
2. **`${megooci.url}` pipeline built-in** → points at `MEGOOCI_PUBLIC_API_URL`,
   preserving today's behavior (it was the API base, used by scripts that call
   back into the instance).
3. **Migration** → clean cutover. Update `.env`, `.env.example`, `README.md`, and
   `docs/prd.md`. No backward-compatible fallback.

## Usage classification

Every current `MEGOOCI_PUBLIC_URL` / `public_url` reference, and where it moves:

### Moves to `MEGOOCI_PUBLIC_API_URL` (these are API endpoints)

| Location | Use |
|----------|-----|
| `backend/app/api/v1/registry_oci.py:86` | registry token realm `{url}/v2/token` |
| `backend/app/api/v1/artifacts.py:212` | artifact download links `{url}/api/v1/artifacts/...` |
| `backend/app/api/v1/project_repositories.py:46` | webhook URLs `{url}/api/v1/webhooks/git/{slug}` |
| `backend/app/services/build_executor.py:567` | `${megooci.url}` built-in injected into pipelines |
| `frontend/src/app/agents/page.tsx:177` | controller URL external agents connect to (reads `info.public_api_url`) |

### Stays on `MEGOOCI_PUBLIC_URL` (now the frontend URL; drop the `FRONTEND_URL or` fallback)

| Location | Use |
|----------|-----|
| `backend/app/api/v1/auth.py:309` | password-reset email links |
| `backend/app/api/v1/invites.py:106` | invite email links |
| `backend/app/api/v1/invites.py:254` | invite email links |

### The `system/info` conflict

`GET /api/v1/system/info` exposes a single `public_url` field consumed two ways:

- `frontend/src/app/settings/page.tsx` "Public URL" row wants the **frontend** URL.
- `frontend/src/app/agents/page.tsx` controller URL wants the **API** URL.

Resolution: the endpoint exposes **both** `public_url` (frontend) and a new
`public_api_url` (API). Each consumer reads the field it needs.

### Not affected

- CORS: `backend/app/main.py:65` uses `allow_origins=["*"]` — no change.
- `MEGOOCI_REGISTRY_HOST`: an independent static setting, not derived from the
  public URL in code (the PRD note about deriving from the public URL host is not
  implemented).

## Changes

### Backend — config (`backend/app/config.py`)
- Redefine `MEGOOCI_PUBLIC_URL` default to `http://localhost:3000`; comment as the
  frontend/app public URL.
- Add `MEGOOCI_PUBLIC_API_URL: str = "http://localhost:8000"`; comment as the API
  public URL.
- Delete `MEGOOCI_FRONTEND_URL` and its comment block.

### Backend — switch to `MEGOOCI_PUBLIC_API_URL`
- `registry_oci.py:86`, `artifacts.py:212`, `project_repositories.py:46`,
  `build_executor.py:567`.

### Backend — use `MEGOOCI_PUBLIC_URL` directly (remove `FRONTEND_URL or` fallback)
- `auth.py:309`, `invites.py:106`, `invites.py:254`.

### Backend — `system/info` (`backend/app/api/v1/system.py`)
- Add `public_api_url: str` to the `SystemInfo` Pydantic model.
- Return `public_url=settings.MEGOOCI_PUBLIC_URL` and
  `public_api_url=settings.MEGOOCI_PUBLIC_API_URL`.

### Frontend
- `frontend/src/lib/api.ts` — add `public_api_url: string` to the `SystemInfo`
  interface.
- `frontend/src/app/agents/page.tsx:177` — controller URL reads
  `info.public_api_url`; update surrounding comments.
- `frontend/src/app/settings/page.tsx` — "Public URL" row now shows the frontend
  URL (`info.public_url`); "API URL (browser)" stays on `NEXT_PUBLIC_API_URL`.
  The two rows are now genuinely distinct.

### Docs / env
- `.env` — `MEGOOCI_PUBLIC_URL=https://ci.mohamjoe.xyz`, add
  `MEGOOCI_PUBLIC_API_URL=https://ci-api.mohamjoe.xyz`, remove `MEGOOCI_FRONTEND_URL`.
- `.env.example` — same structure with localhost defaults and updated comments.
- `README.md:261` — update the `MEGOOCI_PUBLIC_URL` row; add a
  `MEGOOCI_PUBLIC_API_URL` row.
- `docs/prd.md` — update row 364 (`MEGOOCI_PUBLIC_URL` description → frontend/app
  URL) and add a `MEGOOCI_PUBLIC_API_URL` row; row 367 (`MEGOOCI_REGISTRY_HOST`
  note "value of `MEGOOCI_PUBLIC_URL` host" → the API URL host); row 540 webhook
  URL example `{MEGOOCI_PUBLIC_URL}/...` → `{MEGOOCI_PUBLIC_API_URL}/...`.
  (`docs/prd.md` does not reference `MEGOOCI_FRONTEND_URL`.)

## Testing

- `system/info` returns both `public_url` and `public_api_url`.
- Webhook URL, artifact download link, and registry token realm build from
  `MEGOOCI_PUBLIC_API_URL`.
- Invite and password-reset email links build from `MEGOOCI_PUBLIC_URL`.
- Existing tests referencing `MEGOOCI_FRONTEND_URL` or the old `MEGOOCI_PUBLIC_URL`
  semantics are updated. Follow TDD for each change.

## Side benefit

After this change, invite and password-reset emails in the current deployment
correctly link to `ci.mohamjoe.xyz` (the app) instead of the API domain.
