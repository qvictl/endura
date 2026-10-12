#!/usr/bin/bash
# Revert every tweak, then remove Endura.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
if [ -S /run/endura/endura.sock ]; then
  python3 - <<'P'
import socket
s = socket.socket(socket.AF_UNIX); s.settimeout(900); s.connect("/run/endura/endura.sock")
s.sendall(b'{"op":"revert_all"}\n'); s.makefile().readline(); print("all tweaks reverted")
P
fi
systemctl disable --now endurad || true
rm -rf /usr/lib/endura /usr/libexec/endura /usr/bin/endura-gui /etc/systemd/system/endurad.service \
  /etc/systemd/user/endura-audio-gate.service /etc/systemd/user/endura-nap.service \
  /usr/share/kwin/scripts/endura-focus /usr/share/applications/io.github.endura.desktop
systemctl daemon-reload
echo "removed (config kept in /etc/endura, telemetry and backups in /var/lib/endura)"
