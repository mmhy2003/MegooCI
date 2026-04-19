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

### Running with Docker Compose

```bash
# Clone the repository
git clone https://github.com/megooci/megooci.git
cd megooci

# Copy environment file
cp .env.example .env

# Start all services
docker compose up -d

# The UI will be available at http://localhost:3000
# The API will be available at http://localhost:8000
# API docs at http://localhost:8000/docs
```

The first user to sign up automatically becomes an admin.

### Development Setup

#### Backend (FastAPI)

```bash
cd backend
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -e .
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

#### Frontend (Next.js)

```bash
cd frontend
npm install
npm run dev
```

#### Infrastructure

```bash
# Start just Postgres and Redis
docker compose up postgres redis -d
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
