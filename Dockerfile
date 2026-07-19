# Production image for the Gridiron read-only web app.
# Multi-stage: resolve deps with uv (from the committed uv.lock), then a slim runtime.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# 1) Install locked dependencies first (cached layer, no project yet, no dev tools).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) Add the source and install the project itself (console scripts, editable metadata).
COPY . .
RUN uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime
WORKDIR /app

# Copy the resolved virtualenv + app from the builder.
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    # Read-only demo defaults (override for production — see docker-compose.prod.yml).
    DATABASE_URL="sqlite:////data/gridiron.db" \
    SEED_FIXTURES=1 \
    PORT=8000

RUN mkdir -p /data
EXPOSE 8000

ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
