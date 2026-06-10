<div align="center">

# MegooCI

**A simpler, modern open-source alternative to Jenkins.**

Next.js frontend · FastAPI backend · Celery orchestration · Go build agent

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org)
[![Go](https://img.shields.io/badge/go-1.22+-00ADD8.svg?logo=go&logoColor=white)](https://go.dev)
[![Next.js](https://img.shields.io/badge/Next.js-15-000000.svg?logo=next.js&logoColor=white)](https://nextjs.org)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED.svg?logo=docker&logoColor=white)](https://docs.docker.com/compose/)

[Getting Started](#getting-started) · [Features](#features) · [Architecture](#architecture) · [Documentation](#documentation) · [Contributing](#contributing)

</div>

---

Most teams use ~20 % of Jenkins' surface area. MegooCI re-implements that 20 % with a modern stack, opinionated defaults, a clean UI, first-class YAML pipelines, and a built-in AI assistant — delivering 80 %+ of the value with a fraction of the complexity and zero plugins to maintain.

## Features

### Core

- **Declarative YAML pipelines** — schema-validated `megooci.yaml` with stages, steps, conditional `when`, and matrix builds. Edited in-browser with a **CodeMirror 6** syntax-highlighted editor.
- **11 built-in step action types** — `run` (shell), `docker_build`, `docker_login`, `docker_push`, `git_clone`, `git_pull`, `git_push`, `ssh_exec`, `kube_apply`, `wait_webhook`, and `wait_input`. Template interpolation resolves `${{ secrets.NAME }}` and `${{ env.NAME }}` at runtime.
- **AI pipeline assistant** — chat-based interface that generates and refines YAML from natural-language prompts, with project-context awareness (available secrets / env vars) and one-click apply.
- **Remote build agents** — self-hosted `megooci-agent` Go binary (~15 MB static binary) connects to the controller over an authenticated WebSocket and runs steps on remote hosts. Falls back to local execution when no agent is online.
- **Embedded OCI / Docker registry** — OCI Distribution Spec v1.1 compliant. `docker login`, `push`, and `pull` work out of the box. Deploy tokens, anonymous pull, immutable tags, per-project quotas, and automatic garbage collection included.
- **Build artifacts** — collect outputs from pipeline stages via `artifacts.paths` glob patterns. Browse, download, and manage artifacts from a dedicated `/artifacts` page or per-build detail view. Configurable retention by age.

### Developer Experience

- **Modern UI** — cyberpunk-inspired design system built with Next.js 15, React 19, TypeScript, and Tailwind. Three-way theme (Light / Dark / System) with flash-free SSR.
- **Live log streaming** — terminal-style build log viewer with line numbers, timestamps, stderr coloring, auto-scroll, fullscreen mode, and in-log search (`Ctrl/Cmd+F`).
- **Visual stage graph** — status-aware colored stage visualization with clickable navigation and spin animation on running stages.
- **Global search** — Meilisearch-powered `Cmd/Ctrl+K` command palette with instant, typo-tolerant search across projects, pipelines, and builds.
- **PWA support** — installable as a Progressive Web App with service worker, manifest, and app icons.

### Operations & Security

- **Git provider integration** — GitHub / GitLab / generic Git with admin-scoped PAT connections, per-project repository linking, and manual-paste webhooks with HMAC verification. Pushes trigger builds automatically.
- **RBAC** — three system roles (admin, developer, viewer) with granular permissions (`projects`, `pipelines`, `builds`, `artifacts`, `secrets`, `agents`, `registry`), scoped user-role assignments, and permission-gated endpoints + UI.
- **Secrets & environment management** — Fernet-encrypted at rest, scoped at global / project / pipeline level, referenced by name (`${{ secrets.X }}`), never inlined. Visibility toggles and edit support in the UI.
- **User invitations** — admin-initiated invites with optional SMTP email delivery, configurable expiry, and role pre-assignment.
- **Long-lived sessions** — 12-hour access tokens with silent refresh up to 30 days.

### Planned

- Docker / SSH / Kubernetes executors (agent-side)
- Parallel stages, matrix expansion, parameterized builds
- Notifications (email / Slack / Teams / Discord)
- JUnit / coverage ingestion
- OIDC / SAML / LDAP / API tokens
- Scheduled (cron) pipeline triggers
- Image signing (cosign) + vulnerability scanning (Trivy)

## Getting Started

### Prerequisites

| Dependency | Version |
| --- | --- |
| Docker + Compose plugin | 24+ |
| Git | any |
| GNU Make *(recommended)* | any (`choco install make` or `scoop install make` on Windows) |

### 1. Clone and initialize

```bash
git clone https://github.com/megooci/megooci.git
cd megooci
make init          # copies .env.example → .env — edit secrets before proceeding
```

Edit `.env` and set at minimum:

- `MEGOOCI_SECRET_KEY` — master key for encrypting secrets at rest
- `MEGOOCI_JWT_SECRET` — JWT signing key
- `POSTGRES_PASSWORD` — database password

### 2. Start the stack

**Production**

```bash
make up            # docker compose up -d --build
make logs          # tail all container logs
make down          # stop (keeps volumes)
```

**Development (hot-reload)**

```bash
make dev           # foreground — Ctrl+C to stop
# or
make dev-up        # detached
make dev-logs
make dev-down
```

In dev mode the backend runs with `uvicorn --reload`, the frontend runs `next dev` with file polling (reliable on Windows / WSL), and Postgres + Redis are exposed on `localhost` for direct tool access.

### 3. Open the UI

| Service | URL |
| --- | --- |
| Web UI | [http://localhost:3000](http://localhost:3000) |
| API docs (OpenAPI / Swagger) | [http://localhost:8000/docs](http://localhost:8000/docs) |
| Health check | `GET http://localhost:8000/health` |

The **first user to sign up becomes admin** automatically.

## Pipeline Example

```yaml
version: 1
name: my-app

stages:
  - name: test
    steps:
      - run: npm test

  - name: build
    steps:
      - docker_build:
          context: "."
          dockerfile: Dockerfile
          tags:
            - "ghcr.io/org/app:${{ env.VERSION }}"
      - docker_push:
          tags:
            - "ghcr.io/org/app:${{ env.VERSION }}"
    artifacts:
      paths:
        - "dist/*"
        - "coverage/report.html"

  - name: deploy
    when:
      branch: main
    steps:
      - wait_input:
          prompt: "Deploy to production?"
          timeout: 86400
      - ssh_exec:
          host: deploy.example.com
          user: deploy
          private_key: ${{ secrets.SSH_KEY }}
          commands:
            - "cd /opt/app && docker compose pull"
            - "docker compose up -d"
```

To deploy to Kubernetes instead, store a kubeconfig as a secret and use `kube_apply` — it applies the manifests, then waits for every applied Deployment/StatefulSet/DaemonSet to finish rolling out:

```yaml
  - name: deploy
    when:
      branch: main
    steps:
      - name: deploy to production
        kube_apply:
          kubeconfig: ${{ secrets.PROD_KUBECONFIG }}
          manifests:
            - k8s/deployment.yaml
            - k8s/service.yaml
          namespace: production   # optional
          timeout: 300            # optional rollout wait in seconds (default 300)
```

Link the pipeline to a project whose repository points at your GitHub / GitLab repo — a push triggers the webhook and the pipeline runs on the next available agent.

## Running a Self-Hosted Build Agent

The `megooci-agent` is a single Go binary that connects to the controller over WebSocket.

```bash
# 1. Build the agent image
make agent-image

# 2. In the UI → Agents → Register agent → copy the one-shot token

# 3. Start the agent
make agent-up ID=<agent-uuid> TOKEN=megci_agt_...

# 4. Verify
make agent-logs
```

| Variable | Default | Description |
| --- | --- | --- |
| `ID` | — | **Required.** Agent UUID from the UI. |
| `TOKEN` | — | **Required.** Registration token. |
| `CONTROLLER` | `http://backend:8000` | Controller URL (use a public URL when running remotely). |
| `CAPACITY` | `2` | Max concurrent steps per agent. |
| `LOG_LEVEL` | `info` | `debug` / `info` / `warn` / `error` |
| `NETWORK` | `megooci_default` | Docker network name. |
| `NAME` | `megooci-agent` | Container name (useful for multi-agent hosts). |

Agent lifecycle:

```bash
make agent-logs      # tail logs
make agent-restart   # restart (preserves CLI args)
make agent-shell     # shell into the container
make agent-down      # stop and remove
```

For a host-only run without Docker (requires Go 1.22+):

```bash
cd agent && make build
./bin/megooci-agent run --controller ... --agent-id ... --token ...
```

See [`agent/README.md`](agent/README.md) for the full operator guide.

## Git Provider Integration

1. **Admin → Integrations** — register a GitHub / GitLab / generic Git connection (PAT auth).
2. **Project → Integrations tab** — link a repository, copy the webhook URL + secret.
3. **Paste into your provider** — MegooCI verifies each delivery with per-provider HMAC (GitHub `X-Hub-Signature-256`, GitLab `X-Gitlab-Token`, generic `X-MegooCI-Signature`).
4. Pushes trigger builds automatically for matching pipelines.

Delivery history is visible per repository with signature validation status and error details.

## Architecture

```
              ┌──────────────────┐
              │  Next.js 15 UI   │  React 19 · TypeScript · Tailwind
              └────────┬─────────┘
                       │ HTTPS + WebSocket
                       ▼
              ┌──────────────────┐
              │  FastAPI backend  │  /api/v1/* · /ws/* · /v2/* (OCI)
              └──┬───────┬────┬──┘
                 │       │    │
            ┌────▼──┐ ┌──▼──┐ ┌▼────────────┐
            │  PG   │ │Redis│ │  Local FS    │  MEGOOCI_STORAGE_ROOT
            └───────┘ └──┬──┘ └─────────────┘
                         │
                         │ Celery broker + pub/sub
                         ▼
              ┌──────────────────┐
              │  Celery worker   │  pipeline execution · registry GC
              └────────┬─────────┘
                       │ WebSocket control plane
                       ▼
              ┌──────────────────┐
              │  megooci-agent   │  Go binary on each build host
              └──────────────────┘
```

| Layer | Stack |
| --- | --- |
| **Frontend** | Next.js 15 (App Router), React 19, TypeScript, Tailwind 3, TanStack Query, Zustand, CodeMirror 6, Lucide icons |
| **Backend** | FastAPI, SQLAlchemy 2 (async), Pydantic v2, Celery + Beat, Alembic, Meilisearch. Python 3.12+ |
| **Agent** | Go 1.22+, Cobra CLI, gorilla/websocket, single static binary (~15 MB) |
| **Data** | PostgreSQL 16, Redis 7, local filesystem for artifacts / logs / registry blobs |

## Configuration

MegooCI reads all configuration from environment variables. See [`.env.example`](.env.example) for the full list. Key variables:

| Variable | Default | Description |
| --- | --- | --- |
| `MEGOOCI_SECRET_KEY` | — | Master key for Fernet-encrypting secrets and tokens. |
| `MEGOOCI_JWT_SECRET` | — | JWT signing key. |
| `MEGOOCI_JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `720` | Access-token lifetime (12 h). |
| `MEGOOCI_JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `30` | Refresh-token lifetime. |
| `MEGOOCI_SIGNUP_ENABLED` | `true` | Public signup toggle. |
| `MEGOOCI_STORAGE_ROOT` | `/var/lib/megooci` | Root folder for artifacts, logs, and registry blobs. |
| `MEGOOCI_PUBLIC_URL` | `http://localhost:3000` | Public URL of the frontend app (used in invite & password-reset email links). |
| `MEGOOCI_PUBLIC_API_URL` | `http://localhost:8000` | Externally-reachable backend API URL (webhooks, registry, artifact links, agent controller URL). |
| `MEGOOCI_AI_ENABLED` | `true` | Enable/disable the AI pipeline assistant. |
| `MEGOOCI_AI_PROVIDER` | `openai` | `openai`, `anthropic`, `ollama`, `azure_openai`, `custom`, or `disabled`. |
| `MEGOOCI_AI_API_KEY` | — | Provider API key (not needed for `ollama`). |
| `MEGOOCI_AI_MODEL` | provider default | Model identifier (e.g. `gpt-4o-mini`, `claude-sonnet-4-5`). |
| `MEGOOCI_AI_BASE_URL` | — | OpenAI-compatible API endpoint. Use `custom` provider + this URL for vLLM, LiteLLM, LM Studio, etc. All AI settings can also be changed at runtime from **Settings → AI Configuration** (admin only). |
| `MEGOOCI_REGISTRY_STORAGE_PATH` | — | Path for OCI registry blob storage. |
| `MEGOOCI_REGISTRY_MAX_UPLOAD_MB` | — | Max image layer upload size. |
| `MEGOOCI_ARTIFACT_RETENTION_DAYS` | `30` | Days to retain build artifacts before expiry. |
| `MEGOOCI_MEILISEARCH_URL` | — | Meilisearch connection URL for global search. |

## Host-Only Development (No Docker Stack)

Run Postgres + Redis in Docker and everything else on your host:

```bash
docker compose up db redis -d

# Backend
cd backend
python -m venv .venv
source .venv/bin/activate        # .venv\Scripts\Activate.ps1 on Windows
pip install -e .
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# Frontend (second terminal)
cd frontend
npm install
npm run dev
```

## Makefile Reference

Run `make` with no arguments for the full list. Highlights:

| Target | Description |
| --- | --- |
| `init` | Create `.env` from `.env.example`. |
| `up` / `down` / `logs` | Production stack lifecycle. |
| `dev` / `dev-up` / `dev-down` / `dev-logs` | Dev stack with hot-reload. |
| `shell` / `db-shell` / `redis-shell` | Open a shell in backend / Postgres / Redis. |
| `migrate` | Run `alembic upgrade head`. |
| `migration m="..."` | Generate a new Alembic migration. |
| `agent-image` | Build the agent Docker image. |
| `agent-up ID=.. TOKEN=..` | Start an agent container. |
| `agent-down` / `agent-restart` / `agent-logs` | Agent lifecycle. |
| `nuke CONFIRM=yes` | **Destructive** — drops all volumes (Postgres, Redis, storage). |

For Go-specific targets (`build`, `test`, `vet`, `fmt`, `tidy`, `snapshot`, `clean`), run `make <target>` inside `agent/` — see [`agent/Makefile`](agent/Makefile).

## Repository Layout

```
megooci/
├── agent/                  Go agent binary + Dockerfile
├── backend/                FastAPI app, Celery workers, Alembic migrations
│   ├── app/api/v1/         REST + WebSocket + OCI registry routers
│   ├── app/models/         SQLAlchemy models
│   ├── app/services/       build executor, agent dispatcher, git providers, …
│   └── alembic/versions/   database migrations
├── frontend/               Next.js 15 app
│   ├── src/app/            App Router pages
│   ├── src/components/     UI primitives + pipeline editor + AI panel
│   └── src/lib/            API client, hooks, stores
├── docs/                   PRD and documentation
├── docker-compose.yml      Production stack
├── docker-compose.dev.yml  Dev overrides (hot-reload, bind mounts)
├── Makefile                Operator entry point
└── .env.example            Configuration reference
```

## Documentation

- [Product Requirements Document](docs/prd.md) — full feature spec, architecture, and implementation status
- [Agent Operator Guide](agent/README.md) — detailed agent setup, configuration, and troubleshooting
- [API Reference](http://localhost:8000/docs) — auto-generated OpenAPI/Swagger docs (available when the backend is running)

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Run tests and linting
5. Submit a pull request

## License

[Apache-2.0](LICENSE)
