#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -x ".venv/bin/python" ]; then
  echo "[ERROR] Run install_demo_env.sh first." >&2
  exit 1
fi

. ".venv/bin/activate"
python -m pip install -r requirements-dev.txt

echo
echo "[DONE] Test and development dependencies installed."
