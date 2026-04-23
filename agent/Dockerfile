# Multi-stage build for the MegooCI agent. The final image ships a batteries-
# included toolchain (Python, Node/npm/pnpm, uv, build-essential, Docker CLI)
# so most pipeline steps work without needing the user to bring their own base
# image. The Docker CLI is client-only — bind-mount the host socket or run a
# DinD sidecar to enable `docker build` / `docker run` inside pipelines.
#
# Build:
#   docker build -t megooci/agent:dev ./agent
#
# Run (flags are the recommended invocation — keeps the token off
# `docker inspect` of any other container and out of .env):
#
#   docker run -d --restart=unless-stopped \
#     --network megooci_default \
#     megooci/agent:dev \
#     run \
#       --controller http://backend:8000 \
#       --agent-id 01234567-89ab-cdef-0123-456789abcdef \
#       --token megci_agt_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
#
# Environment-variable fallbacks (MEGOOCI_CONTROLLER_URL, MEGOOCI_AGENT_ID,
# MEGOOCI_AGENT_TOKEN) are also honored if you'd rather not have the token
# on the command line.

ARG GO_VERSION=1.22

FROM golang:${GO_VERSION}-bookworm AS builder
WORKDIR /src

# Copy the dependency manifest. `go.sum` is optional (the `*` glob tolerates
# its absence) because we regenerate it with `go mod tidy` below. Keeping
# this as its own layer means deps aren't re-resolved unless go.mod changes.
COPY go.mod go.sum* ./

# Copy all sources, then run `tidy` so go.sum is always consistent with the
# imports in the code. This makes the image buildable even if the caller
# hasn't committed a go.sum (e.g. fresh clone, Go not installed locally).
COPY . .
RUN --mount=type=cache,target=/go/pkg/mod \
    go mod tidy

ARG VERSION=0.1.0-dev
ARG COMMIT=unknown
ARG DATE=unknown
ARG TARGETOS
ARG TARGETARCH

# CGO_ENABLED=0 + -s -w strips the debug tables so the binary is ~8 MB
# instead of ~25. Trimpath makes the binary reproducible across build hosts.
ENV CGO_ENABLED=0
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
    GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64} \
    go build -trimpath \
      -ldflags "-s -w \
        -X github.com/megooci/megooci-agent/internal/version.Version=${VERSION} \
        -X github.com/megooci/megooci-agent/internal/version.Commit=${COMMIT} \
        -X github.com/megooci/megooci-agent/internal/version.Date=${DATE}" \
      -o /out/megooci-agent ./cmd/megooci-agent


FROM debian:bookworm-slim

# Harden apt against the flaky DNS inside Docker Desktop's build VM.
RUN echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries

# ── System packages (Debian repos) ──────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git curl gnupg \
      build-essential \
      python3 python3-pip python3-venv \
 && rm -rf /var/lib/apt/lists/*

# ── Node.js (NodeSource official repo) ──────────────────────────────────────
ARG NODE_MAJOR=20
RUN curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | gpg --dearmor -o /usr/share/keyrings/nodesource.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
      > /etc/apt/sources.list.d/nodesource.list \
 && apt-get update && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# ── pnpm (via corepack, ships with Node.js) ────────────────────────────────
RUN corepack enable && corepack prepare pnpm@latest --activate

# ── Docker CLI (Docker official repo) ───────────────────────────────────────
RUN curl -fsSL https://download.docker.com/linux/debian/gpg \
      | gpg --dearmor -o /usr/share/keyrings/docker.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker.gpg] \
      https://download.docker.com/linux/debian bookworm stable" \
      > /etc/apt/sources.list.d/docker.list \
 && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
 && rm -rf /var/lib/apt/lists/*

# ── uv (Astral official image) ─────────────────────────────────────────────
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ── Non-root user ──────────────────────────────────────────────────────────
RUN useradd --create-home --shell /bin/sh --uid 1000 runner
USER runner
WORKDIR /home/runner

COPY --from=builder /out/megooci-agent /usr/local/bin/megooci-agent

ENTRYPOINT ["/usr/local/bin/megooci-agent"]
CMD ["run"]
