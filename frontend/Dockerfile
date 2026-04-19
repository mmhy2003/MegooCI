# syntax=docker/dockerfile:1.7

# =============================================================================
# Production Dockerfile for MegooCI Frontend (Next.js 15, standalone output)
#
# Multi-stage build:
#   1. deps     — install production + dev deps against lockfile
#   2. builder  — compile the Next.js app into a standalone bundle
#   3. runner   — minimal runtime image serving the standalone server
#
# Build:  docker build -t megooci-frontend:latest ./frontend
# Run:    docker run -p 3000:3000 --env-file .env megooci-frontend:latest
# =============================================================================

ARG NODE_VERSION=22

# ----------------------------------------------------------------------------
# Stage 1: install dependencies (cached layer, rebuilt only when lockfile changes)
# ----------------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS deps
WORKDIR /app

# Install deps needed by some native node modules on alpine
RUN apk add --no-cache libc6-compat

COPY package.json package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm \
    if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; \
    else npm install --no-audit --no-fund; fi

# ----------------------------------------------------------------------------
# Stage 2: build the Next.js app
# ----------------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS builder
WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

# NEXT_PUBLIC_* vars are inlined into the client bundle at build time.
# Override via `--build-arg NEXT_PUBLIC_API_URL=https://api.example.com`.
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN npm run build

# ----------------------------------------------------------------------------
# Stage 3: runtime image
# ----------------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# curl is used by the HEALTHCHECK below
RUN apk add --no-cache curl

# Create a dedicated non-root user
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 --ingroup nodejs nextjs

# Copy the Next.js standalone output (includes only required node_modules)
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail --silent --show-error --max-time 4 http://127.0.0.1:3000/ || exit 1

CMD ["node", "server.js"]
