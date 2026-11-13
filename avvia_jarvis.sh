#!/usr/bin/env bash
set -euo pipefail
JARVIS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$JARVIS_DIR"
if [[ -x "$JARVIS_DIR/venv/bin/python" ]]; then
    exec "$JARVIS_DIR/venv/bin/python" "$JARVIS_DIR/jarvis_v10.py" "$@"
fi
exec python3 "$JARVIS_DIR/jarvis_v10.py" "$@"
