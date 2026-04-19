# MegooCI — Product Requirements Document (PRD)

| Field | Value |
| --- | --- |
| **Product Name** | MegooCI |
| **Tagline** | A simpler, modern open-source alternative to Jenkins |
| **Document Status** | Draft v1.1 |
| **Last Updated** | 2026-04-19 |
| **Owner** | MegooCI Core Team |
| **License (planned)** | Apache-2.0 (OSS) |

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
| Modern UI | Blue Ocean add-on | ✅ Default UI |
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
                     └─┬───────────┬────────────┬─┘
                       │           │            │
             ┌─────────▼─┐     ┌───▼───┐    ┌───▼─────────────┐
             │ PostgreSQL│     │ Redis │    │ Local Filesystem│
             │ (metadata)│     │(broker│    │ MEGOOCI_STORAGE │
             │           │     │ + pub │    │   _ROOT         │
             │           │     │ /sub) │    │ artifacts/ logs │
             │           │     │       │    │ registry/       │
             └───────────┘     └───┬───┘    └─────────────────┘
                                   │                ▲
                       ┌───────────▼────────────┐   │
                       │   Celery Workers       │───┘ read/write
                       │   + Celery Beat (cron) │
                       │   Orchestrate builds   │
                       └───────────┬────────────┘
                                   │ dispatch
                 ┌─────────────────┼─────────────────┐
                 ▼                 ▼                 ▼
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
- Tailwind CSS + shadcn/ui for the component library.
- TanStack Query for data fetching / caching.
- Zustand (or React context) for local UI state.
- WebSocket client for live log streaming and build status.
- Auth via custom JWT integration with the FastAPI backend.
- AI chat panel for pipeline generation/editing, streaming tokens from the backend.
- Monaco-based editor for `megooci.yaml` and `megooci.py`, with schema + type hints.

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
- **Local filesystem** at `MEGOOCI_STORAGE_ROOT`:
  - `artifacts/<pipeline_id>/<build_id>/...` — build artifacts.
  - `logs/<pipeline_id>/<build_id>/<step_id>.log` — archived build logs (live logs are streamed via Redis and written through to disk).
  - `registry/blobs/sha256/<ab>/<digest>` — content-addressed OCI blob store (deduplicated across repos).
  - `registry/manifests/<project>/<repo>/<digest>` — OCI manifests.
  - `registry/uploads/<session_id>` — in-progress chunked uploads.
  - `tmp/` — scratch space; regularly cleaned.
  - Backups are standard filesystem backups (rsync, restic, snapshots).

### 9.3 Deployment Topologies

- **All-in-one (dev / small):** single Docker Compose — controller, Postgres, Redis, one worker, one agent. Artifacts & logs live on a mounted host volume (e.g., `./data:/var/lib/megooci`).
- **Single-node production:** same Compose stack on a dedicated VM with backed-up volumes; suitable for teams up to ~100 engineers.
- **Clustered (production):** Kubernetes Helm chart — HA controller (n≥2), Postgres (managed), Redis (managed), N workers, dynamic K8s agents. A single **ReadWriteMany** persistent volume (NFS, CephFS, EFS, etc.) backs `MEGOOCI_STORAGE_ROOT` and is mounted by all controller and worker pods.

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
- **Pipeline** `(id, project_id, name, source_repo_url, default_branch, definition_path, definition_format [yaml|python], enabled)`
- **Trigger** `(id, pipeline_id, type, config_json)`
- **WebhookEndpoint** `(id, pipeline_id, slug, secret_hash, created_at, last_used_at)`
- **OutgoingWebhook** `(id, scope_type, scope_id, url, events[], secret_hash)`
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
