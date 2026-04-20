# Multi-stage build for the MegooCI agent. Produces a tiny final image that
# is still useful as a build host (Debian slim) — we keep /bin/sh, git, and
# curl so basic pipeline steps work out of the box without needing the user
# to bring their own base image. Users who want Docker-in-Docker or custom
# toolchains can base their own image on this or substitute something
# larger like a language-specific toolchain image.
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

# Cache deps first.
COPY go.mod go.sum* ./
RUN go mod download

COPY . .

ARG VERSION=0.1.0-dev
ARG COMMIT=unknown
ARG DATE=unknown
ARG TARGETOS
ARG TARGETARCH

# CGO_ENABLED=0 + -s -w strips the debug tables so the binary is ~8 MB
# instead of ~25. Trimpath makes the binary reproducible across build hosts.
ENV CGO_ENABLED=0
RUN --mount=type=cache,target=/root/.cache/go-build \
    GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64} \
    go build -trimpath \
      -ldflags "-s -w \
        -X github.com/megooci/megooci-agent/internal/version.Version=${VERSION} \
        -X github.com/megooci/megooci-agent/internal/version.Commit=${COMMIT} \
        -X github.com/megooci/megooci-agent/internal/version.Date=${DATE}" \
      -o /out/megooci-agent ./cmd/megooci-agent


FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git curl \
 && rm -rf /var/lib/apt/lists/*

# Non-root by default. Mount volumes as this user if your steps need to
# persist data between runs.
RUN useradd --create-home --shell /bin/sh --uid 1000 runner
USER runner
WORKDIR /home/runner

COPY --from=builder /out/megooci-agent /usr/local/bin/megooci-agent

ENTRYPOINT ["/usr/local/bin/megooci-agent"]
CMD ["run"]
