# P5 Slice 2 — container image for AgentBase cloud deployment
# Multi-stage: build deps, then assemble minimal runtime.

# ---- Builder stage ----
FROM python:3.12-slim AS builder
WORKDIR /build

# Copy all source and metadata needed to install the project
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY config/ ./config/

# Install project and its dependencies (production only)
RUN pip install uv --quiet && uv sync --no-dev --no-editable

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
COPY scripts/ /app/scripts/
COPY litestream.yml /etc/litestream.yml

ENV PYTHONPATH=/app
EXPOSE 8080
ENTRYPOINT ["/app/scripts/start.sh"]
