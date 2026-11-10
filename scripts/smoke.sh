#!/usr/bin/env bash
# scripts/smoke.sh — Smoke test against live deployment (Plan Step 1.10)
# Usage: bash scripts/smoke.sh https://<your-service>.onrender.com
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <base_url>"
    echo "Example: $0 https://support-copilot.onrender.com"
    exit 1
fi

TARGET_URL="${1%/}"
MAX_WARMUP_ATTEMPTS=15
WARMUP_SLEEP_SEC=5

echo "============================================================"
echo " Support Copilot — Smoke Test Suite (Step 1.10)"
echo " Target URL: $TARGET_URL"
echo "============================================================"

# 1. Warm-up probe (Render free tier sleeps when idle and needs 30-60s to wake up)
echo "=== 1. Probing service availability (handling potential cold start) ==="
attempt=1
service_ready=false

while [ $attempt -le $MAX_WARMUP_ATTEMPTS ]; do
    echo "Attempt $attempt of $MAX_WARMUP_ATTEMPTS: probing $TARGET_URL/healthz..."
    if curl -fsS --max-time 10 "$TARGET_URL/healthz" > /dev/null 2>&1; then
        service_ready=true
        echo "[OK] Service is online and reachable."
        break
    fi
    echo "Service still warming up, waiting ${WARMUP_SLEEP_SEC}s..."
    sleep $WARMUP_SLEEP_SEC
    attempt=$(( attempt + 1 ))
done

if [ "$service_ready" = false ]; then
    echo "ERROR: Service at $TARGET_URL failed to wake up after $(( MAX_WARMUP_ATTEMPTS * WARMUP_SLEEP_SEC )) seconds."
    exit 1
fi

# 2. Check /healthz (Liveness)
echo "=== 2. Testing Liveness probe (/healthz) ==="
health_resp=$(curl -fsS --max-time 10 "$TARGET_URL/healthz")
echo "Response: $health_resp"
if [[ "$health_resp" != *"healthy"* ]]; then
    echo "ERROR: /healthz did not return healthy status."
    exit 1
fi
echo "[OK] /healthz returned HTTP 200 healthy."

# 3. Check /readyz (Readiness)
echo "=== 3. Testing Readiness probe (/readyz) ==="
ready_resp=$(curl -fsS --max-time 10 "$TARGET_URL/readyz")
echo "Response: $ready_resp"
if [[ "$ready_resp" != *"ready"* ]]; then
    echo "ERROR: /readyz did not return ready status."
    exit 1
fi
echo "[OK] /readyz returned HTTP 200 ready."

# 4. Check /ask endpoint
echo "=== 4. Testing Question-Answering endpoint (/ask) ==="
ask_payload='{"question": "What services does Support Copilot offer?"}'
ask_resp=$(curl -fsS --max-time 30 -X POST "$TARGET_URL/ask" \
    -H "Content-Type: application/json" \
    -H "X-Request-ID: smoke-test-$(date +%s)" \
    -d "$ask_payload")

echo "Response: $ask_resp"
if [[ "$ask_resp" != *"answer"* ]]; then
    echo "ERROR: /ask response did not contain an 'answer' field."
    exit 1
fi
echo "[OK] /ask returned HTTP 200 with valid completion response."

echo "============================================================"
echo " [SUCCESS] All smoke tests passed against $TARGET_URL!"
echo "============================================================"

