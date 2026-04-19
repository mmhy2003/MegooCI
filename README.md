# MegooCI

A simpler, modern open-source alternative to Jenkins.

## Features

- **Declarative YAML pipelines** — schema-validated `megooci.yaml` files
- **Imperative Python pipelines** — full Python SDK for complex workflows
- **AI pipeline generator** — describe what you want, get a working pipeline
- **Modern UI** — built with Next.js, real-time log streaming, dark mode
- **Built-in container registry** — OCI-compliant, no external registry needed
- **Distributed execution** — Docker, SSH, and Kubernetes executors
- **Secrets & environment management** — encrypted at rest, scoped by project
- **RBAC & audit logs** — enterprise-ready access control

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Git

A `Makefile` is provided as the easiest entry point for both environments.
Run `make` with no arguments to see every available target.

### Running in Production (Docker Compose)

```bash
# Clone the repository
git clone https://github.com/megooci/megooci.git
cd megooci

# Copy environment file and edit the secrets
make init            # or: cp .env.example .env

# Build and start all services (production images, no bind mounts)
make up              # or: docker compose up -d --build

# The UI will be available at http://localhost:3000
# The API will be available at http://localhost:8000
# API docs at http://localhost:8000/docs

# Tail logs / stop the stack
make logs
make down
```

The first user to sign up automatically becomes an admin.

### Running in Development (hot-reload)

```bash
make dev             # foreground, shows logs; Ctrl+C to stop
# or
make dev-up          # background
make dev-logs        # tail logs
make dev-down        # stop
```

Under the hood, `make dev` runs:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

On Windows, install GNU Make via `choco install make` / `scoop install make`,
or run the raw `docker compose` commands shown above.

In dev mode:

- Backend runs `uvicorn --reload` with `./backend` bind-mounted.
- Frontend runs `next dev` with `./frontend` bind-mounted (polling watcher
  enabled for reliable hot-reload on Windows/macOS/WSL).
- Postgres is exposed on `localhost:5432`, Redis on `localhost:6379` so you
  can connect your IDE/CLI tools directly.
- Containers do not auto-restart, so `Ctrl+C` stops cleanly.

### Host-Only Development

If you prefer running the apps directly on your host and only using Docker
for Postgres + Redis:

```bash
# Start only the infrastructure services
docker compose up db redis -d

# Backend
cd backend
python -m venv .venv
source .venv/bin/activate       # or .venv\Scripts\Activate.ps1 on Windows
pip install -e .
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# Frontend (in a second terminal)
cd frontend
npm install
npm run dev
```

## Architecture

- **Frontend**: Next.js 15 (App Router), React 19, TypeScript, Tailwind CSS, shadcn/ui
- **Backend**: FastAPI, SQLAlchemy 2.x, Pydantic v2, Python 3.12+
- **Task Execution**: Celery with Redis broker
- **Database**: PostgreSQL 16+
- **Cache/Broker**: Redis 7+
- **Storage**: Local filesystem

## Configuration

MegooCI is configured via environment variables. See `.env.example` for all available options.

Key variables:

| Variable | Default | Description |
| --- | --- | --- |
| `MEGOOCI_SIGNUP_ENABLED` | `true` | Enable/disable public signup |
| `MEGOOCI_STORAGE_ROOT` | `/var/lib/megooci` | Root folder for artifacts and logs |
| `MEGOOCI_AI_ENABLED` | `true` | Enable AI pipeline generation |
| `MEGOOCI_AI_PROVIDER` | `openai` | AI provider (openai/anthropic/ollama) |
| `MEGOOCI_REGISTRY_ENABLED` | `true` | Enable built-in container registry |

## Pipeline Example (YAML)

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
      - upload_artifact:
          path: dist/
          name: build-output

  - name: deploy
    when:
      branch: main
    steps:
      - run: ./deploy.sh
```

## License

Apache-2.0
