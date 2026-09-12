#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

DEFAULT_CODEX_PYTHON="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [ -x "$DEFAULT_CODEX_PYTHON" ]; then
  PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_CODEX_PYTHON}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi
FRONTEND_PORT="${FRONTEND_PORT:-8000}"
API_PORT="${API_PORT:-8766}"
JOBS_DIR="${JOBS_DIR:-/tmp/ppt-master-api-jobs}"
STORE="${STORE:-json}"

if [ "${PUBLIC_HOST:-0}" = "1" ]; then
  HOST="${HOST:-0.0.0.0}"
else
  HOST="${HOST:-127.0.0.1}"
fi

if [ "$HOST" = "0.0.0.0" ]; then
  LOCAL_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")"
else
  LOCAL_IP="127.0.0.1"
fi

FRONTEND_BASE_URL="${PPT_MASTER_FRONTEND_BASE_URL:-http://${LOCAL_IP}:${FRONTEND_PORT}}"
API_BASE_URL="${API_BASE_URL:-http://${LOCAL_IP}:${API_PORT}}"
ACCESS_TOKEN="${PPT_MASTER_ACCESS_TOKEN:-}"

if [ "${PUBLIC_HOST:-0}" = "1" ] && [ -z "$ACCESS_TOKEN" ]; then
  echo "Refusing public bind without PPT_MASTER_ACCESS_TOKEN. Keep PUBLIC_HOST=0 for private previews." >&2
  exit 1
fi

if [ "${PPT_MASTER_DEPLOYMENT:-}" = "production" ] || [ "${PPT_MASTER_DEPLOYMENT:-}" = "prod" ]; then
  PYTHONPATH="skills/ppt-master/scripts" "$PYTHON_BIN" scripts/check-cloud-generator-readiness.py
fi

cleanup() {
  if [ -n "${API_PID:-}" ]; then kill "$API_PID" 2>/dev/null || true; fi
  if [ -n "${WEB_PID:-}" ]; then kill "$WEB_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

echo "Starting PPT Master local public preview..."
echo "Frontend host: ${HOST}:${FRONTEND_PORT}"
echo "API host:      ${HOST}:${API_PORT}"
echo "Jobs dir:      ${JOBS_DIR}"
echo "Access token:  $([ -n "$ACCESS_TOKEN" ] && echo enabled || echo disabled)"

"$PYTHON_BIN" -m http.server "$FRONTEND_PORT" --bind "$HOST" &
WEB_PID="$!"

API_ARGS=(
  -m cloud_generator.api_server
  --host "$HOST" \
  --port "$API_PORT" \
  --jobs-dir "$JOBS_DIR" \
  --store "$STORE" \
  --frontend-base-url "$FRONTEND_BASE_URL"
)
if [ -n "$ACCESS_TOKEN" ]; then
  API_ARGS+=(--access-token "$ACCESS_TOKEN")
fi

PYTHONPATH="skills/ppt-master/scripts" "$PYTHON_BIN" "${API_ARGS[@]}" &
API_PID="$!"

TOKEN_PARAM=""
if [ -n "$ACCESS_TOKEN" ]; then
  TOKEN_PARAM="&token=${ACCESS_TOKEN}"
fi

echo
echo "Open:"
echo "  ${FRONTEND_BASE_URL}/cloud-generator.html?lang=zh-CN&api=${API_BASE_URL}${TOKEN_PARAM}"
echo
echo "Press Ctrl+C to stop both services."
wait
