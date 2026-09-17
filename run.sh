#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

HOST="${DAS_PHOTO_HOST:-0.0.0.0}"
PORT="${DAS_PHOTO_PORT:-8787}"
OPEN_BROWSER="${DAS_PHOTO_OPEN_BROWSER:-1}"
URL="http://127.0.0.1:${PORT}"

echo
echo "DAS Photo Tools"
echo "Downloader: ${URL}"
echo "Labeler:    ${URL}/labeler"
echo "Project:    $(pwd)"
echo "Listening:  http://${HOST}:${PORT}"
echo

if [ "$OPEN_BROWSER" = "1" ] && command -v xdg-open >/dev/null 2>&1; then
  xdg-open "${URL}" >/dev/null 2>&1 || true
fi

exec "${PYTHON}" downloader.py --host "${HOST}" --port "${PORT}"
