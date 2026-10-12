#!/usr/bin/bash
# Revert every tweak, then remove Endura.
set -euo pipefail
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
exec /usr/bin/python3 -c 'import sys; sys.path.insert(0, "."); from endura.installer import uninstall; uninstall()'
