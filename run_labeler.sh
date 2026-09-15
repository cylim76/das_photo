#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

export LABELER_HOST="${LABELER_HOST:-0.0.0.0}"
export LABELER_PORT="${LABELER_PORT:-8765}"
URL="http://127.0.0.1:${LABELER_PORT}/labeler"

echo
echo "Container Photo Labeler"
echo "Local URL: ${URL}"
echo "LAN URL: http://YOUR-SERVER-IP:${LABELER_PORT}/labeler"
echo "Project: $(pwd)"
echo

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "${URL}" >/dev/null 2>&1 || true
fi

exec "${PYTHON}" labeler/server.py
