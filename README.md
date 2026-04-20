# MegooCI

A simpler, modern open-source alternative to Jenkins.

Next.js frontend, FastAPI backend, Celery for orchestration, and a Go agent
for remote execution. See [docs/prd.md](docs/prd.md) for the full product
spec, and [docs/prd.md §6.15](docs/prd.md) for the current implementation
status snapshot.

## Highlights

- **YAML pipelines** — schema-validated `megooci.yaml` files
- **Modern UI** — Next.js 15 + React 19, real-time log streaming, dark mode
  with auto-detect, mobile responsive
- **Git provider integration** — GitHub / GitLab / generic Git with
  admin-scoped PAT connections, project-level repository picker, and
  manual-paste webhooks with HMAC verification (§6.16)
- **Remote build agents** — self-hosted `megooci-agent` Go binary talks to
  the controller over an authenticated WebSocket and runs steps on remote
  hosts (§6.3)
- **Secrets & environment management** — encrypted at rest, scoped by project
- **Long-lived sessions** — 12-hour access tokens with silent refresh up to
  30 days

### Planned (not yet shipped)

- Python pipelines (`megooci.py` + SDK)
- AI pipeline generator
- Built-in OCI/Docker registry
- Docker / SSH / Kubernetes executors
- Notifications (email / Slack / Teams / Discord)
- Artifacts + JUnit / coverage ingestion
- OIDC / SAML / LDAP / RBAC

## Quick Start

### Prerequisites

- Docker 24+ and the `docker compose` plugin
- Git
- GNU `make` (optional but strongly recommended — on Windows install via
  `choco install make` or `scoop install make`)

The root [Makefile](Makefile) is the primary operator interface. Run
`make` (or `make help`) for the full list of targets.

### 1. First-time setup

```bash
git clone https://github.com/megooci/megooci.git
cd megooci

# Copy .env.example -> .env and edit secrets (MEGOOCI_SECRET_KEY,
# MEGOOCI_JWT_SECRET, POSTGRES_PASSWORD, etc.)
make init
```

### 2. Start the stack

**Production-style (no bind mounts, production Dockerfiles):**

```bash
make up              # = docker compose up -d --build
make logs            # tail logs
make down            # stop (keeps volumes)
```

**Development (hot-reload, bind-mounted source):**

```bash
make dev             # foreground — Ctrl+C to stop
# or
make dev-up          # background
make dev-logs
make dev-down
```

In dev mode:

- Backend: `uvicorn --reload`, `./backend` bind-mounted into the container.
- Frontend: `next dev` with polling watcher (reliable on Windows/WSL).
- Postgres on `localhost:5432`, Redis on `localhost:6379` for direct tool
  access.

### 3. Open the UI

- Web UI: [http://localhost:3000](http://localhost:3000)
- API docs (OpenAPI): [http://localhost:8000/docs](http://localhost:8000/docs)
- Health: `GET http://localhost:8000/health`

The first user to sign up becomes an admin automatically.

## Running a self-hosted build agent

Builds execute locally on the controller unless a `megooci-agent` is
connected. When an agent is online, the controller dispatches steps to it
over WebSocket and streams logs back through the same UI. The agent is
written in Go (single static binary, ~15 MB); see
[agent/README.md](agent/README.md) for the full operator doc and
[docs/prd.md §6.3](docs/prd.md) for the spec.

### Flow

```bash
# 1. One-off: build the agent Docker image
make agent-image

# 2. In the UI, go to Agents -> Register agent. Copy the one-shot token.

# 3. Start the agent container, passing ID + token as CLI flags
make agent-up ID=01234567-89ab-... TOKEN=megci_agt_...

# 4. Verify it connected
make agent-logs
```

Useful overrides (all on the make command line):

| Variable | Default | Purpose |
| --- | --- | --- |
| `ID` | — | **Required** — agent UUID from the UI. |
| `TOKEN` | — | **Required** — token from registration / rotation. |
| `CONTROLLER` | `http://backend:8000` | Use a public URL when running the agent on a different host. |
| `CAPACITY` | `2` | Concurrent steps per agent. |
| `LOG_LEVEL` | `info` | `debug` / `info` / `warn` / `error`. |
| `NETWORK` | `megooci_default` | Docker network — match your Compose project name. |
| `NAME` | `megooci-agent` | Container name (useful when running multiple agents on one host). |

The token is passed as a CLI flag to `docker run`; it never lands in
`.env`, `.bash_history` (assuming `read -s`), or `docker inspect` of any
unrelated container.

Lifecycle:

```bash
make agent-logs         # tail logs
make agent-restart      # restart the container (keeps its original CLI args)
make agent-shell        # /bin/sh inside the container
make agent-down         # stop and remove
```

For a host-only run (no Docker, requires Go 1.22+), use the agent's own
module Makefile:

```bash
cd agent
make build
./bin/megooci-agent run --controller ... --agent-id ... --token ...
```

## Git provider integration

Admins register GitHub / GitLab / generic connections once; project owners
pick a connection and a repository, and MegooCI generates a webhook URL +
one-shot secret to paste into the provider. Pushes trigger builds
automatically. See §6.16 of the PRD for the full feature matrix. Key UI
locations:

- **Integrations** (top-level navbar, admin-only) — create / test / rotate
  connections.
- **Project → Integrations tab** — pick a connection, browse available
  repositories, link one, copy the webhook URL + secret + provider-
  specific instructions, and view delivery history.
- **Pipelines → New** — optional "Linked repository" dropdown that
  inherits the repo URL and default branch from the project's link.

## Makefile reference

Run `make` with no arguments for the full interactive list. Highlights:

| Target | Purpose |
| --- | --- |
| `init` | Create `.env` from `.env.example`. |
| `up` / `down` / `logs` | Production stack. |
| `dev` / `dev-up` / `dev-logs` / `dev-down` | Dev stack (hot-reload). |
| `shell` / `db-shell` / `redis-shell` | Open a shell in backend / Postgres / Redis. |
| `migrate` | `alembic upgrade head`. |
| `migration m="..."` | Generate a new autogenerated migration. |
| `agent-image` | Build the agent Docker image. |
| `agent-image-push` | Push the image to the configured registry. |
| `agent-up ID=.. TOKEN=..` | Start the agent container wired to the running stack. |
| `agent-down` / `agent-restart` / `agent-logs` / `agent-shell` | Agent lifecycle. |
| `nuke CONFIRM=yes` | **Destructive** — drops Postgres, Redis, and storage volumes. |

For Go-specific tasks (`build`, `test`, `vet`, `fmt`, `tidy`, `snapshot`,
`clean`), run `make <target>` from inside the `agent/` directory — see
[agent/Makefile](agent/Makefile).

## Architecture

```
              +------------------+
              |  Next.js 15 UI   |   React 19 / TS / Tailwind
              +---------+--------+
                        | HTTPS + WS
                        v
              +------------------+
              |  FastAPI backend |   /api/v1/* + /ws/*
              +-+-------+------+-+
                |       |      |
           +----v-+ +---v--+ +-v-----------+
           |  PG  | |Redis | | Local FS    |  MEGOOCI_STORAGE_ROOT
           +------+ +--+---+ +-------------+
                       |
                       | Celery broker + pub/sub
                       v
              +------------------+
              | Celery worker    |   runs pipelines; dispatches to agents
              +---------+--------+
                        | WebSocket control plane
                        v
              +------------------+
              |  megooci-agent   |   Go binary on each build host
              +------------------+
```

**Stack at a glance:**

- **Frontend**: Next.js 15 (App Router), React 19, TypeScript, Tailwind 3,
  TanStack Query, Zustand, `sonner`, `lucide-react`, in-house UI primitives.
- **Backend**: FastAPI, SQLAlchemy 2 (async), Pydantic v2, Celery + Celery
  Beat, Alembic. Python 3.12+.
- **Agent**: Go 1.22+, `spf13/cobra`, `gorilla/websocket`, single static
  binary, ~15 MB.
- **Data**: PostgreSQL 16, Redis 7, local filesystem for artifacts and
  logs (`MEGOOCI_STORAGE_ROOT`).

## Configuration

MegooCI reads its configuration from environment variables (see
[.env.example](.env.example) for the full list). Most-used:

| Variable | Default | Description |
| --- | --- | --- |
| `MEGOOCI_SECRET_KEY` | — | Master key for Fernet-encrypting tokens and secrets. |
| `MEGOOCI_JWT_SECRET` | — | JWT signing key. |
| `MEGOOCI_JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `720` | Access-token lifetime (12 h). |
| `MEGOOCI_JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `30` | Refresh-token lifetime. |
| `MEGOOCI_SIGNUP_ENABLED` | `true` | Public signup toggle. |
| `MEGOOCI_STORAGE_ROOT` | `/var/lib/megooci` | Root folder for artifacts and logs. |
| `MEGOOCI_PUBLIC_URL` | `http://localhost:8000` | External URL used to build webhook URLs. |
| `MEGOOCI_WEBHOOK_DELIVERY_RETENTION` | `200` | Rows kept per linked repository. |
| `MEGOOCI_AGENT_VERSION` | `0.1.0-dev` | Tag applied when `make agent-image` builds the agent. |

## Pipeline example

```yaml
version: 1
name: my-app

stages:
  - name: test
    steps:
      - run: npm test

  - name: build
    steps:
      - run: npm run build

  - name: deploy
    when:
      branch: main
    steps:
      - run: ./deploy.sh
```

Assign the pipeline to a project whose linked repository points at your
GitHub / GitLab repo; a push to `main` will fire the webhook, trigger this
pipeline, and run its steps on the next available agent.

## Host-only development (no Docker stack)

If you'd rather run the apps directly on your host and only use Docker for
Postgres + Redis:

```bash
# Start only the infrastructure services
docker compose up db redis -d

# Backend
cd backend
python -m venv .venv
source .venv/bin/activate        # or .venv\Scripts\Activate.ps1 on Windows
pip install -e .
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# Frontend (in a second terminal)
cd frontend
npm install
npm run dev
```

## Repository layout

```
megooci/
+-- agent/                 Go agent — `megooci-agent` binary + Dockerfile
+-- backend/               FastAPI app, Celery workers, Alembic migrations
|   +-- app/api/v1/        REST + WebSocket routers
|   +-- app/models/        SQLAlchemy models
|   +-- app/services/      build_executor, agent_dispatcher, git_providers, ...
|   +-- alembic/versions/  DB migrations
+-- frontend/              Next.js 15 app
|   +-- src/app/           App-router pages
|   +-- src/components/    UI
|   +-- src/lib/api.ts     API client
+-- docs/prd.md            Full product requirements doc
+-- docker-compose.yml     Production stack
+-- docker-compose.dev.yml Dev overrides (hot-reload)
+-- Makefile               Operator entry point
```

## License

Apache-2.0.
