#!/bin/bash
# Startup wrapper: restore DB → start Litestream replicate → launch app → drain/flush on shutdown
set -euo pipefail

LITESTREAM_CONFIG="${LITESTREAM_CONFIG:-/etc/litestream.yml}"

# 1. Restore DB if backup exists (best-effort)
if [ -n "${LITESTREAM_BUCKET:-}" ] && [ -n "${LITESTREAM_ACCESS_KEY_ID:-}" ] && [ -n "${LITESTREAM_SECRET_ACCESS_KEY:-}" ]; then
    echo "[start.sh] Restoring database from Litestream backup..."
    litestream restore -if-replica-exists -config "$LITESTREAM_CONFIG" /data/kb.db 2>&1 || echo "[start.sh] No backup found — will create fresh DB."
fi

# 2. Start Litestream replication in background if configured
if [ -n "${LITESTREAM_BUCKET:-}" ]; then
    echo "[start.sh] Starting Litestream replication..."
    litestream replicate -config "$LITESTREAM_CONFIG" &
    LITESTREAM_PID=$!
fi

# 3. Launch the app
echo "[start.sh] Launching cloud entry point..."
python -m mcp_server.cloud_entry &
APP_PID=$!

# 4. Trap SIGTERM: drain uvicorn → flush Litestream → exit
cleanup() {
    echo "[start.sh] Shutting down — forwarding SIGTERM to app..."
    kill -TERM "$APP_PID" 2>/dev/null
    wait "$APP_PID" 2>/dev/null || true
    echo "[start.sh] App drained."
    if [ -n "${LITESTREAM_PID:-}" ]; then
        echo "[start.sh] Running final Litestream flush..."
        kill -TERM "$LITESTREAM_PID" 2>/dev/null
        wait "$LITESTREAM_PID" 2>/dev/null || true
        # Force a final snapshot
        litestream snapshot -config "$LITESTREAM_CONFIG" /data/kb.db 2>/dev/null || true
        echo "[start.sh] Litestream flushed."
    fi
    echo "[start.sh] Exit."
    exit 0
}
trap cleanup SIGTERM

# 5. Wait for the app (the main process)
wait "$APP_PID"
