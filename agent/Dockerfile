# Multi-stage build for the MegooCI agent. The final image ships a batteries-
# included toolchain (Python, Node/npm/pnpm, uv, build-essential, Docker CLI,
# Temurin JDK 21, Maven, Dart SDK, kubectl) so most pipeline steps work without needing
# the user to bring their own base image. The Docker CLI is client-only —
# bind-mount the host socket or run a DinD sidecar to enable `docker build` /
# `docker run` inside pipelines.
#
# Build:
#   docker build -t megooci/agent:dev ./agent
#
# Run (flags are the recommended invocation — keeps the token off
# `docker inspect` of any other container and out of .env):
#
#   docker run -d --restart=unless-stopped \
#     --network megooci_default \
#     -v /var/run/docker.sock:/var/run/docker.sock \
#     --group-add $(stat -c '%g' /var/run/docker.sock) \
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
      ca-certificates git curl gnupg openssh-client sshpass \
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

# ── Pi AI Coding Agent ─────────────────────────────────────────────
# Installed globally so `ai_agent` pipeline steps can invoke `pi -p`.
RUN npm install -g @earendil-works/pi-coding-agent

# ── Docker CLI + BuildKit (Docker official repo) ───────────────────────────
RUN curl -fsSL https://download.docker.com/linux/debian/gpg \
      | gpg --dearmor -o /usr/share/keyrings/docker.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker.gpg] \
      https://download.docker.com/linux/debian bookworm stable" \
      > /etc/apt/sources.list.d/docker.list \
 && apt-get update && apt-get install -y --no-install-recommends \
      docker-ce-cli docker-buildx-plugin \
 && rm -rf /var/lib/apt/lists/*

# ── uv (Astral official image) ─────────────────────────────────────────────
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ── Temurin JDK 21 LTS (Adoptium official repo) ────────────────────────────
# Installs to /usr/lib/jvm/temurin-21-jdk-<arch>; we add a stable arch-agnostic
# symlink so JAVA_HOME works the same on amd64 and arm64 images.
ARG JDK_MAJOR=21
RUN curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public \
      | gpg --dearmor -o /usr/share/keyrings/adoptium.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb bookworm main" \
      > /etc/apt/sources.list.d/adoptium.list \
 && apt-get update && apt-get install -y --no-install-recommends "temurin-${JDK_MAJOR}-jdk" \
 && ln -sf "/usr/lib/jvm/temurin-${JDK_MAJOR}-jdk-$(dpkg --print-architecture)" /usr/lib/jvm/default-jvm \
 && rm -rf /var/lib/apt/lists/*
ENV JAVA_HOME=/usr/lib/jvm/default-jvm
ENV PATH="${JAVA_HOME}/bin:${PATH}"

# ── Maven (Apache binary tarball) ──────────────────────────────────────────
# Pulled directly from Apache instead of `apt install maven` to avoid the
# Debian package's hard dependency on OpenJDK (which would duplicate Temurin).
ARG MAVEN_VERSION=3.9.9
RUN curl -fsSL "https://archive.apache.org/dist/maven/maven-3/${MAVEN_VERSION}/binaries/apache-maven-${MAVEN_VERSION}-bin.tar.gz" \
      | tar -xz -C /opt \
 && ln -sf "/opt/apache-maven-${MAVEN_VERSION}" /opt/maven \
 && ln -sf /opt/maven/bin/mvn /usr/local/bin/mvn \
 && ln -sf /opt/maven/bin/mvnDebug /usr/local/bin/mvnDebug
ENV MAVEN_HOME=/opt/maven

# ── Dart SDK (Google official repo) ────────────────────────────────────────
# Supports amd64 + arm64. Pure-Dart projects use `dart pub`, `dart compile`,
# `dart test`, etc. directly; no Flutter SDK is bundled in this image.
RUN curl -fsSL https://dl-ssl.google.com/linux/linux_signing_key.pub \
      | gpg --dearmor -o /usr/share/keyrings/dart.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/dart.gpg] https://storage.googleapis.com/download.dartlang.org/linux/debian stable main" \
      > /etc/apt/sources.list.d/dart_stable.list \
 && apt-get update && apt-get install -y --no-install-recommends dart \
 && rm -rf /var/lib/apt/lists/*
ENV PATH="/usr/lib/dart/bin:${PATH}"

# ── kubectl (Kubernetes CLI, pinned) ───────────────────────────────────────
# Required by `kube_apply` pipeline steps. Static binary from the official
# Kubernetes release bucket; arch follows the image platform (amd64/arm64).
ARG KUBECTL_VERSION=v1.36.1
RUN curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/$(dpkg --print-architecture)/kubectl" \
      -o /usr/local/bin/kubectl \
 && chmod 0755 /usr/local/bin/kubectl \
 && kubectl version --client

# ── Non-root user ──────────────────────────────────────────────────────────
# Create a 'docker' group so the socket GID can be mapped at runtime via
# `docker run --group-add`.  The runner user is added to it by default.
RUN groupadd -g 998 docker \
 && useradd --create-home --shell /bin/sh --uid 1000 -G docker runner
USER runner
WORKDIR /home/runner

COPY --from=builder /out/megooci-agent /usr/local/bin/megooci-agent

ENTRYPOINT ["/usr/local/bin/megooci-agent"]
CMD ["run"]
