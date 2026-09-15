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

echo "DAS Photo Tools"
echo "Listening: http://${HOST}:${PORT}"
echo "Project:   $(pwd)"
echo "Photo dir: ${DAS_PHOTO_ROOT:-../photos}"

exec "${PYTHON}" downloader.py --host "${HOST}" --port "${PORT}"
