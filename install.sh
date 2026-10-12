#!/usr/bin/bash
# Install Endura system-wide. Re-runnable. Remove with ./uninstall.sh
set -euo pipefail
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }

need_py() { python3 -c "import $1" 2>/dev/null; }
if ! need_py PyQt6 || ! need_py pulsectl || ! need_py dbus || ! need_py gi || ! command -v gcc >/dev/null; then
  if command -v dnf >/dev/null; then
    dnf -y install python3-pyqt6 python3-pulsectl python3-dbus python3-gobject gcc pulseaudio-utils
  elif command -v apt-get >/dev/null; then
    apt-get install -y python3-pyqt6 python3-pulsectl python3-dbus python3-gi gcc pulseaudio-utils
  elif command -v pacman >/dev/null; then
    pacman -S --needed --noconfirm python-pyqt6 python-pulsectl python-dbus python-gobject gcc libpulse
  else
    echo "install PyQt6, pulsectl, dbus-python, PyGObject, gcc and parec (pulseaudio-utils) yourself"; exit 1
  fi
fi

install -d /usr/lib/endura/endura/plasma /usr/libexec/endura /var/lib/endura /etc/endura
install -m 0644 endura/*.py /usr/lib/endura/endura/
install -m 0644 endura/plasma/*.js /usr/lib/endura/endura/plasma/
gcc -O2 -o /usr/libexec/endura/burst tools/burst.c
install -m 0755 tools/endura-bench /usr/libexec/endura/endura-bench
install -m 0755 third_party/tscsync/set-mode.sh /usr/libexec/endura/tscsync-set-mode
cat >/usr/bin/endura-gui <<'X'
#!/usr/bin/bash
PYTHONPATH=/usr/lib/endura exec python3 -m endura.gui "$@"
X
chmod 0755 /usr/bin/endura-gui
install -m 0644 packaging/endurad.service /etc/systemd/system/endurad.service
install -d /etc/systemd/user
install -m 0644 packaging/endura-audio-gate.service /etc/systemd/user/endura-audio-gate.service
install -m 0644 packaging/endura-nap.service /etc/systemd/user/endura-nap.service
install -d /usr/share/kwin/scripts/endura-focus/contents/code
install -m 0644 kwin/endura-focus/metadata.json /usr/share/kwin/scripts/endura-focus/metadata.json
install -m 0644 kwin/endura-focus/contents/code/main.js /usr/share/kwin/scripts/endura-focus/contents/code/main.js
install -m 0644 packaging/io.github.endura.desktop /usr/share/applications/io.github.endura.desktop
command -v restorecon >/dev/null && restorecon -R /usr/lib/endura /usr/libexec/endura /usr/bin/endura-gui \
  /etc/systemd/system/endurad.service /etc/systemd/user /usr/share/kwin/scripts/endura-focus 2>/dev/null || true

if [ ! -s /var/lib/endura/burst_ref ]; then
  echo "calibrating the smoothness reference (5 s at full performance)…"
  C=/sys/devices/system/cpu
  if [ -e $C/cpu0/cpufreq/energy_performance_preference ]; then
    epp=$(cat $C/cpu0/cpufreq/energy_performance_preference)
    for f in $C/cpu*/cpufreq; do echo performance >$f/energy_performance_preference 2>/dev/null || true; done
  fi
  /usr/libexec/endura/burst calib >/var/lib/endura/burst_ref
  if [ -n "${epp:-}" ]; then
    for f in $C/cpu*/cpufreq; do echo "$epp" >$f/energy_performance_preference 2>/dev/null || true; done
  fi
fi
systemctl daemon-reload
systemctl enable --now endurad
systemctl restart endurad
echo "installed. Open \"Endura\" from the menu or run: endura-gui"
