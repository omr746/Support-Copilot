#!/usr/bin/env bash
# scripts/check_docker.sh — Validate Docker build, compose orchestration, and container security
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-support-copilot:ci}"
MAX_SIZE_MB="${MAX_SIZE_MB:-400}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.ci.yml}"
PORT="${PORT:-8000}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-ci_test_password}"

cleanup() {
    echo "=== Cleaning up Docker Compose services ==="
    docker compose -f "$COMPOSE_FILE" down -v --remove-orphans || true
}
trap cleanup EXIT

echo "=== 1. Building multi-stage Docker image ($IMAGE_NAME) ==="
docker build -t "$IMAGE_NAME" .

echo "=== 2. Verifying image size limit (<= ${MAX_SIZE_MB}MB) ==="
size_bytes=$(docker image inspect "$IMAGE_NAME" --format='{{.Size}}')
size_mb=$(( size_bytes / 1024 / 1024 ))
echo "Measured image size: ${size_mb} MB (enforced limit: ${MAX_SIZE_MB} MB)"
if [ "$size_mb" -gt "$MAX_SIZE_MB" ]; then
    echo "ERROR: Image size ${size_mb}MB exceeds limit of ${MAX_SIZE_MB}MB"
    exit 1
fi
echo "[OK] Image size constraint satisfied: ${size_mb}MB <= ${MAX_SIZE_MB}MB"

echo "=== 3. Starting services with $COMPOSE_FILE ==="
docker compose -f "$COMPOSE_FILE" down -v --remove-orphans || true
docker compose -f "$COMPOSE_FILE" up -d --wait

echo "=== 4. Verifying container runs as non-root user ==="
uid=$(docker compose -f "$COMPOSE_FILE" exec -T app id -u)
username=$(docker compose -f "$COMPOSE_FILE" exec -T app whoami)
echo "Container user: $username (UID: $uid)"
if [ "$uid" -eq 0 ]; then
    echo "ERROR: Security violation: Container is running as root (UID 0)!"
    exit 1
fi
echo "[OK] Non-root user verified (UID $uid)."

echo "=== 5. Verifying ffmpeg installation ==="
ffmpeg_version=$(docker compose -f "$COMPOSE_FILE" exec -T app ffmpeg -version | head -n 1)
echo "Installed ffmpeg: $ffmpeg_version"
if [[ "$ffmpeg_version" != *"ffmpeg version"* ]]; then
    echo "ERROR: ffmpeg is not properly installed in the container"
    exit 1
fi
echo "[OK] ffmpeg verified."

echo "=== 6. Querying /healthz probe ==="
healthz_resp=$(curl -fsS "http://localhost:${PORT}/healthz")
echo "/healthz response: $healthz_resp"
if [[ "$healthz_resp" != *"healthy"* ]]; then
    echo "ERROR: /healthz probe did not return healthy status"
    exit 1
fi
echo "[OK] /healthz returned HTTP 200 with healthy payload."

echo "=== 7. Querying /readyz probe ==="
readyz_resp=$(curl -fsS "http://localhost:${PORT}/readyz")
echo "/readyz response: $readyz_resp"
if [[ "$readyz_resp" != *"ready"* ]]; then
    echo "ERROR: /readyz probe did not return ready status"
    exit 1
fi
echo "[OK] /readyz returned HTTP 200 with ready payload."

echo "=== [SUCCESS] All Step 1.8 Docker checks passed successfully! ==="

