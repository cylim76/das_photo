#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

URL="http://127.0.0.1:8787"

echo
echo "DAS Photo Downloader"
echo "Local URL: ${URL}"
echo "LAN URL: http://YOUR-SERVER-IP:8787"
echo "Project: $(pwd)"
echo

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "${URL}" >/dev/null 2>&1 || true
fi

exec "${PYTHON}" downloader.py --host 0.0.0.0 --port 8787
