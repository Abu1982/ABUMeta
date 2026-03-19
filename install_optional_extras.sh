#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -x ".venv/bin/python" ]; then
  echo "[ERROR] Run install_demo_env.sh first." >&2
  exit 1
fi

. ".venv/bin/activate"
python -m pip install -r requirements-optional.txt
python -m playwright install chromium

echo
echo "[DONE] Optional extras installed."
case "$(uname -s)" in
  Linux*)
    echo "[NOTE] If Chromium is missing system libraries on Linux, rerun:"
    echo "       sudo .venv/bin/python -m playwright install --with-deps chromium"
    ;;
esac
