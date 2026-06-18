# P5 Slice 2 — container image for AgentBase cloud deployment
# Multi-stage: build deps, then assemble minimal runtime.

# ---- UI builder stage ----
FROM node:22-slim AS ui-builder
WORKDIR /ui
COPY docs/0_draft/ui-astro/package.json docs/0_draft/ui-astro/package-lock.json* ./
RUN npm ci --quiet
COPY docs/0_draft/ui-astro/ ./
RUN npm run build

# ---- Builder stage ----
FROM python:3.12-slim AS builder
WORKDIR /build

# 1. Install uv + copy only dependency metadata (cached unless deps change)
COPY pyproject.toml README.md ./
RUN pip install uv --quiet

# 2. Create minimal src layout so uv sync can find the package
RUN mkdir -p src/core && touch src/core/__init__.py
COPY config/ ./config/
RUN uv sync --no-dev --no-editable 2>/dev/null || true

# 3. Copy real source and re-sync (fast — deps already cached)
COPY src/ ./src/
RUN uv sync --no-dev --no-editable

# ---- Runtime stage ----
FROM python:3.12-slim

# Litestream binary
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates wget && \
    wget -q https://github.com/benbjohnson/litestream/releases/download/v0.3.13/litestream-v0.3.13-linux-amd64.tar.gz -O /tmp/litestream.tar.gz && \
    tar -xzf /tmp/litestream.tar.gz -C /usr/local/bin/ litestream && \
    rm /tmp/litestream.tar.gz && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Create data directories (dummy vault satisfies VAULT_ROOT check — TD-059)
RUN mkdir -p /data /data/vault

# Copy installed packages from builder
COPY --from=builder /build/.venv/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/

# Copy application source, config, and scripts at runtime paths
COPY src/ /app/src/
COPY src/storage/schema.sql /usr/local/lib/python3.12/site-packages/storage/schema.sql
COPY src/storage/migrations/ /usr/local/lib/python3.12/site-packages/storage/migrations/
COPY config/ /usr/local/lib/python3.12/site-packages/config/
COPY src/config/ /usr/local/lib/python3.12/site-packages/config/
COPY src/prompts/ /usr/local/lib/python3.12/site-packages/prompts/
COPY scripts/ /app/scripts/
COPY litestream.yml /etc/litestream.yml

# Copy built UI assets
COPY --from=ui-builder /ui/dist/ /app/ui/

# Pre-download cross-encoder model so container doesn't hit HuggingFace at runtime
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

ENV PYTHONPATH=/app
EXPOSE 8080
ENTRYPOINT ["/app/scripts/start.sh"]
