"""Hide/show Plasma panel system-monitor widgets (run as the desktop user).

Usage: python3 -m endura.panel hide|show|status
The snapshot of removed widgets (type, full config, panel order) is kept in
~/.local/state/endura/panel-widgets.json so `show` restores them exactly.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent / "plasma"
STATE = Path.home() / ".local/state/endura/panel-widgets.json"
APPLETSRC = Path.home() / ".config/plasma-org.kde.plasma.desktop-appletsrc"


def evaluate(script):
    qdbus = next((q for q in ("qdbus-qt6", "qdbus6", "qdbus") if
                  subprocess.run(["sh", "-c", f"command -v {q}"], capture_output=True).returncode == 0), None)
    p = subprocess.run([qdbus, "org.kde.plasmashell", "/PlasmaShell",
                        "org.kde.PlasmaShell.evaluateScript", script],
                       capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise RuntimeError(p.stderr.strip())
    return p.stdout.strip()


def hide():
    if STATE.exists():
        return "already hidden"
    STATE.parent.mkdir(parents=True, exist_ok=True)
    backup = STATE.with_name("appletsrc.before-hide")
    backup.write_bytes(APPLETSRC.read_bytes())
    out = evaluate((HERE / "hide.js").read_text())
    snap = json.loads(out.splitlines()[-1])
    # written even when empty so the daemon knows this session is handled
    STATE.write_text(json.dumps(snap, ensure_ascii=False, indent=1))
    # Removed applets linger (Plasma keeps them for "undo") and keep polling
    # the sensor daemon; stop it and runtime-mask it so they cannot revive it.
    subprocess.run(["systemctl", "--user", "mask", "--runtime", "plasma-ksystemstats"], timeout=30)
    subprocess.run(["systemctl", "--user", "stop", "plasma-ksystemstats"], timeout=30)
    if not snap["panels"]:
        return "nothing to hide"
    return f"hidden {sum(len(p['widgets']) for p in snap['panels'])} widgets"


def show():
    if not STATE.exists():
        return "nothing hidden"
    subprocess.run(["systemctl", "--user", "unmask", "--runtime", "plasma-ksystemstats"], timeout=30)
    snap = STATE.read_text()
    if not json.loads(snap)["panels"]:
        STATE.unlink()
        return "nothing hidden"
    out = evaluate((HERE / "show.js").read_text().replace("__ENDURA_SNAPSHOT__", snap, 1))
    STATE.unlink()
    # Plasma applies AppletOrder only when the panel loads, and plasmashell
    # writes its in-memory order back on exit. So: stop it (config flushed),
    # write the original order into the file, start it again (~2 s blink).
    orders = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0] == "panel" and parts[2] == "order":
            orders[parts[1]] = parts[3]
    subprocess.run(["systemctl", "--user", "stop", "plasma-plasmashell"], timeout=60)
    for pid, order in orders.items():
        subprocess.run(["kwriteconfig6", "--file", str(APPLETSRC), "--group", "Containments",
                        "--group", pid, "--group", "General", "--key", "AppletOrder", order],
                       timeout=10)
    subprocess.run(["systemctl", "--user", "start", "plasma-plasmashell"], timeout=60)
    return out or "shown"


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "hide":
        print(hide())
    elif cmd == "show":
        print(show())
    else:
        print("hidden" if STATE.exists() else "shown")


if __name__ == "__main__":
    main()
