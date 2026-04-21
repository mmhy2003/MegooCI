# MegooCI — Product Requirements Document (PRD)

| Field | Value |
| --- | --- |
| **Product Name** | MegooCI |
| **Tagline** | A simpler, modern open-source alternative to Jenkins |
| **Document Status** | Draft v1.5 |
| **Last Updated** | 2026-04-21 |
| **Owner** | MegooCI Core Team |
| **License (planned)** | Apache-2.0 (OSS) |

> **Change log**
>
> - **v1.5 (2026-04-21)** — Global search delivered (§6.17). Meilisearch added as an infrastructure dependency; backend indexes projects, pipelines, and builds on startup and incrementally on CRUD. New `GET /api/v1/search` endpoint with multi-index query. Frontend ships a fully-functional `Cmd/Ctrl+K` command palette with debounced search, keyboard navigation, and grouped results by entity type. Build log viewer gains in-log search (`Ctrl/Cmd+F`). UI/UX overhaul: cyberpunk-inspired color theme with neon-cyan/magenta palette, three-way theme toggle (Light / Dark / System) with OS-level tracking and flash-free SSR, custom hand-rolled UI primitives (badge variants, promise-based confirm dialog, dialog system, avatar with initials fallback), visual stage graph, terminal-style build log viewer with auto-scroll/follow/fullscreen/copy, collapsible sidebar with mobile drawer, dynamic breadcrumbs, and PWA support (service worker + web manifest). Updated §6.9, §6.14, §6.15, §9.1, §9.2.
> - **v1.4 (2026-04-20)** — Agent control plane delivered (F-3.4, F-3.7). New `agent/` Go module ships the `megooci-agent` binary (Cobra CLI, gorilla/websocket client, subprocess executor, heartbeat, reconnect, capacity semaphore, cancellation). Backend gains `/api/v1/ws/agents/{id}/connect` with bcrypt-hashed token auth, a Redis-queue dispatcher, `/rotate-token` endpoint, and a try-agent-first / fall-back-to-local execution strategy. Alembic migration `003_agent_tokens.py`. §6.15 status updated accordingly.
> - **v1.3 (2026-04-20)** — Added §6.16 "Git Provider Integration" (admin-scoped Git connections with PAT auth, per-project repository linking, manual-paste webhook receivers with HMAC verification for GitHub/GitLab/Generic, delivery log, webhook-triggered builds). Added `GitProviderConnection`, `ProjectRepository`, and `WebhookDelivery` to §10. Added OAuth and delivery-retention env vars to §6.14. Marked Phase 1 as delivered in §6.15.
> - **v1.2 (2026-04-20)** — Added §6.15 "Implementation Status Snapshot" reflecting a full-codebase audit of `backend/` and `frontend/`. No requirement changes; the feature tables above remain the target specification.
> - **v1.1 (2026-04-19)** — Added embedded OCI registry (§6.13), AI assistant details (§6.12), env-var model.

---

## 1. Executive Summary

MegooCI is an **open-source CI/CD automation server** that aims for **feature parity with Jenkins' most-used capabilities** while delivering a **dramatically simpler UI, configuration experience, and developer workflow**. It targets teams that love Jenkins' power but are frustrated by its dated interface, Groovy-heavy configuration, plugin sprawl, and steep learning curve.

MegooCI is explicitly **not a plugin platform**. Instead, it ships a curated, batteries-included feature set with two first-class ways to author pipelines: **declarative YAML** and **imperative Python**. An integrated **AI pipeline generator** turns natural-language descriptions into ready-to-run YAML or Python pipelines.

The product is built on a modern stack — **Next.js (frontend)**, **FastAPI (backend API)**, and **Celery (distributed task execution & scheduling)** — with PostgreSQL and Redis as core infrastructure, and the **local filesystem** as the default artifact and log store.

---

## 2. Problem Statement

Jenkins is the most widely deployed self-hosted CI/CD server, but users consistently report the following pain points:

1. **Outdated UI/UX.** The default UI (and even Blue Ocean) feels dated, inconsistent, and cluttered. Common tasks require many clicks and deep menu diving.
2. **Complex configuration.** Job configuration relies on a mixture of XML, Groovy DSL, Jenkinsfiles, and plugin-specific forms. Small mistakes produce cryptic errors.
3. **Plugin fragility.** The plugin ecosystem is powerful but brittle — version mismatches, abandoned plugins, and security CVEs are common.
4. **Heavyweight for small teams.** Running a production-grade Jenkins controller with agents is operationally expensive.
5. **Poor developer ergonomics.** Writing and debugging `Jenkinsfile` pipelines locally is painful; feedback loops are slow.
6. **Weak observability.** Build logs, metrics, and history are hard to navigate at scale.

**MegooCI's thesis:** most teams use ~20% of Jenkins' surface area. By re-implementing that 20% with a modern stack, opinionated defaults, a clean UI, first-class YAML *and* Python pipelines, and an AI assistant to author them, we can deliver 80%+ of the value with a fraction of the complexity — and without a plugin ecosystem to maintain.

---

## 3. Goals & Non-Goals

### 3.1 Goals

- **G1.** Deliver Jenkins-equivalent functionality for the most common CI/CD workloads: build, test, package, deploy.
- **G2.** Offer a **clean, modern, fast UI** built with Next.js and a cohesive design system.
- **G3.** Provide **two first-class pipeline authoring formats**: declarative **YAML** and imperative **Python**.
- **G4.** Ship an **AI pipeline generator** that produces YAML or Python pipelines from natural-language prompts and repo context.
- **G5.** Support **distributed build execution** across multiple agents/runners (Docker, Kubernetes, SSH, local).
- **G6.** Be **trivially installable** via a single Docker Compose file or Helm chart, with **local filesystem storage** working out of the box.
- **G7.** Be **configurable at runtime via environment variables** for key operational toggles (signup, auth providers, storage paths, AI providers, etc.).
- **G8.** Be **secure by default** — RBAC, secret management, audit logs, signed artifacts.

### 3.2 Non-Goals (v1 and beyond)

- **NG1.** 100% Jenkins plugin compatibility. We will not re-implement or load `.hpi` plugins.
- **NG2.** **A plugin / extension framework of any kind.** MegooCI is intentionally a closed, curated product. All functionality ships in-tree; integrations are first-party. Users extend behavior through pipeline steps (shell commands, scripts, containers) — not through server-side plugins.
- **NG3.** Groovy DSL support.
- **NG4.** Multi-tenant SaaS offering. v1 targets self-hosted single-tenant deployments.
- **NG5.** Replacing GitHub Actions / GitLab CI for teams already happy with those.
- **NG6.** Mobile-native app (responsive web only).
- **NG7.** Cloud object storage (S3/MinIO/GCS) as a primary artifact backend in v1. Local filesystem is the canonical store; cloud backends may be reconsidered post-1.0 based on demand.

---

## 4. Target Users & Personas

### 4.1 Persona: "Priya the Platform Engineer"
- Runs CI/CD for a 50–500 engineer org.
- Currently maintains a Jenkins controller and ~20 agents.
- Spends ~10 hrs/week on Jenkins plugin upgrades and flaky pipeline debugging.
- **Needs:** reliability, RBAC, audit logs, horizontal scaling, SSO, IaC-friendly config.

### 4.2 Persona: "Dan the Dev Team Lead"
- Leads a 5–10 person product team.
- Owns a handful of Jenkins jobs but doesn't administer the server.
- **Needs:** fast feedback, readable pipelines, clear build logs, easy rollbacks, Slack notifications.

### 4.3 Persona: "Sam the Solo/OSS Developer"
- Runs CI for a personal project or small open-source repo.
- Finds Jenkins overkill and GitHub Actions minutes limiting.
- **Needs:** one-command install, GitHub webhook integration, low resource footprint.

### 4.4 Persona: "Alex the Security Engineer"
- Audits CI/CD infrastructure for compliance (SOC 2, ISO 27001).
- **Needs:** audit trails, secret rotation, least-privilege RBAC, signed builds, reproducibility.

---

## 5. User Stories (High-Level)

- As **Dan**, I can create a new pipeline by connecting a Git repo in under 60 seconds.
- As **Dan**, I can see a live-streaming, color-highlighted log of my running build.
- As **Dan**, I can re-run a failed build with one click, with the same parameters.
- As **Priya**, I can register a new build agent by running a single `docker run` command.
- As **Priya**, I can define roles and permissions that map to SSO groups.
- As **Sam**, I can install MegooCI locally with `docker compose up` and see the UI at `localhost:8080`.
- As **Alex**, I can view an immutable audit log of every configuration change and every secret access.
- As a **pipeline author**, I can define stages, parallel steps, matrix builds, and conditional steps in a single YAML file **or** a Python file, whichever I prefer.
- As a **pipeline author**, I can store secrets and environment variables once and reference them by name, never by value.
- As a **pipeline author**, I can describe what I want in plain English and have the AI assistant generate a working YAML or Python pipeline I can review, edit, and commit.
- As an **administrator**, I can set `MEGOOCI_SIGNUP_ENABLED=false` in my environment and have the public signup page/API immediately disabled without restarting (or with a single restart), so I can lock down my instance at any time.
- As an **administrator**, I can configure the artifact storage root (e.g. `/var/lib/megooci/artifacts`) via an env variable and rely on simple filesystem backups — no object store required.

---

## 6. Product Scope & Features (v1)

Features are grouped into **Must-Have (M)** for v1.0, **Should-Have (S)** for v1.x, and **Could-Have (C)** for post-v1.

### 6.1 Pipelines & Jobs

| ID | Feature | Priority | Description |
| --- | --- | --- | --- |
| F-1.1 | Declarative **YAML** pipelines | M | Single `megooci.yaml` file, schema-validated, with stages, steps, matrix, parallel, when-conditions. |
| F-1.2 | Imperative **Python** pipelines | M | Single `megooci.py` file using the MegooCI Python SDK (`from megooci import pipeline, stage, step, …`). Executed in a sandboxed interpreter to produce the same internal build graph as YAML. |
| F-1.3 | Freestyle jobs | M | UI-driven single-command jobs for trivial cases. |
| F-1.4 | Multi-branch pipelines | M | Auto-discover branches and PRs from a Git repo; one pipeline config per branch. |
| F-1.5 | Parameterized builds | M | String, choice, boolean, password, file, and Git-ref parameters. |
| F-1.6 | Matrix / axis builds | M | Fan-out across OS × language-version × arch, etc. |
| F-1.7 | Parallel stages | M | Run independent stages concurrently within a pipeline. |
| F-1.8 | Conditional steps (`when`) | M | Run steps based on branch, tag, changed files, prior step status, env vars. |
| F-1.9 | Pipeline env variables | M | Define env vars at pipeline, stage, or step scope; support inheritance and overrides. |
| F-1.10 | Reusable pipeline templates | S | Shared includes / "starter" templates for both YAML and Python formats. |
| F-1.11 | Visual pipeline editor | S | Drag-and-drop UI that round-trips to YAML (Python is read-only in the visual editor). |
| F-1.12 | Pipeline-as-code validation CLI | M | `megooci lint path/to/file` validates both YAML and Python pipelines locally. |
| F-1.13 | YAML ↔ Python conversion | S | One-click "convert this pipeline to YAML/Python" in the UI, using the shared internal build-graph representation. |

### 6.2 Build Triggers

| ID | Feature | Priority |
| --- | --- | --- |
| F-2.1 | Manual trigger (UI + API) | M |
| F-2.2 | Git webhook triggers (GitHub, GitLab, Bitbucket, Gitea) | M |
| F-2.3 | Scheduled / cron triggers (via Celery Beat) | M |
| F-2.4 | SCM polling (fallback) | M |
| F-2.5 | Upstream/downstream job triggers | M |
| F-2.6 | Tag & release triggers | M |
| F-2.7 | External API trigger with signed tokens | S |

### 6.3 Execution: Agents & Runners

| ID | Feature | Priority |
| --- | --- | --- |
| F-3.1 | Local executor on controller | M |
| F-3.2 | Docker executor (run each step in a container) | M |
| F-3.3 | Remote SSH agent | M |
| F-3.4 | Dedicated agent binary (self-registering) | M |
| F-3.5 | Kubernetes executor (pod per build) | S |
| F-3.6 | Agent labels / selectors | M |
| F-3.7 | Concurrency limits per agent / per pipeline | M |
| F-3.8 | Auto-scaling agent pools | C |

### 6.4 SCM Integrations

| ID | Feature | Priority |
| --- | --- | --- |
| F-4.1 | GitHub (public + Enterprise) | M |
| F-4.2 | GitLab (SaaS + self-hosted) | M |
| F-4.3 | Bitbucket Cloud + Server | S |
| F-4.4 | Gitea / Forgejo | S |
| F-4.5 | Generic Git over HTTPS/SSH | M |
| F-4.6 | Commit status / check reporting back to SCM | M |
| F-4.7 | PR comment reporting | S |

### 6.5 Artifacts, Logs & Test Results

> **Storage policy:** MegooCI uses the **local filesystem** as its canonical and only storage backend in v1 for both artifacts and archived logs. The storage root is configured via the `MEGOOCI_STORAGE_ROOT` environment variable (default: `/var/lib/megooci`). Cloud/object-store backends (S3, MinIO, GCS) are **out of scope** for v1 (see NG7).

| ID | Feature | Priority |
| --- | --- | --- |
| F-5.1 | Live-streaming build logs (WebSocket) | M |
| F-5.2 | Persistent log storage on local disk with search | M |
| F-5.3 | Artifact upload/download to/from local filesystem | M |
| F-5.4 | Content-addressed layout (`<root>/artifacts/<pipeline_id>/<build_id>/...`) | M |
| F-5.5 | Artifact retention policies (per-pipeline: by count and by age) | M |
| F-5.6 | Disk-usage dashboard + quota per project | M |
| F-5.7 | JUnit / xUnit test result parsing & trend charts | M |
| F-5.8 | Code coverage report ingestion (Cobertura, LCOV) | S |
| F-5.9 | Artifact signing (Sigstore/cosign) | C |

### 6.6 Notifications & Integrations

| ID | Feature | Priority |
| --- | --- | --- |
| F-6.1 | Email notifications | M |
| F-6.2 | Slack / MS Teams / Discord | M |
| F-6.3 | Generic outgoing webhooks | M |
| F-6.4 | In-app notifications | M |

### 6.7 Users, Auth & RBAC

| ID | Feature | Priority |
| --- | --- | --- |
| F-7.1 | Local username/password auth | M |
| F-7.2 | **Runtime signup toggle via env var** (`MEGOOCI_SIGNUP_ENABLED=true/false`) — hides the signup UI, disables `POST /api/auth/signup`, and returns `403 SignupDisabled`. Admins can still invite users when signup is off. | M |
| F-7.3 | Admin-initiated user invites (email + one-time link) | M |
| F-7.4 | First-run bootstrap: first created account becomes admin; signup automatically disabled afterward unless explicitly re-enabled | M |
| F-7.5 | OAuth2/OIDC (GitHub, Google, Okta, generic) | M |
| F-7.6 | SAML 2.0 | S |
| F-7.7 | LDAP / AD | S |
| F-7.8 | Role-based access control (project/folder-scoped) | M |
| F-7.9 | API tokens per user | M |
| F-7.10 | Audit log (immutable, exportable) | M |

### 6.8 Secrets, Env Variables & Credentials

| ID | Feature | Priority |
| --- | --- | --- |
| F-8.1 | Encrypted-at-rest secret store (AES-256-GCM, envelope encryption) | M |
| F-8.2 | Scoped secrets & env vars (global → project/folder → pipeline → stage → step) with explicit override rules | M |
| F-8.3 | Secret types: text, username/password, SSH key, cert, file, token | M |
| F-8.4 | **Plain (non-secret) environment variables** managed independently from secrets, with the same scoping model | M |
| F-8.5 | Reference by name in pipelines (`${{ secrets.NAME }}` / `${{ env.NAME }}`); never inlined | M |
| F-8.6 | Automatic log masking of secret values | M |
| F-8.7 | Bulk import/export of env vars from `.env` files (secrets import only; export never reveals values) | S |
| F-8.8 | Integration with HashiCorp Vault | S |
| F-8.9 | Secret rotation reminders | C |

### 6.9 UI / UX

| ID | Feature | Priority |
| --- | --- | --- |
| F-9.1 | Dashboard: recent builds, favorites, system health | M |
| F-9.2 | Pipeline detail view with stage graph + live logs | M |
| F-9.3 | Project/folder hierarchy | M |
| F-9.4 | Global search (pipelines, builds, logs) | M |
| F-9.5 | Dark mode + accessible color palette (WCAG AA) | M |
| F-9.6 | Keyboard shortcuts | S |
| F-9.7 | Responsive layout (tablet + desktop) | M |
| F-9.8 | "Compare builds" diff view | S |

### 6.10 Observability & Admin

| ID | Feature | Priority |
| --- | --- | --- |
| F-10.1 | System health dashboard (queue depth, agent status) | M |
| F-10.2 | Prometheus `/metrics` endpoint | M |
| F-10.3 | Structured JSON logging | M |
| F-10.4 | OpenTelemetry tracing | S |
| F-10.5 | Backup / restore tooling | M |

### 6.11 Webhooks & Programmatic Integration

> MegooCI **does not have a plugin system.** Users extend behavior by running arbitrary commands, scripts, or containers inside pipeline steps, and by integrating via webhooks, the REST API, and the CLI.

| ID | Feature | Priority | Description |
| --- | --- | --- | --- |
| F-11.1 | **Incoming webhooks** — Git providers (GitHub, GitLab, Bitbucket, Gitea) and generic signed endpoints (`POST /api/webhooks/{slug}`) | M | Each pipeline can expose one or more webhook URLs with a per-endpoint secret, HMAC signature verification, and replay protection. |
| F-11.2 | **Outgoing webhooks** — configurable per pipeline/project for build lifecycle events (`started`, `succeeded`, `failed`, `cancelled`) | M | Payload is JSON; target URL is signed with HMAC; retries with exponential backoff. |
| F-11.3 | Public REST API + OpenAPI 3.1 docs | M | Covers every UI action; stable and versioned. |
| F-11.4 | CLI (`megooci`) for all common operations | M | Login, trigger builds, tail logs, lint pipelines, manage secrets/env, download artifacts. |
| F-11.5 | Python SDK used by Python pipelines **and** by external automation scripts | M | Shipped on PyPI as `megooci-sdk`. |

### 6.12 AI Pipeline Assistance

| ID | Feature | Priority | Description |
| --- | --- | --- | --- |
| F-12.1 | **AI-generated pipelines from natural language** | M | "Generate" button in the UI: user describes the project and goal (e.g., "Python FastAPI app, run pytest, build a Docker image, push to registry on `main`"); MegooCI produces a ready-to-run pipeline in the user's chosen format (YAML or Python). |
| F-12.2 | **Repository-aware generation** | M | When a Git repo is connected, the AI inspects the repo (package manifests, Dockerfile, test framework, languages) to produce an accurate starter pipeline. |
| F-12.3 | **Explain & edit in chat** | M | Conversational UI to iterate on the generated pipeline ("also add a nightly cron", "run tests in parallel across Python 3.11 and 3.12"). |
| F-12.4 | **Convert YAML ↔ Python via AI** | M | One-click conversion between the two authoring formats, using the AI to preserve comments and intent. |
| F-12.5 | **Fix-it suggestions on failed builds** | S | On failure, the AI reads recent log lines and suggests a concrete pipeline or code change; the user can apply with one click. |
| F-12.6 | **Pluggable AI provider via env vars** | M | `MEGOOCI_AI_PROVIDER` (`openai`, `anthropic`, `ollama`, `azure_openai`, `disabled`), plus provider-specific keys and model names. Self-hosted Ollama is a supported path for air-gapped environments. |
| F-12.7 | **AI feature kill-switch** | M | `MEGOOCI_AI_ENABLED=false` fully hides AI UI and disables AI endpoints. |
| F-12.8 | **Privacy controls** | M | Explicit per-project toggle for whether repo contents may be sent to external AI providers; default off for private projects when using a cloud provider. |
| F-12.9 | **Generated-content safety** | M | AI output is always shown as a diff for user review before being saved; never auto-committed. Generated pipelines are linted and dry-run validated before save. |

### 6.13 Built-in Container (Docker/OCI) Registry

MegooCI ships with an **embedded, OCI-compliant container registry** so that Docker images produced during a build can be **pushed from pipeline steps** and **pulled by external servers** (production hosts, staging VMs, Kubernetes clusters) without needing Docker Hub, GHCR, or a separate Harbor/Nexus deployment.

The registry implements the **OCI Distribution Spec v1.1** (superset of the Docker Registry HTTP API V2), stores image blobs and manifests as regular files under `MEGOOCI_STORAGE_ROOT/registry/`, and is served by the FastAPI controller on a dedicated, configurable path and/or port.

| ID | Feature | Priority | Description |
| --- | --- | --- | --- |
| F-13.1 | OCI Distribution Spec v1.1 / Docker Registry HTTP API V2 implementation | M | `/v2/` endpoint tree on the controller. Fully compatible with `docker`, `podman`, `buildah`, `crane`, `skopeo`, `containerd`, `helm`, and Kubernetes `imagePullSecrets`. |
| F-13.2 | Image storage on local filesystem | M | Blobs stored content-addressed (`sha256`) under `MEGOOCI_STORAGE_ROOT/registry/blobs/`; manifests under `.../manifests/`. Shared blob deduplication across repos. |
| F-13.3 | Namespacing: `<controller_host>/<project_slug>/<repo_name>:<tag>` | M | Each MegooCI project maps to a registry namespace; pipelines can only push to their own project's namespace by default. |
| F-13.4 | Push from pipeline steps | M | First-class `docker_push` pipeline step in both YAML and Python; automatic authentication using a short-lived build-scoped token; no need to put registry credentials in user secrets. |
| F-13.5 | **Pull from external servers** | M | Any server with Docker/Podman can `docker login <controller_host>` using a MegooCI **deploy token** or a user's API token, then `docker pull` images produced by any build the token has access to. |
| F-13.6 | Deploy tokens (pull-only or pull+push) | M | Per-project or per-repo, revocable, with expiry; shown once at creation. Designed for servers and CI/CD of other systems. |
| F-13.7 | Anonymous pull (opt-in per project) | S | Public projects can allow unauthenticated pulls for OSS use cases. |
| F-13.8 | Multi-arch / manifest lists | M | Support `linux/amd64`, `linux/arm64`, and arbitrary OCI manifest lists. |
| F-13.9 | Tag & digest semantics | M | Mutable tags + immutable digests; optional "immutable tags" per repo to prevent overwrites. |
| F-13.10 | Retention policies tied to builds | M | When a build is garbage-collected per artifact retention rules (F-5.5), its pushed images are also removed unless retained by a tag policy (e.g. "keep latest 10 semver tags", "keep `main` forever"). |
| F-13.11 | Garbage collection of unreferenced blobs | M | Scheduled Celery Beat task; safe, two-phase mark-and-sweep; per-registry disk-usage dashboard. |
| F-13.12 | Per-image UI page | M | Browse repos, tags, layers, size, digest, build provenance link ("pushed by build #142 of pipeline X"), pull command snippet, and "latest pulls" activity log. |
| F-13.13 | Automatic build → image provenance | M | Every pushed image is linked back to the exact build + commit that produced it in the MegooCI DB; shown as metadata on the image and on the build detail page. |
| F-13.14 | Image signing (cosign / Sigstore) | S | Optional signing of pushed images; verification policy per project. |
| F-13.15 | Vulnerability scanning of pushed images (Trivy) | S | Run async on push; show CVE report in the UI; configurable fail-the-build thresholds. |
| F-13.16 | Webhook events for registry operations | S | `image.pushed`, `image.pulled`, `image.deleted` fire the standard outgoing webhook pipeline (F-11.2). |
| F-13.17 | Runtime toggle: `MEGOOCI_REGISTRY_ENABLED` | M | Admins can disable the embedded registry (e.g., if they prefer an external one). All registry endpoints return 404 when disabled. |
| F-13.18 | Optional proxy cache for external registries | C | Cache `docker.io`, `ghcr.io`, etc. through MegooCI to speed up repeated pulls on the build fleet. |
| F-13.19 | Registry quota per project | M | Max storage in GB per project; new pushes refused with a clear error when exceeded. |
| F-13.20 | TLS requirement | M | Registry must be served over HTTPS in production (Docker clients require it except for `localhost`). Helm chart and Compose defaults include a TLS-terminating ingress. |

> **Non-registry clients.** Because the implementation is the standard OCI Distribution API, MegooCI is a drop-in destination for any tool that speaks that protocol — including `helm push` (OCI charts), language build tools that produce OCI artifacts, and tools like `oras` for arbitrary OCI artifact types.

#### 6.13.1 Pipeline usage examples

YAML (`megooci.yaml`):

```yaml
- name: package
  steps:
    - docker_build:
        context: .
        file: Dockerfile
        tags:
          - "${{ megooci.registry }}/${{ project.slug }}/web:${{ build.commit_short }}"
          - "${{ megooci.registry }}/${{ project.slug }}/web:latest"
    - docker_push:
        tags:
          - "${{ megooci.registry }}/${{ project.slug }}/web:${{ build.commit_short }}"
          - "${{ megooci.registry }}/${{ project.slug }}/web:latest"
```

Python (`megooci.py`):

```python
from megooci import Step, registry

image = f"{registry.host}/{project.slug}/web"
pipeline.add(Stage("package", steps=[
    Step.docker_build(
        context=".",
        file="Dockerfile",
        tags=[f"{image}:{build.commit_short}", f"{image}:latest"],
    ),
    Step.docker_push(tags=[f"{image}:{build.commit_short}", f"{image}:latest"]),
]))
```

From an external server (e.g., production host):

```bash
# One-time
docker login registry.megoo.example.com -u deploy-token -p <TOKEN>

# On each deploy
docker pull registry.megoo.example.com/acme-web/web:stable
docker run -d registry.megoo.example.com/acme-web/web:stable
```

### 6.14 Runtime Configuration (Environment Variables)

All operational toggles are driven by environment variables so administrators can change behavior by editing a single `.env` file (or Kubernetes ConfigMap/Secret) and restarting the controller. A non-exhaustive list:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MEGOOCI_SIGNUP_ENABLED` | `false` after first admin is created | Enable/disable public signup at any moment. |
| `MEGOOCI_DEFAULT_ROLE` | `viewer` | Role assigned to users who sign up. |
| `MEGOOCI_STORAGE_ROOT` | `/var/lib/megooci` | Root folder for artifacts, archived logs, tmp. |
| `MEGOOCI_ARTIFACT_RETENTION_BUILDS` | `50` | Default per-pipeline retention count. |
| `MEGOOCI_ARTIFACT_RETENTION_DAYS` | `30` | Default per-pipeline retention age. |
| `MEGOOCI_DATABASE_URL` | — | PostgreSQL DSN. |
| `MEGOOCI_REDIS_URL` | — | Redis DSN (Celery broker + pub/sub). |
| `MEGOOCI_SECRET_KEY` | — | Master key for envelope-encrypting secrets. |
| `MEGOOCI_JWT_SECRET` | — | JWT signing key. |
| `MEGOOCI_OIDC_*` | — | OIDC provider config. |
| `MEGOOCI_AI_ENABLED` | `true` | Master toggle for AI features. |
| `MEGOOCI_AI_PROVIDER` | `openai` | `openai` \| `anthropic` \| `ollama` \| `azure_openai` \| `disabled`. |
| `MEGOOCI_AI_API_KEY` | — | Provider API key (unused for `ollama`). |
| `MEGOOCI_AI_MODEL` | provider default | Model identifier. |
| `MEGOOCI_AI_BASE_URL` | — | Custom base URL (e.g., for Ollama, Azure). |
| `MEGOOCI_PYTHON_PIPELINE_TIMEOUT_SECONDS` | `10` | Max time a Python pipeline definition is allowed to run during graph construction. |
| `MEGOOCI_LOG_LEVEL` | `INFO` | Global log level. |
| `MEGOOCI_PUBLIC_URL` | — | Externally reachable base URL (used for webhooks, emails). |
| `MEGOOCI_WEBHOOK_SIGNATURE_HEADER` | `X-MegooCI-Signature` | Header name for outgoing webhook HMAC. |
| `MEGOOCI_REGISTRY_ENABLED` | `true` | Enable the embedded OCI/Docker registry. |
| `MEGOOCI_REGISTRY_HOST` | value of `MEGOOCI_PUBLIC_URL` host | Hostname advertised in image references (e.g., `registry.megoo.example.com`). |
| `MEGOOCI_REGISTRY_PORT` | same as controller | Dedicated port for the registry, if separated from the main API. |
| `MEGOOCI_REGISTRY_STORAGE_PATH` | `${MEGOOCI_STORAGE_ROOT}/registry` | Root directory for registry blobs and manifests. |
| `MEGOOCI_REGISTRY_MAX_UPLOAD_MB` | `2048` | Per-layer upload size limit. |
| `MEGOOCI_REGISTRY_ALLOW_ANONYMOUS_PULL` | `false` | Allow unauthenticated pulls for projects that opt in (F-13.7). |
| `MEGOOCI_REGISTRY_GC_CRON` | `0 3 * * *` | Cron expression for the unreferenced-blob garbage collector. |
| `MEGOOCI_MEILISEARCH_URL` | `http://localhost:7700` | Meilisearch instance URL. The backend connects on startup to sync indexes. If unreachable, search is degraded but the server starts normally. |
| `MEGOOCI_MEILISEARCH_API_KEY` | `megooci-meili-master-key` | Meilisearch API key. Must match the `MEILI_MASTER_KEY` configured on the Meilisearch service. |
| `MEGOOCI_GITHUB_OAUTH_CLIENT_ID` | — | GitHub OAuth app Client ID (used by §6.16 Phase 2). Empty disables OAuth for GitHub. |
| `MEGOOCI_GITHUB_OAUTH_CLIENT_SECRET` | — | GitHub OAuth app Client Secret (Phase 2). |
| `MEGOOCI_GITLAB_OAUTH_CLIENT_ID` | — | GitLab OAuth application ID (Phase 2). |
| `MEGOOCI_GITLAB_OAUTH_CLIENT_SECRET` | — | GitLab OAuth application secret (Phase 2). |
| `MEGOOCI_WEBHOOK_DELIVERY_RETENTION` | `200` | Max `WebhookDelivery` rows kept per linked repository; older rows pruned on insert. |

### 6.15 Implementation Status Snapshot (as of 2026-04-21)

This section documents the **actual state of the codebase** at the date above, from a full audit of `backend/` and `frontend/`. It is advisory — the feature tables in §6.1–§6.18 remain the target specification, and §14 (Release Plan) remains the roadmap. When there's a conflict between this snapshot and the requirement tables, the requirement tables win.

Legend: ✅ Implemented · 🟡 Partial (model, UI scaffolding, or config-only; key paths missing) · ❌ Missing

#### 6.15.1 Backend (`backend/`)

**Stack** — ✅ FastAPI + SQLAlchemy 2 (async) + Pydantic v2 + Celery (Redis broker) + Alembic + `python-jose` + `bcrypt` + `PyYAML` + `meilisearch-python-sdk`. Python 3.12. Versions in `pyproject.toml` are mostly unpinned. **No AI SDKs** and **no OCI/registry libraries** are listed as dependencies.

**API routers under `/api/v1/`:** `auth`, `projects`, `pipelines`, `builds`, `secrets-env`, `agents`, `system`, `websocket`, `search`. Plus `GET /health` on the root app.

**Data model (`backend/app/models/`):** `User`, `Project`, `Pipeline`, `Trigger`, `WebhookEndpoint`, `Build`, `Stage`, `Step`, `LogChunk`, `Artifact`, `Agent`, `Secret`, `EnvVar`, `AuditLogEntry`. **Missing** vs §10: `Role`, `UserRole`, `Invite`, `OutgoingWebhook`, `ContainerRepository`, `ContainerImage`, `ContainerTag`, `RegistryDeployToken`, `RegistryEvent`, `AiConversation`, `AiMessage`.

| Area | PRD ref | Status | Notes |
| --- | --- | --- | --- |
| Local auth (email/password), JWT access + refresh, `/me` | F-7.1 | ✅ | `bcrypt` hashing; `python-jose` JWT. |
| `MEGOOCI_SIGNUP_ENABLED` runtime gate + first-user becomes admin | F-7.2, F-7.4 | 🟡 | Flag enforced. **Bug:** the "auto-disable signup after first admin" path mutates the in-memory `Settings` object only — not persisted, so signup re-enables on every restart. |
| OIDC / OAuth2 · SAML · LDAP | F-7.5, F-7.6, F-7.7 | ❌ | Not implemented. |
| API tokens / PATs per user | F-7.9 | ❌ | Only JWT access + refresh exist. |
| Admin-initiated invites | F-7.3 | ❌ | No model, no endpoints. |
| RBAC (Role / UserRole, scoped permissions) | F-7.8 | ❌ | No tables; `User.is_admin` boolean is the only authorization primitive. |
| Projects / Pipelines / Builds / Stages / Steps CRUD | F-1.\* | ✅ | With `definition_format` (`yaml` / `python`) and `yaml_content`. |
| YAML pipeline parser, compiler, validator | F-1.1 | 🟡 | `services/pipeline_compiler.py` has `parse_yaml_pipeline`, `compile_to_build_graph`, and `validate_pipeline`. **Validation is not called** on pipeline create/update; it runs implicitly at `POST /builds/{pipeline_id}/trigger`. |
| Imperative Python pipelines + sandbox | F-1.2 | ❌ | No SDK, no sandboxed subprocess. `MEGOOCI_PYTHON_PIPELINE_TIMEOUT_SECONDS` is defined in config but unused. |
| Freestyle jobs · Multi-branch · Parameterized · Matrix | F-1.3 – F-1.6 | ❌ | None of these are wired into the trigger or executor. |
| Parallel stages, conditional `when`, matrix/axis | F-1.6 – F-1.8 | ❌ | Executor runs stages strictly sequentially; `when` is not evaluated; no matrix expansion. |
| Pipeline env variable scoping & inheritance | F-1.9 | ❌ | Secrets/env vars are not loaded by the executor at build time. |
| Local executor (shell on controller) | F-3.1 | ✅ | `services/build_executor.py` invokes `asyncio.create_subprocess_shell(step.command)` as a fallback when no agent is online. Used unchanged for back-compat. |
| Docker / SSH / Kubernetes executors | F-3.2, F-3.3, F-3.5 | ❌ | Agent-side `executor.Executor` interface is in place for future implementations; only `Local` ships today. |
| **Agent control plane** (Go binary + WebSocket + token auth + dispatcher) | F-3.4 | ✅ | `agent/` module: `megooci-agent` Go binary with Cobra CLI, gorilla/websocket client, subprocess executor, heartbeat, reconnect, cancellation. Backend: `agents_ws.py` WS endpoint, `core/agent_auth.py` bcrypt token auth, `services/agent_dispatcher.py` Redis queue + pub/sub, `build_executor.py` dispatches to a connected agent when available and falls back to local otherwise. Alembic migration `003_agent_tokens.py` persists bcrypt-hashed tokens + 12-char prefix + issued-at. Dockerfile + GoReleaser config included. |
| Agent capacity limits | F-3.7 | ✅ | Enforced client-side via a buffered-channel semaphore in the Go agent (`internal/executor/local.go`). |
| Agent labels / label-based scheduling | F-3.6 | 🟡 | `Agent.labels` is persisted and shown in the UI; Phase-1 dispatcher picks the least-recently-used online agent without consulting labels. Label matching is the next scheduler improvement. |
| Live build logs via WebSocket | F-5.1 | 🟡 | `WS /api/v1/ws/builds/{id}/logs` bridges Redis pub/sub channel `build:{id}:logs`. **No auth on the socket.** |
| Archived log storage on disk | F-5.2 | 🟡 | Logs are written to `LogChunk` rows in Postgres. No flush to `MEGOOCI_STORAGE_ROOT/logs/...` yet. |
| Artifact upload/download · retention · quotas | F-5.3 – F-5.6 | 🟡 | `Artifact` model + migration exist; no API, no upload/download flow, executor never creates rows. |
| JUnit / coverage ingestion | F-5.7, F-5.8 | ❌ | Not implemented. |
| Secret store (Fernet-encrypted, scoped) + types | F-8.1, F-8.2, F-8.3 | ✅ | `core/security.py` uses Fernet keyed from `MEGOOCI_SECRET_KEY`. **Note:** Fernet is AES-128-CBC + HMAC-SHA256, **not AES-256-GCM as specified in F-8.1**. Scoping via `scope_type` / `scope_id`; `secret_type` defaults to `"text"` but is not enum-validated. |
| Env vars (non-secret) | F-8.4 | ✅ | Separate `EnvVar` model; values stored **plaintext** (consistent with F-8.4). |
| Reference by name in pipelines (`${{ secrets.X }}`) | F-8.5 | ❌ | No template interpolation; executor never loads secret/env values. |
| Automatic log masking of secret values | F-8.6 | ❌ | Not implemented. |
| Incoming Git webhooks (GitHub / GitLab / Generic) with HMAC | F-2.2, F-11.1, F-16.\* | ✅ | `POST /api/v1/webhooks/git/{slug}` in `api/v1/webhooks_git.py`; per-provider verification in `services/git_providers.py`; replay protection via `UNIQUE(project_repository_id, provider_delivery_id)`. Legacy pipeline-scoped `WebhookEndpoint` table remains unused. |
| Admin-scoped Git provider connections + per-project repo links | F-16.1 – F-16.14 | ✅ | Models `GitProviderConnection`, `ProjectRepository`, `WebhookDelivery` + Alembic `002_git_integration.py`; admin routes `/api/v1/git/connections` and project routes `/api/v1/projects/{id}/repositories`. PAT only; OAuth deferred to Phase 2. |
| Outgoing webhooks with HMAC + retries | F-11.2 | ❌ | No model, no delivery service. |
| Cron / scheduled triggers | F-2.3 | ❌ | Celery Beat is wired up (`beat_schedule_filename` set in `celery_app.py`) but `beat_schedule` is empty — no periodic tasks registered. |
| SCM polling · upstream/downstream · tag/release triggers | F-2.4, F-2.5, F-2.6 | ❌ | None. |
| `Trigger` model (storage) | — | 🟡 | Exists; no API and no evaluator. |
| Notifications (email / Slack / Teams / Discord / generic webhook) | F-6.\* | ❌ | No config, no sender, no templates. |
| AI provider adapter + streaming chat endpoints | F-12.\* | 🟡 | Env vars defined (`MEGOOCI_AI_*`) and surfaced via `GET /system/info.ai` with derived readiness strings. **No chat/completion endpoints**, no provider clients, no prompt templates. |
| Embedded OCI/Docker registry (`/v2/...`) | F-13.\* | ❌ | No `/v2/...` routes; no `ContainerRepository` / `ContainerImage` / `ContainerTag` / `RegistryDeployToken` models. `MEGOOCI_REGISTRY_ENABLED` / `MEGOOCI_REGISTRY_HOST` are only reflected in `/system/info` for the UI. |
| Audit log storage | F-7.10 | 🟡 | `AuditLogEntry` table exists; **no writer code** anywhere in the request pipeline. |
| System info (config snapshot) | — | ✅ | `GET /api/v1/system/info` returns `SystemInfo` with AI / storage / auth / registry blocks. |
| **Global search (Meilisearch)** | F-9.4, F-17.\* | ✅ | `meilisearch-python-sdk` async client. Three indexes (`projects`, `pipelines`, `builds`) with per-index searchable/filterable/sortable attributes. Bulk sync on startup (up to 500 recent builds); incremental `index_*` / `remove_*` helpers called from CRUD paths (fire-and-forget). `GET /api/v1/search?q=&limit=` multi-index endpoint. Graceful degradation: if Meilisearch is down at startup, search is unavailable but the server runs. |
| Prometheus `/metrics` | F-10.2 | ❌ | No metrics endpoint. |
| Structured JSON logging | F-10.3 | ❌ | Standard uvicorn logging. `MEGOOCI_LOG_LEVEL` is exposed but not applied to logger config. |
| Backup / restore tooling | F-10.5 | ❌ | Not present. |
| Alembic migrations | — | ✅ | Revision `001_initial_schema.py` present. **Redundancy:** `database.init_db()` also calls `Base.metadata.create_all()` on startup, overlapping with migrations. |

#### 6.15.2 Frontend (`frontend/`)

**Stack** — ✅ Next.js 15 (App Router) + React 19 + TypeScript + Tailwind 3 + TanStack Query 5 + Zustand 5 + `sonner` + `lucide-react` + `class-variance-authority` + custom UI primitives. **No Monaco editor**, **no charting library**, **no Radix UI packages** (UI primitives are hand-rolled to match the shadcn look). Custom `CommandPalette` replaces any need for `cmdk`.

**Pages implemented:** `/`, `/login`, `/signup`, `/dashboard`, `/pipelines`, `/pipelines/new`, `/pipelines/[id]`, `/projects`, `/projects/[id]`, `/builds`, `/builds/[id]`, `/agents`, `/secrets`, `/settings`.

**UI primitives (`src/components/ui/`):** `avatar`, `badge`, `button`, `card`, `confirm-dialog`, `dialog`, `dropdown-menu`, `input`, `scroll-area`, `select`, `separator`, `skeleton`, `textarea`.

| Area | PRD ref | Status | Notes |
| --- | --- | --- | --- |
| **App shell — collapsible sidebar + mobile drawer** | F-9.7, F-18.7, F-18.8 | ✅ | Desktop: collapsible sidebar with state persisted to `localStorage`. Mobile: full-width off-canvas drawer with dark backdrop overlay and body scroll lock. 8 nav items (Dashboard, Pipelines, Projects, Builds, Agents, Secrets, Integrations [admin-only], Settings). User section at bottom with avatar dropdown (profile, theme cycle) and one-click logout with confirmation. Route change closes drawer. Dynamic breadcrumbs in header (full trail on desktop, page title + hamburger on mobile). |
| Dashboard — stat cards + recent builds table | F-9.1 | ✅ | Cards: total pipelines, total builds, success rate, active agents. Table columns hide progressively on smaller viewports. |
| Pipeline listing, detail, creation, edit | F-1.1 | ✅ | Detail has Overview / Builds / Configuration tabs; trigger build; delete with in-app confirm. |
| YAML / Python pipeline editor | F-1.1, F-1.2 | 🟡 | Plain `<textarea>`, not Monaco. Radio toggle YAML / Python on **create** only; both formats persist into the single `yaml_content` field, and the detail editor does not change UX by format. |
| Builds list + detail with stage graph + live logs + re-run / cancel | F-9.2, F-5.1, F-18.5, F-18.6 | ✅ | `StageGraph` (status-aware colored buttons with arrow connectors, clickable with ring highlight, spin animation on running stages) + `BuildLogViewer` (terminal-dark theme `#0d1117`, line numbers, timestamps, stderr red coloring, auto-scroll/follow, fullscreen toggle, copy-all, **in-log search `Ctrl/Cmd+F`** with yellow `<mark>` highlighting); WebSocket via `useWebSocket` hook with auto-reconnect every 3 s. **WS URL is hardcoded to `ws://<hostname>:8000/ws/...`** instead of using the Next.js rewrite proxy. |
| Projects listing / detail with secrets + env vars tabs | F-9.3 | ✅ | Scoped secrets + env vars CRUD in project settings tab. |
| Agents listing + admin-only registration + one-time token card | F-3.4 | ✅ | 15-second polling; destructive actions use `useConfirm`. |
| Secrets / env vars global page | F-8.\* | ✅ | `/secrets` aggregates per project with add + delete. `envVarsApi.update` exists but has **no UI** surface (values can only be deleted + re-created). |
| Settings page mirror of `GET /system/info` (profile, AI, auth, storage, registry) | — | ✅ | Read-only; registry block is purely informational. |
| **Promise-based confirmation dialogs** (replaces `window.confirm`) | UX principle 9, F-18.4 | ✅ | `ConfirmProvider` + `useConfirm` hook. 4 tones: `default`, `destructive`, `warning`, `success`, each with distinct icon, icon background color, and button variant. Backdrop blur, scale/translate entry animation, body scroll lock, keyboard handling (Esc/Enter), auto-focus on confirm button. Zero remaining `window.confirm` calls. |
| Signup page gated by `signup_enabled` | F-7.2 | 🟡 | Backend flag is **displayed** on Settings but not yet used to conditionally hide `/signup` or the "Create one" link on `/login`. |
| **Cyberpunk design system** | F-18.1 | ✅ | Full cyberpunk-inspired color theme. Light: lavender surfaces, teal-cyan primary, magenta accents. Dark: deep violet-black surfaces, neon cyan primary, hot magenta destructive. Custom CSS variables for all semantic tokens in `globals.css`. |
| **Three-way theme toggle (Light / Dark / System)** | F-9.5, F-18.2 | ✅ | Custom `ThemeProvider` (not `next-themes`) persists to `localStorage` under `megooci_theme`. System mode tracks `prefers-color-scheme` and auto-updates. Two variants: `segmented` (settings pages) and `icon` (header). Flash-free SSR via inline script in `layout.tsx`. |
| **Global search / `⌘K` command palette** | F-9.4, F-9.6, F-17.6 | ✅ | `CommandPalette` component triggered by `Cmd/Ctrl+K` or header search bar click. 200 ms debounced Meilisearch query via `GET /api/v1/search`. Results grouped by type (Project/Pipeline/Build) with type-specific icons and colors. Full keyboard navigation (↑↓ to move, Enter to open, Esc to close). Desktop header shows a styled search input with "Cmd+K" hint; mobile shows a compact icon button. |
| Notifications UI | F-6.4 | ❌ | Header bell icon has no dropdown; no notification center, no Slack/email config UI. |
| "New Build" header quick action | — | ❌ | Button exists but has no `onClick`. |
| AI chat panel / "Generate with AI" / fix-it suggestions | F-12.\* | ❌ | No AI UI at all; Settings only reflects backend readiness. |
| Container registry UI (image browser, tags, pull snippets) | F-13.12 | ❌ | Only read-only status in Settings. |
| Artifact browser / downloads | F-5.3 | ❌ | No UI. |
| JUnit / coverage results view | F-5.7, F-5.8 | ❌ | No UI. |
| **Semantic badge variants** | F-18.3 | ✅ | `class-variance-authority`-driven badges: `success`, `failed`, `running` (`animate-pulse-slow`), `pending`, `cancelled`, `default`, `secondary`, `destructive`, `outline`. |
| **Custom dialog system** | F-18.9 | ✅ | Fully custom (no Radix). Controlled/uncontrolled modes. Mobile-first bottom-sheet on small screens, centered on SM+. Escape key + backdrop click to close. |
| **Avatar with initials fallback** | F-18.10 | ✅ | Generates initials from name/email. `sm`/`md`/`lg` sizes with image error fallback. |
| **PWA support** | F-18.11 | ✅ | Service worker registration in production. Web manifest, Apple web app config, theme colors, viewport fit cover, app icons in multiple sizes. |
| Visual pipeline editor (drag-and-drop) | F-1.11 | ❌ | Deferred (PRD priority S). |
| Compare builds diff view | F-9.8 | ❌ | Deferred (PRD priority S). |

#### 6.15.3 End-to-end capability today

- ✅ A user can sign up, log in, create a project, create a YAML pipeline, trigger a build, and watch live logs stream from a shell-executed build.
- ✅ An admin can register an agent, copy its registration token, and run the `megooci-agent` Go binary on a build host. Subsequent builds are dispatched to that agent (WebSocket control channel + bcrypt-hashed tokens + Redis dispatch queue); if no agent is online the controller falls back to running steps in-process.
- ✅ Operators can view current backend configuration (AI, auth, storage, registry) via the Settings page.
- ✅ **Global search** works end-to-end: Meilisearch indexes are synced on startup, `Cmd/Ctrl+K` opens the command palette, and users can search across projects, pipelines, and builds with instant, typo-tolerant results and keyboard navigation.
- ✅ **Design system** is cohesive: cyberpunk theme with Light / Dark / System modes, semantic badges, promise-based confirm dialogs, visual stage graph, terminal-style build log viewer with in-log search, collapsible sidebar with mobile drawer, dynamic breadcrumbs, and PWA support.
- 🟡 Cancellation, retry, and live log streaming all work. A cancel on a running build now also signals any agent executing a step; the build log WebSocket to the browser remains unauthenticated.
- ❌ Docker / SSH / K8s executors, artifact flow, notifications, and registry are still unbuilt.

#### 6.15.4 Largest gaps vs. the specification (priority-ordered for upcoming work)

1. **Pipeline runtime fidelity.** Parallel stages (F-1.7), conditional `when` (F-1.8), matrix (F-1.6), parameters (F-1.5), and **secret/env injection** into the executor (F-8.5, F-1.9). The agent side is ready to receive richer step descriptors; the controller's compiler output needs to catch up.
2. **Alternative executors on the agent** — Docker (F-3.2), SSH (F-3.3), Kubernetes (F-3.5). The agent exposes an `executor.Executor` interface; only `Local` ships today.
3. **Label-based agent scheduling** (F-3.6). Labels are persisted and displayed; the dispatcher currently ignores them when selecting an online agent.
4. **Outgoing webhooks** (F-11.2). Incoming Git webhooks are implemented (§6.16); outgoing webhooks with HMAC + retries are not.
5. **Scheduled triggers** via Celery Beat (F-2.3). Beat is wired up; the schedule is empty.
6. **Artifacts + test results** (F-5.3 – F-5.8). No endpoints, no on-disk layout, no UI.
7. **Embedded OCI/Docker registry** (F-13.\*). Entirely unbuilt and the largest net-new feature in the PRD.
8. **AI assistant** (F-12.\*). Config-only today; needs provider adapter, streaming chat endpoint, and the "Generate with AI" UI flow.
9. **Audit-log writers.** Table exists; no handler records events (F-7.10).
10. **Notifications** (F-6.\*). No config, no sender, no templates, no notification center UI. Header bell icon has no dropdown.
11. **Observability** — `/metrics` (F-10.2) and structured JSON logging (F-10.3).
12. **Enterprise auth** — OIDC (F-7.5), SAML/LDAP (F-7.6/7), API tokens (F-7.9), invites (F-7.3), RBAC (F-7.8), and a **durable** signup-disable mechanism that survives restarts (F-7.2/4).

> **Closed since last snapshot:** Global search / command palette (F-9.4) is now fully functional (§6.17). Dark mode has been upgraded to a three-way theme toggle (F-9.5, F-18.2). All `window.confirm` calls replaced with promise-based dialogs (F-18.4). PWA support shipped (F-18.11).

### 6.16 Git Provider Integration

MegooCI lets administrators register **Git provider connections** (GitHub SaaS or Enterprise Server, GitLab SaaS or self-hosted, or any generic Git host) once, and lets project owners **link specific repositories** to their projects using those connections. Each linked repository exposes a **manual-paste webhook** URL + signed secret that the user installs in the provider's UI; pushes to that repository trigger builds for any pipeline in the project that is linked to the same repository.

This section is fully UI-driven: no configuration file edits are required. Phase 1 supports **Personal Access Tokens (PATs)** for provider auth; Phase 2 will add full OAuth redirect flows for GitHub and GitLab.

| ID | Feature | Priority | Description |
| --- | --- | --- | --- |
| F-16.1 | Admin-scoped `GitProviderConnection` store | M | Admins create/update/delete/test provider connections from **Settings → Integrations**. Each connection stores provider type, base URL, an encrypted credential, and the most recent validation result. |
| F-16.2 | PAT authentication (GitHub / GitLab / Generic) | M | The credential is a user-pasted token (or username/token for generic). Stored Fernet-encrypted at rest; never returned from the API. Only a 4-char suffix is shown in list/detail responses. |
| F-16.3 | OAuth-ready data model | S | `auth_mode` + OAuth columns (`oauth_client_id`, `encrypted_oauth_client_secret`, `encrypted_refresh_token`, `token_expires_at`) ship in Phase 1 but accept only `pat` until Phase 2 lands. |
| F-16.4 | Connection test | M | `POST /api/v1/git/connections/{id}/test` calls the provider (`GET /user` for GitHub/GitLab, `git ls-remote` for generic) with the current credential and records `validation_status` + `last_validated_at`. |
| F-16.5 | Per-project `ProjectRepository` link | M | A project owner links a `(connection, repo_url, default_branch, display_name)` tuple to their project. A project may have N linked repositories (monorepo plus helper repos). Pipelines may optionally reference a `project_repository_id` to inherit the repo URL + default branch. |
| F-16.6 | Manual-paste webhook setup | M | On link, MegooCI generates a 24-char `webhook_slug` and a 32-byte random secret (shown once). The UI renders provider-specific instructions: URL to paste (`{MEGOOCI_PUBLIC_URL}/api/v1/webhooks/git/{slug}`), secret to paste, which events to subscribe to, and content-type guidance. |
| F-16.7 | Per-provider HMAC verification | M | GitHub: `X-Hub-Signature-256` (HMAC-SHA256 of raw body). GitLab: `X-Gitlab-Token` constant-time compared to secret. Generic: `X-MegooCI-Signature: sha256=...` HMAC-SHA256 of raw body. All comparisons use `hmac.compare_digest`. |
| F-16.8 | Replay protection | M | Unique `(project_repository_id, provider_delivery_id)` on `WebhookDelivery`; duplicates return 409. GitHub uses `X-GitHub-Delivery`; GitLab `X-Gitlab-Event-UUID`; generic uses `X-MegooCI-Delivery` or a random UUID. |
| F-16.9 | Webhook delivery log | M | Every inbound request (accepted or rejected) is recorded as a `WebhookDelivery` row with event type, branch, commit sha, signature validity, the HTTP status we returned, an error string when rejected, and a 4 KB payload excerpt. Visible in the UI under **Project → Integrations → Deliveries**. |
| F-16.10 | Webhook-triggered build enqueue | M | On a verified push, MegooCI finds pipelines in the project whose `project_repository_id` matches the link (or, for back-compat, whose `source_repo_url` matches) and whose branch filter accepts the pushed branch; each match enqueues a `megooci.run_build` Celery task with `trigger_type="webhook"`. |
| F-16.11 | Rotate webhook secret | M | `POST /api/v1/projects/{project_id}/repositories/{repo_id}/rotate-secret` returns a new plaintext secret exactly once. Old secret is invalidated immediately. |
| F-16.12 | Rate limiting on webhook endpoint | S | The unauthenticated `POST /api/v1/webhooks/git/{slug}` route is rate-limited per slug via a Redis token bucket (default 60/min) to contain misconfigured or malicious replay floods. |
| F-16.13 | Provider guards | M | `DELETE /api/v1/git/connections/{id}` returns 409 while any `ProjectRepository` references it. `GET` on `/api/v1/webhooks/git/{slug}` returns 405 to prevent slug discovery via accidental GETs. |
| F-16.14 | Provider scope (Phase 1) | M | GitHub (SaaS + Enterprise Server), GitLab (SaaS + self-hosted), Generic Git (any HTTPS URL with username/token). Bitbucket and Gitea are deferred (§6.4 F-4.3 / F-4.4). |

#### 6.16.1 Data model additions

See §10 for the canonical list. Summary of the new tables:

- `GitProviderConnection(id, name, provider_type, base_url, auth_mode, encrypted_credential, encrypted_refresh_token, oauth_client_id, encrypted_oauth_client_secret, token_scopes, token_expires_at, validation_status, last_validated_at, validation_error, created_by, created_at, updated_at)`
- `ProjectRepository(id, project_id, connection_id, repo_url, default_branch, display_name, webhook_slug UNIQUE, webhook_secret_hash, last_event_at, last_event_status, created_by, created_at, updated_at)`
- `WebhookDelivery(id, project_repository_id, provider_delivery_id, event_type, branch, commit_sha, author, signature_valid, http_status, error, payload_excerpt, received_at, processed_at)` with `UNIQUE(project_repository_id, provider_delivery_id)`
- `Pipeline` gains an optional nullable `project_repository_id` FK (back-compat — existing pipelines keep using `source_repo_url` unchanged).

#### 6.16.2 Phases (Git Provider Integration)

- **Phase 1 (shipped 2026-04-20):** PRD entries above, data model + Alembic migration `002_git_integration.py`, admin connections (PAT only, with `test`), per-project repository links, manual webhook setup UI, deliveries log, webhook receiver with per-provider HMAC verification and build enqueue, rate limiting.
- **Phase 2 (deferred):** OAuth redirect flows for GitHub and GitLab with refresh-token rotation; commit-status / check writeback to the provider; repo picker (list repos the token can see) replacing the free-form URL field; SCM polling fallback for hosts without webhook support.

### 6.17 Global Search (Meilisearch)

MegooCI ships a **global search** feature backed by **Meilisearch**, providing instant, typo-tolerant, full-text search across all first-class entities. The backend maintains Meilisearch indexes that are bulk-synced on startup and incrementally updated on every create/update/delete. The frontend exposes this via a **command palette** (`Cmd/Ctrl+K`) and the header search bar.

| ID | Feature | Priority | Description |
| --- | --- | --- | --- |
| F-17.1 | **Meilisearch infrastructure** | M | Meilisearch v1.12 is added as a required service in `docker-compose.yml` (production) and `docker-compose.dev.yml` (development). Configured via `MEGOOCI_MEILISEARCH_URL` and `MEGOOCI_MEILISEARCH_API_KEY` env vars. |
| F-17.2 | **Project, pipeline, and build indexes** | M | Three Meilisearch indexes (`projects`, `pipelines`, `builds`) with per-index searchable attributes (e.g. name, slug, description for projects; branch, commit_sha, status for builds), filterable and sortable fields. |
| F-17.3 | **Full startup sync** | M | On backend startup, all existing projects, pipelines, and recent builds (capped at 500) are bulk-synced into Meilisearch. If Meilisearch is unavailable, the backend logs a warning and starts without search rather than crashing. |
| F-17.4 | **Incremental indexing on CRUD** | M | Every project/pipeline/build create, update, or delete fires a corresponding `index_*` / `remove_*` call to Meilisearch. Indexing failures are logged but never block the primary API response (fire-and-forget). |
| F-17.5 | **Multi-index search API** | M | `GET /api/v1/search?q=<query>&limit=<n>` (authenticated) runs a single multi-index query across projects, pipelines, and builds. Returns `SearchResponse` with grouped, typed `SearchHit` results (id, type, title, subtitle, url, extra metadata). |
| F-17.6 | **Command palette UI** | M | Frontend ships a `CommandPalette` component triggered by `Cmd/Ctrl+K` (global keyboard shortcut) or clicking the header search bar. Modal overlay with backdrop blur, text input with 200 ms debounce, grouped results by entity type (Project → Pipeline → Build), type-specific icons and colors, full keyboard navigation (arrow up/down, Enter to open, Esc to close), and a footer with shortcut hints. |
| F-17.7 | **Header search bar** | M | Desktop: a styled read-only input in the header with a search icon and "Search… Cmd+K" placeholder that opens the command palette on click. Mobile/tablet: a compact search icon button. |
| F-17.8 | **Build log in-viewer search** | M | The `BuildLogViewer` supports `Ctrl/Cmd+F` in-log search: filters log lines by text match, highlights matching terms with a yellow marker (`<mark>` tags), and scrolls to results. |

### 6.18 UI/UX Overhaul & Design System

MegooCI's frontend has been overhauled with a cohesive, cyberpunk-inspired design system, replacing decorative-only elements with fully functional components. All UI primitives are **hand-rolled** (no Radix dependency) to match the shadcn aesthetic while keeping the dependency tree minimal.

| ID | Feature | Priority | Description |
| --- | --- | --- | --- |
| F-18.1 | **Cyberpunk color theme** | M | Light mode: pale lavender surfaces, deep violet text, teal-cyan primary (`hsl(176, 100%, 30%)`), magenta accents. Dark mode: deep violet-black surfaces (`hsl(264, 90%, 4%)`), neon cyan primary (`hsl(176, 100%, 50%)`), hot magenta destructive, electric neon accents. Custom semantic tokens: `--success`, `--warning`, `--destructive`. |
| F-18.2 | **Three-way theme toggle** | M | Supports **Light / Dark / System** modes. Two variants: `segmented` (pill-shaped radio group for settings pages) and `icon` (cycling icon button for headers). System mode tracks `prefers-color-scheme` and auto-updates when the OS theme changes. Custom `ThemeProvider` (not `next-themes`) persists preference to `localStorage` under `megooci_theme`. Flash-free: an inline script in `layout.tsx` applies the `dark` class before React hydrates. |
| F-18.3 | **Semantic badge variants** | M | `class-variance-authority`-driven badges with 7 variants: `success`, `failed`, `running` (with `animate-pulse-slow`), `pending`, `cancelled`, plus standard `default`/`secondary`/`destructive`/`outline`. |
| F-18.4 | **Promise-based confirm dialog** | M | Custom `ConfirmProvider` + `useConfirm()` hook. Supports 4 tones: `default`, `destructive`, `warning`, `success`, each with a distinct icon, icon background color, and button variant. Backdrop blur, scale/translate entry animation, body scroll lock, keyboard handling (Esc/Enter), auto-focus on confirm button. Zero remaining `window.confirm` calls. |
| F-18.5 | **Visual stage graph** | M | Pipeline stage visualization showing stages as colored, bordered buttons connected by arrow icons. Status-aware: each status (pending/running/success/failed/cancelled) has its own icon, text color, background tint, and border color. Running stages get `animate-spin` on the loader icon. Clickable with a ring highlight on the selected stage. |
| F-18.6 | **Terminal-style build log viewer** | M | Dark-themed (`#0d1117`) log viewer with line numbers, timestamp display, stderr coloring (red), auto-scroll/follow mode, fullscreen toggle, copy-all-to-clipboard, and in-log search with highlighting. |
| F-18.7 | **Collapsible sidebar + mobile drawer** | M | Desktop: collapsible sidebar with state persisted to `localStorage`. Mobile: full-width off-canvas drawer with dark backdrop overlay and body scroll lock. 8 navigation items including admin-only Integrations link. User section at the bottom with avatar dropdown (Profile, theme cycle) and one-click logout with confirmation dialog. |
| F-18.8 | **Dynamic breadcrumbs** | M | Header generates breadcrumbs from the current pathname. Desktop: full breadcrumb trail. Mobile: current page title + hamburger menu. |
| F-18.9 | **Custom dialog system** | M | Fully custom dialog (no Radix dependency) with controlled/uncontrolled modes. Mobile-first: `items-end` (bottom sheet) on small screens, `items-center` on SM+. Escape key handling, backdrop click to close. |
| F-18.10 | **Avatar with initials fallback** | M | Generates initials from name/email, supports `sm`/`md`/`lg` sizes, with image error fallback to initials. |
| F-18.11 | **PWA support** | S | Service worker registration in production. Full PWA metadata: web manifest, Apple web app config, theme colors, viewport fit cover, app icons in multiple sizes. |

---

## 7. Feature Parity Matrix vs. Jenkins

| Capability | Jenkins | MegooCI v1 |
| --- | --- | --- |
| Declarative pipelines | Jenkinsfile (Groovy) | **YAML** (`megooci.yaml`) |
| Scripted pipelines | Groovy | **Python** (`megooci.py`, via Python SDK) |
| AI-assisted pipeline authoring | ❌ | ✅ Built-in, YAML + Python |
| Freestyle jobs | ✅ | ✅ |
| Multi-branch pipelines | ✅ | ✅ |
| Parameterized builds | ✅ | ✅ |
| Matrix builds | ✅ | ✅ |
| Parallel stages | ✅ | ✅ |
| Agents (SSH, Docker, K8s) | ✅ | ✅ (K8s in v1.1) |
| Git webhook triggers | ✅ (via plugin) | ✅ built-in |
| Generic incoming/outgoing webhooks | Plugin-based | ✅ Built-in |
| Cron triggers | ✅ | ✅ (Celery Beat) |
| **Plugin ecosystem** | ✅ (1.8k+ plugins) | ❌ **Intentionally not supported.** Extensibility via shell/container steps + REST API. |
| Global search (⌘K command palette) | Plugin-based | ✅ Built-in, Meilisearch-powered |
| Modern UI | Blue Ocean add-on | ✅ Default UI (cyberpunk design system, dark/light/system themes) |
| RBAC | Plugin-based | ✅ Built-in |
| Audit log | Plugin-based | ✅ Built-in |
| Credentials store | ✅ | ✅ |
| Env var management | Partial | ✅ First-class, scoped |
| Signup on/off toggle | Manual config | ✅ Runtime env variable |
| Artifact storage | Local FS or plugins | **Local FS only (v1)** |
| Container image registry | ❌ (requires external Nexus/Harbor/Docker Hub + plugins) | ✅ **Built-in OCI-compliant registry**, pull from any Docker/Kubernetes host |
| REST API | ✅ (inconsistent) | ✅ OpenAPI-first |
| CLI | `jenkins-cli.jar` | ✅ `megooci` native binary |
| LDAP/SAML/OIDC | Plugins | ✅ Built-in OIDC; SAML/LDAP in v1.1 |

---

## 8. User Experience Principles

1. **Progressive disclosure.** Simple jobs are configured in under 60 seconds; advanced config is available but never required upfront.
2. **Config-as-code, UI-assisted.** The source of truth is always a `megooci.yaml` or `megooci.py` file in the repo. The UI is a fast, friendly editor and AI collaborator on top of it.
3. **Two ways, same result.** YAML and Python pipelines compile to the same internal build graph. Every feature works in both.
4. **AI is a helper, not the author.** AI output is always a reviewable diff; nothing is saved or committed without explicit user approval.
5. **Fast feedback.** Live-streamed logs, optimistic UI updates, sub-200ms navigation on common pages.
6. **Readable defaults.** Clear typography, generous whitespace, consistent iconography, and no more than 2 primary actions per screen.
7. **Explain errors in plain English.** Every error includes: what happened, why, and the next action.
8. **Keyboard-first for power users.** Global command palette (`Cmd/Ctrl+K`).
9. **Zero surprises.** Destructive actions require confirmation; all state changes appear in the audit log.

---

## 9. Technical Architecture

### 9.1 High-Level Diagram (logical)

```
                     ┌────────────────────────────┐
                     │   Next.js Frontend (SPA)   │
                     │   React 19 / App Router    │
                     │   Command Palette (⌘K)     │
                     │   AI Assistant UI          │
                     └────────────┬───────────────┘
                                  │ HTTPS + WebSocket
                                  ▼
                     ┌────────────────────────────┐
                     │   FastAPI Backend (ASGI)   │
                     │   REST + WS + OpenAPI      │
                     │   Pipeline compiler        │
                     │   (YAML + Python → graph)  │
                     │   AI provider adapter      │
                     │   OCI Registry (/v2/...)   │
                     └─┬───────┬──┬────────────┬──┘
                       │       │  │            │
             ┌─────────▼─┐  ┌─▼──▼──┐    ┌────▼────────────┐
             │ PostgreSQL│  │ Redis │    │ Local Filesystem│
             │ (metadata)│  │(broker│    │ MEGOOCI_STORAGE │
             │           │  │ + pub │    │   _ROOT         │
             │           │  │ /sub) │    │ artifacts/ logs │
             │           │  │       │    │ registry/       │
             └───────────┘  └───┬───┘    └─────────────────┘
                                │                ▲
             ┌──────────┐   ┌───▼───────────────┐│
             │Meilisearch│  │   Celery Workers  ├┘ read/write
             │ (search)  │  │ + Celery Beat     │
             │ projects, │  │   Orchestrate     │
             │ pipelines,│  │   builds          │
             │ builds    │  └──────┬────────────┘
             └──────────┘         │ dispatch
                 ┌────────────────┼─────────────────┐
                 ▼                ▼                  ▼
          ┌────────────┐    ┌────────────┐    ┌────────────┐
          │ Local Exec │    │ Docker Exec│    │ SSH / K8s  │
          │            │    │            │    │ Agents     │
          └────────────┘    └────────────┘    └────────────┘
                                   │
                                   ▼
                         ┌───────────────────┐
                         │ External AI API   │
                         │ (OpenAI/Anthropic │
                         │ /Ollama/Azure)    │
                         │ — optional        │
                         └───────────────────┘
```

### 9.2 Component Responsibilities

**Frontend — Next.js (latest, App Router, React 19, TypeScript)**
- Server Components for fast initial loads; Client Components for interactive views.
- Tailwind CSS + hand-rolled UI primitives (shadcn-style, no Radix dependency) + `class-variance-authority` for variant-driven components.
- Cyberpunk-inspired design system with CSS-variable-based semantic tokens; three-way theme toggle (Light / Dark / System) via custom `ThemeProvider` with flash-free SSR.
- TanStack Query for data fetching / caching.
- Zustand for auth state; React context for theme and confirmation dialogs.
- WebSocket client for live log streaming and build status (auto-reconnect).
- Command palette (`Cmd/Ctrl+K`) powered by Meilisearch for instant cross-entity search.
- Auth via custom JWT integration with the FastAPI backend; single-flight token refresh to avoid race conditions.
- AI chat panel for pipeline generation/editing, streaming tokens from the backend (planned).
- Plain `<textarea>` editor for `megooci.yaml` and `megooci.py` (Monaco upgrade planned).
- PWA support: service worker, web manifest, Apple web app metadata.

**Backend — FastAPI (Python 3.12+)**
- REST API (OpenAPI 3.1 auto-generated).
- WebSocket endpoints for live logs, build events, and AI streaming responses (`/ws/builds/{id}/logs`, `/ws/ai/chat`).
- Auth: JWT access tokens + refresh tokens; OIDC/OAuth via Authlib; signup endpoint gated by `MEGOOCI_SIGNUP_ENABLED`.
- ORM: SQLAlchemy 2.x + Alembic migrations.
- Validation: Pydantic v2.
- **Pipeline compiler** that converts both YAML and Python pipeline definitions into a shared internal **build graph** (DAG of stages and steps).
  - YAML → parsed with Pydantic schemas.
  - Python → executed in a restricted subprocess with a resource/time budget (`MEGOOCI_PYTHON_PIPELINE_TIMEOUT_SECONDS`) and a whitelisted SDK (`megooci-sdk`). Standard library I/O and network are blocked during graph construction.
- **AI adapter layer** — provider-agnostic interface with concrete implementations for OpenAI, Anthropic, Azure OpenAI, and Ollama. Handles prompt templating, repo-context assembly, token streaming, and diff generation.
- **Embedded OCI/Docker registry** — the `/v2/…` endpoint tree is mounted directly on the FastAPI app (can also be bound to a separate port). Implements the OCI Distribution Spec v1.1 over the local filesystem, with Postgres for metadata (repos, tags, image↔build provenance) and Redis for upload session tracking and push/pull rate limiting. Auth is unified with MegooCI auth: Docker `login` uses a user's API token or a project deploy token.
- Dispatches build jobs onto Celery queues.
- Emits events to Redis pub/sub for frontend push updates.

**Task Execution — Celery**
- Broker: Redis (default) or RabbitMQ.
- Result backend: PostgreSQL.
- **Celery Beat** handles cron-scheduled pipelines.
- Dedicated queues per agent label, per priority (`high`, `default`, `low`).
- Each build = a Celery task chain: `prepare → clone → stages[] → collect_artifacts → report`.
- Long-running step execution is delegated from Celery workers to agents over a WebSocket channel; the worker tracks state and streams logs back.

**Agents**
- Self-contained Python (or Go) binary that connects outbound to the controller.
- Advertise labels, capacity, OS/arch.
- Execute steps in isolated environments (Docker container by default, or local process, or K8s pod).
- Stream logs back to the controller in real time.
- Upload artifacts back to the controller over HTTPS; controller persists them to `MEGOOCI_STORAGE_ROOT` on its local disk.

**Data Stores**
- **PostgreSQL 16+**: users, projects, pipelines, builds, steps, artifacts metadata, audit log, credentials + env vars (encrypted column), AI conversation history.
- **Redis 7+**: Celery broker, pub/sub for live events, rate limiting, short-lived caches.
- **Meilisearch v1.12**: full-text search engine for projects, pipelines, and builds. Indexes are synced on backend startup and updated incrementally on CRUD. Deployed as a Docker service alongside Postgres and Redis.
- **Local filesystem** at `MEGOOCI_STORAGE_ROOT`:
  - `artifacts/<pipeline_id>/<build_id>/...` — build artifacts.
  - `logs/<pipeline_id>/<build_id>/<step_id>.log` — archived build logs (live logs are streamed via Redis and written through to disk).
  - `registry/blobs/sha256/<ab>/<digest>` — content-addressed OCI blob store (deduplicated across repos).
  - `registry/manifests/<project>/<repo>/<digest>` — OCI manifests.
  - `registry/uploads/<session_id>` — in-progress chunked uploads.
  - `tmp/` — scratch space; regularly cleaned.
  - Backups are standard filesystem backups (rsync, restic, snapshots).

### 9.3 Deployment Topologies

- **All-in-one (dev / small):** single Docker Compose — controller, Postgres, Redis, Meilisearch, one worker, one agent. Artifacts & logs live on a mounted host volume (e.g., `./data:/var/lib/megooci`).
- **Single-node production:** same Compose stack on a dedicated VM with backed-up volumes; suitable for teams up to ~100 engineers.
- **Clustered (production):** Kubernetes Helm chart — HA controller (n≥2), Postgres (managed), Redis (managed), Meilisearch, N workers, dynamic K8s agents. A single **ReadWriteMany** persistent volume (NFS, CephFS, EFS, etc.) backs `MEGOOCI_STORAGE_ROOT` and is mounted by all controller and worker pods.

### 9.4 Security Architecture

- All traffic TLS-terminated at ingress.
- Short-lived JWTs (15 min) + rotating refresh tokens (stored httpOnly, secure).
- Secrets and env vars encrypted at rest with envelope encryption (AES-256-GCM + KEK derived from `MEGOOCI_SECRET_KEY`).
- Per-request RBAC check middleware in FastAPI.
- Signup endpoint globally gated behind `MEGOOCI_SIGNUP_ENABLED`; administrators can flip the flag at any time.
- Incoming webhooks verified via HMAC signatures and per-endpoint secrets; outgoing webhooks signed with HMAC and retried on 5xx.
- Python pipelines executed in a sandboxed subprocess with CPU/time/memory limits and a whitelisted SDK; no network or filesystem access during graph construction.
- Controller ↔ agent channel authenticated with mutual TLS or shared signed tokens.
- AI calls optionally route through a configurable base URL, allowing air-gapped deployments with Ollama. External AI providers are disabled automatically for private projects unless explicitly opted in.
- CSRF protection on state-changing endpoints; strict CORS policy.
- Storage root is writable only by the controller/worker service account; artifacts are served through signed, time-limited download URLs.
- Dependency scanning (pip-audit, npm audit) in our own CI.
- SBOM generated for each release.

### 9.5 Key Technical Decisions / ADRs (to author)

- ADR-001: YAML schema for pipelines.
- ADR-002: Python pipeline SDK surface and sandboxed execution model.
- ADR-003: Shared internal build-graph representation for YAML + Python.
- ADR-004: Controller ↔ agent protocol (WebSocket vs gRPC).
- ADR-005: Local-filesystem artifact & log storage layout, retention, and quota enforcement.
- ADR-006: Secret & env-var encryption scheme and key management.
- ADR-007: AI provider adapter interface and prompt/template governance.
- ADR-008: Webhook signing, replay protection, and retry semantics.
- ADR-009: Embedded OCI registry — on-disk layout, dedup strategy, garbage collection, and auth integration.

---

## 10. Data Model (Logical, Simplified)

- **User** `(id, email, name, auth_provider, created_at, is_active, is_admin)`
- **Role** `(id, name, permissions[])`
- **UserRole** `(user_id, role_id, scope_type, scope_id)`
- **Invite** `(id, email, role_id, token_hash, expires_at, created_by)`
- **Project / Folder** `(id, parent_id, name, description, created_by, allow_ai_repo_context)`
- **Pipeline** `(id, project_id, project_repository_id_nullable, name, source_repo_url, default_branch, definition_path, definition_format [yaml|python], enabled)`
- **Trigger** `(id, pipeline_id, type, config_json)`
- **WebhookEndpoint** `(id, pipeline_id, slug, secret_hash, created_at, last_used_at)` — legacy pipeline-scoped endpoint; superseded by `ProjectRepository` webhooks in §6.16.
- **OutgoingWebhook** `(id, scope_type, scope_id, url, events[], secret_hash)`
- **GitProviderConnection** `(id, name, provider_type [github|gitlab|generic], base_url, auth_mode [pat|oauth], encrypted_credential, encrypted_refresh_token, oauth_client_id, encrypted_oauth_client_secret, token_scopes, token_expires_at, validation_status [unknown|ok|failed], last_validated_at, validation_error, created_by, created_at, updated_at)`
- **ProjectRepository** `(id, project_id, connection_id, repo_url, default_branch, display_name, webhook_slug UNIQUE, webhook_secret_hash, last_event_at, last_event_status, created_by, created_at, updated_at)`
- **WebhookDelivery** `(id, project_repository_id, provider_delivery_id, event_type, branch, commit_sha, author, signature_valid, http_status, error, payload_excerpt, received_at, processed_at)` with `UNIQUE(project_repository_id, provider_delivery_id)`
- **Build** `(id, pipeline_id, number, branch, commit_sha, status, started_at, finished_at, triggered_by, trigger_type, params_json)`
- **Stage** `(id, build_id, name, status, started_at, finished_at)`
- **Step** `(id, stage_id, name, status, exit_code, started_at, finished_at, agent_id)`
- **LogChunk** `(id, step_id, seq, ts, stream, content)` (hot, in Postgres) → flushed to local disk at `MEGOOCI_STORAGE_ROOT/logs/...` on step completion.
- **Artifact** `(id, build_id, relative_path, size_bytes, checksum_sha256, storage_path, retention_until)`
- **ContainerRepository** `(id, project_id, name, is_public, immutable_tags, created_at)`
- **ContainerImage** `(id, repository_id, digest_sha256, size_bytes, manifest_media_type, architecture, os, build_id_nullable, pushed_at)`
- **ContainerTag** `(id, repository_id, name, image_id, updated_at)` (unique per repo; latest tag → image pointer)
- **RegistryDeployToken** `(id, project_id, name, scope [pull|pull_push], token_hash, expires_at, last_used_at, created_by, revoked)`
- **RegistryEvent** `(id, ts, actor, action [push|pull|delete], repository_id, tag_or_digest, client_ip, user_agent)`
- **Agent** `(id, name, labels[], os, arch, capacity, last_seen_at, status)`
- **Secret** `(id, scope_type, scope_id, name, type, encrypted_payload, created_by, rotated_at)`
- **EnvVar** `(id, scope_type, scope_id, name, value, is_secret_ref, created_by)` — non-secret env vars; `value` is plaintext.
- **AiConversation** `(id, user_id, pipeline_id_nullable, title, created_at)`
- **AiMessage** `(id, conversation_id, role [user|assistant|system], content, token_count, created_at)`
- **AuditLogEntry** `(id, ts, actor_id, action, target_type, target_id, metadata_json, ip_address)`

---

## 11. Pipeline Authoring Formats

MegooCI supports two equally-capable authoring formats. Both compile to the same internal build graph.

### 11.1 YAML — `megooci.yaml`

```yaml
version: 1
name: build-and-deploy-web

triggers:
  - type: github_push
    branches: [main, release/*]
  - type: schedule
    cron: "0 2 * * *"
  - type: webhook
    slug: release-trigger

parameters:
  - name: DEPLOY_ENV
    type: choice
    choices: [staging, production]
    default: staging

env:
  NODE_ENV: production
  LOG_LEVEL: info

agent:
  label: linux-docker

stages:
  - name: install
    steps:
      - run: npm ci

  - name: test
    parallel:
      - name: unit
        steps:
          - run: npm run test:unit
      - name: lint
        steps:
          - run: npm run lint

  - name: build
    steps:
      - run: npm run build
      - upload_artifact:
          path: .next/
          name: next-build

  - name: deploy
    when:
      branch: main
    steps:
      - run: ./scripts/deploy.sh
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.aws_access_key }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.aws_secret_key }}
          ENV: ${{ params.DEPLOY_ENV }}

notifications:
  on_failure:
    - slack: "#ci-alerts"
  on_success:
    - slack: "#deploys"
```

### 11.2 Python — `megooci.py`

Python pipelines are expressed with the `megooci-sdk` package. The file is loaded by the controller inside a sandboxed subprocess; its job is to return a `Pipeline` object describing the build graph. Arbitrary Python logic is allowed at graph-construction time (loops, conditionals, helper functions), but no network or filesystem I/O.

```python
from megooci import (
    Pipeline, Stage, Step, Parallel,
    GithubPushTrigger, ScheduleTrigger, WebhookTrigger,
    ChoiceParam, secret, env, param,
)

pipeline = Pipeline(
    name="build-and-deploy-web",
    triggers=[
        GithubPushTrigger(branches=["main", "release/*"]),
        ScheduleTrigger(cron="0 2 * * *"),
        WebhookTrigger(slug="release-trigger"),
    ],
    parameters=[
        ChoiceParam(
            name="DEPLOY_ENV",
            choices=["staging", "production"],
            default="staging",
        ),
    ],
    env={
        "NODE_ENV": "production",
        "LOG_LEVEL": "info",
    },
    agent_label="linux-docker",
)

pipeline.add(Stage("install", steps=[Step.run("npm ci")]))

pipeline.add(Stage("test", parallel=[
    Parallel("unit", steps=[Step.run("npm run test:unit")]),
    Parallel("lint", steps=[Step.run("npm run lint")]),
]))

pipeline.add(Stage("build", steps=[
    Step.run("npm run build"),
    Step.upload_artifact(path=".next/", name="next-build"),
]))

deploy_steps = [
    Step.run(
        "./scripts/deploy.sh",
        env={
            "AWS_ACCESS_KEY_ID": secret("aws_access_key"),
            "AWS_SECRET_ACCESS_KEY": secret("aws_secret_key"),
            "ENV": param("DEPLOY_ENV"),
        },
    ),
]
pipeline.add(Stage("deploy", when=pipeline.when(branch="main"), steps=deploy_steps))

pipeline.on_failure(slack="#ci-alerts")
pipeline.on_success(slack="#deploys")
```

### 11.3 AI-generated Pipelines (UI Flow)

1. User clicks **"Generate pipeline with AI"** on a new pipeline page.
2. User picks the target format (**YAML** or **Python**) and, optionally, connects a Git repo.
3. User describes the goal in plain English (e.g., "Build a FastAPI backend, run pytest with coverage, produce a Docker image, push to GHCR on main").
4. Backend gathers repo context (manifest files, languages, existing config) within privacy limits (F-12.8).
5. AI streams a draft pipeline; the UI renders it in the editor with a **Diff** view.
6. User iterates via chat ("also add a nightly cron"), accepts changes, and saves.
7. On save, the pipeline is linted (`megooci lint`) and dry-run compiled; only valid pipelines can be committed.

---

## 12. Non-Functional Requirements

| Category | Requirement |
| --- | --- |
| **Performance** | API P95 < 300 ms for reads, < 800 ms for writes under 100 rps. UI LCP < 2.0 s on broadband. |
| **Scalability** | Support 500 concurrent builds, 200 agents, 100k builds in history on a single clustered deployment. |
| **Availability** | 99.9% for the clustered deployment. Controller is stateless + HA; data stores are HA via managed services. |
| **Durability** | Zero data loss for build metadata. Artifacts and logs survive host failure via filesystem backups (single-node) or a replicated persistent volume (clustered). |
| **Security** | OWASP Top 10 hardened; quarterly dep scans; pen-test before 1.0 GA. |
| **Observability** | Prometheus metrics, structured logs, OTel traces, health + readiness endpoints. |
| **Compatibility** | Latest two versions of Chrome, Firefox, Safari, Edge. Server runs on Linux x86_64 + arm64. |
| **I18n** | English at v1; i18n framework in place for future locales. |
| **Accessibility** | WCAG 2.1 AA for all primary flows. |
| **Data retention** | Configurable: default keep last 50 builds or 30 days per pipeline. |

---

## 13. Success Metrics

**North-star metric:** Weekly active pipelines per installation.

**Secondary metrics:**
- Time-to-first-green-build after install (target: < 10 minutes).
- Median pipeline configuration time vs. Jenkins (target: 3× faster via user study).
- UI task success rate in usability tests (target: ≥ 90%).
- p95 build queue wait time (target: < 5 s with available capacity).
- Mean time between controller restarts (target: > 30 days).
- Community: GitHub stars, Docker pulls, monthly active contributors.

---

## 14. Release Plan & Milestones

| Milestone | Target | Scope |
| --- | --- | --- |
| **M0 — Foundations** | Week 0–3 | Repo setup, CI/CD for MegooCI itself, ADRs, design system, DB schema, auth skeleton. |
| **M1 — MVP (Alpha)** | Week 4–10 | Local executor, **YAML pipelines**, Git webhook (GitHub), live logs, basic UI, manual + cron triggers, local user auth with **signup env toggle**, local-FS artifact storage, env vars + secrets. |
| **M2 — Beta** | Week 11–16 | Docker executor, remote SSH agents, **Python pipelines + SDK**, JUnit, Slack notifications, OIDC, RBAC, audit log, multi-branch, incoming + outgoing webhooks, **embedded OCI registry v1 (push/pull, deploy tokens, GC, UI)**. |
| **M3 — 1.0 GA** | Week 17–22 | Matrix builds, parallel stages, templates, GitLab/Bitbucket, Helm chart, backup/restore, Prometheus metrics, **AI pipeline generator (v1: YAML + Python generation, chat-based edit)**, registry multi-arch + provenance links, hardening + pen-test. |
| **M4 — 1.1** | Post-GA | Kubernetes executor, SAML/LDAP, visual pipeline editor, Vault integration, OTel tracing, **AI fix-it suggestions on failed builds**, **registry image signing + Trivy vulnerability scans**. |
| **M5 — 1.2+** | Post-GA | Auto-scaling agent pools, signed artifacts, cross-build comparison, AI provider fine-tuning hooks, **registry proxy cache for external registries**. (Note: **no plugin framework is planned**, per NG2.) |

---

## 15. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Scope creep chasing Jenkins parity | High | High | Strict priority labels (M/S/C); quarterly scope review; NG2 (no plugins) is a hard line. |
| Celery is the wrong fit for long-running build orchestration | Medium | High | Prototype in M0; benchmark vs. custom scheduler; keep execution layer abstracted. |
| Controller ↔ agent protocol instability | Medium | High | Early load testing; versioned protocol; ADR-004 upfront. |
| Python pipelines enable arbitrary code execution on controller | High | Critical | Mandatory subprocess sandbox with time/memory limits; no network/FS I/O during graph construction; ADR-002. |
| Local filesystem storage limits scale / HA story | Medium | High | Document single-node vs clustered deployment; support RWX PVs on K8s; add disk-quota enforcement and retention policies from day one. |
| Security vulnerability in execution sandbox | Medium | Critical | Container isolation by default, mandatory log masking, external audit pre-1.0. |
| Adoption blocked by missing Jenkins plugin equivalents | High | Medium | Ship top-20 most-used integrations as first-party features; publish a compatibility matrix; AI assistant helps users author replacements for niche plugins as shell/container steps. |
| AI provider outage / cost / data leakage | Medium | Medium | Feature is toggle-able (`MEGOOCI_AI_ENABLED`); support self-hosted Ollama for air-gapped environments; per-project privacy controls (F-12.8). |
| AI generates incorrect or insecure pipelines | Medium | Medium | Output is always diff-reviewed before save; automatic `megooci lint` + dry-run compile gate save; logs of every AI-assisted change in the audit log. |
| DSL schema churn across YAML + Python | Medium | Medium | `version: 1` field, semver for the Python SDK, shared build-graph IR (ADR-003) decouples authoring format from internal model. |
| Embedded registry disk exhaustion / runaway growth | High | High | Per-project quotas (F-13.19), tag-aware retention tied to build retention (F-13.10), scheduled GC with visibility (F-13.11), disk-usage dashboard. |
| OCI spec conformance bugs block real-world Docker clients | Medium | High | Run the official OCI conformance test suite in our CI; test against `docker`, `podman`, `crane`, `containerd`, `helm`, and Kubernetes `kubelet` on every release. |
| Unauthorized image pull from production hosts | Medium | Critical | Deploy tokens are scoped to a project, revocable, and expire; all pulls are audit-logged; anonymous pull is off by default; TLS required for non-localhost. |
| OSS community momentum doesn't materialize | Medium | Medium | Clear contributor guide, "good first issue" labels, monthly community call. |

---

## 16. Open Questions

1. What is our **upgrade/migration story** from Jenkins? Do we provide a best-effort `Jenkinsfile → megooci.yaml` converter (likely powered by the AI generator)?
2. Should v1 ship with a **built-in artifact/package registry UI**, or purely filesystem-based artifact browsing?
3. Should Python pipelines also be allowed to run **runtime hooks** (e.g. small Python callbacks during build execution), or strictly limited to graph-construction time?
4. Is a **hosted/SaaS tier** part of the monetization plan, and if so, does v1 need multi-tenant primitives now?
5. Do we require organizations to **bring their own AI API key**, or do we ever proxy through a MegooCI-hosted AI gateway?
6. Single binary distribution (Go rewrite of controller) — is it worth it in v1.x for the "Sam" persona?
7. Should the Python pipeline SDK also be usable **outside MegooCI** (e.g., to dry-run a pipeline locally), or is it controller-only?

---

## 17. Glossary

- **Pipeline** — a versioned definition of build/test/deploy stages, stored as `megooci.yaml` or `megooci.py` in a repo.
- **Build** — a single execution of a pipeline against a specific commit & parameters.
- **Stage** — a logical group of steps within a build (e.g., `test`, `deploy`).
- **Step** — an atomic unit of work within a stage (a shell command, script, or container invocation).
- **Build graph** — the internal DAG representation that both YAML and Python pipelines compile to.
- **Python SDK** — the `megooci-sdk` PyPI package used to author `megooci.py` pipelines and as a client library.
- **Agent / Runner** — a worker process that executes build steps, connected to the controller.
- **Controller** — the central server hosting the API, UI, scheduler, and orchestration logic.
- **Executor** — the runtime environment a step is run in (local process, Docker container, K8s pod, SSH host).
- **Secret** — an encrypted value (token, key, password) referenced by name in pipelines.
- **Env variable** — a named, non-secret value scoped to the global, project, pipeline, stage, or step level.
- **Trigger** — a rule that starts a build (webhook, cron, manual, upstream, API).
- **Webhook** — an HTTP endpoint used to signal events, either incoming (to trigger builds) or outgoing (to notify external systems of build events).
- **Storage root** — the local directory at `MEGOOCI_STORAGE_ROOT` where artifacts, archived logs, and the container registry blobs live.
- **Container registry** — MegooCI's built-in, OCI-compliant Docker image registry that hosts images produced by builds and serves `docker pull` requests from external servers.
- **Deploy token** — a scoped, revocable credential (pull-only or pull+push) that external servers use to authenticate with the MegooCI container registry.
- **OCI Distribution Spec** — the open standard for container image registries, implemented by Docker Hub, GHCR, Harbor, and now MegooCI.
- **AI provider** — the external or self-hosted LLM backend (OpenAI, Anthropic, Azure OpenAI, Ollama) used by the AI pipeline assistant.

---

## 18. Appendix: Reference Inspirations

- **Jenkins** — feature breadth, agent model.
- **GitHub Actions** — declarative YAML, matrix builds, marketplace ergonomics.
- **GitLab CI** — integrated SCM + CI experience.
- **Drone CI** — container-native execution model.
- **Buildkite** — hybrid SaaS controller + self-hosted agents.
- **Argo Workflows / Tekton** — Kubernetes-native execution patterns.

---

*End of document. Changes to this PRD are tracked via the `docs/prd.md` file history.*
