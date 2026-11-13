#!/usr/bin/env bash
set -euo pipefail
JARVIS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$JARVIS_DIR/installer.py" --mode import "$@"
