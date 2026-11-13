#!/usr/bin/env bash
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
destination="${XDG_DATA_HOME:-$HOME/.local/share}/jarvis-v10"
exec python3 "$source_dir/installer.py" --source "$source_dir" --destination "$destination" "$@"
