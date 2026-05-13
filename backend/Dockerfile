# ============================================================================
#  MegooCI Backend — Multi-stage Dockerfile
# ============================================================================
#  Stages:
#    base       – shared foundation (OS deps, Python packages, entrypoint)
#    dev        – development target  (hot-reload via --reload)
#    production – production target   (multi-worker via --workers 4)
# ============================================================================


# ---------------------------------------------------------------------------
#  Stage 1: base — shared across all targets
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

# Set the working directory for all subsequent instructions
WORKDIR /app

# Install system-level build & runtime dependencies:
#   • gcc       – required to compile C-extension Python packages
#   • libpq-dev – PostgreSQL client headers (needed by psycopg2 / asyncpg)
# The apt cache is removed afterwards to keep the image lean.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only the project manifest first so that the dependency-install layer
# is cached independently of application code changes.
COPY pyproject.toml .

# Install the project in editable mode (no pip cache to reduce image size)
RUN pip install --no-cache-dir -e .

# Copy the entrypoint script into a PATH-accessible location
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

# Normalize Windows-style line endings (CRLF → LF) that may have been
# introduced by Git on Windows, then ensure the script is executable.
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
    && chmod +x /usr/local/bin/entrypoint.sh

# Copy the remaining application source code
COPY . .

# Use the entrypoint script for container startup (e.g. run migrations, etc.)
ENTRYPOINT ["entrypoint.sh"]


# ---------------------------------------------------------------------------
#  Stage 2: dev — development target (docker compose --target dev)
# ---------------------------------------------------------------------------
FROM base AS dev

# Start Uvicorn with --reload so code changes are picked up automatically
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]


# ---------------------------------------------------------------------------
#  Stage 3: production — production target (default)
# ---------------------------------------------------------------------------
FROM base AS production

# Start Uvicorn with 4 worker processes for production-grade concurrency
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
