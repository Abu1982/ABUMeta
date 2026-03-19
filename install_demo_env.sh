#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if command -v python3 >/dev/null 2>&1; then
  PY_CMD="python3"
elif command -v python >/dev/null 2>&1; then
  PY_CMD="python"
else
  echo "[ERROR] No Python interpreter found. Install Python 3.11+ first." >&2
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  "$PY_CMD" -m venv .venv
fi

. ".venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r requirements-demo.txt

echo
echo "[DONE] Base demo dependencies installed."
echo "Run bash install_optional_extras.sh for optional browser/vector features."
echo "Run bash install_dev_env.sh for test/dev dependencies."
