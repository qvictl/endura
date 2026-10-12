"""Small helpers shared by the daemon and tweaks."""
import json
import os
import shutil
import subprocess
from pathlib import Path

STATE_DIR = Path("/var/lib/endura")
BACKUP_DIR = STATE_DIR / "backup"
CONFIG_PATH = Path("/etc/endura/config.json")


def read(path, default=None):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def write(path, value):
    try:
        Path(path).write_text(str(value))
        return True
    except OSError:
        return False


def run(cmd, check=False, **kw):
    """Run a command, return (rc, stdout). Never raises unless check=True."""
    try:
        p = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                           text=True, timeout=kw.pop("timeout", 60), **kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        if check:
            raise
        return 1, str(e)
    if check and p.returncode:
        raise RuntimeError(f"{cmd}: {p.stderr.strip()}")
    return p.returncode, p.stdout


def on_battery():
    from . import hw
    return hw.on_battery()


_du_cache = (0.0, (None, None))


def desktop_user():
    """(name, uid) of the user owning the graphical seat0 session (cached 5 min)."""
    global _du_cache
    import time
    if time.monotonic() - _du_cache[0] < 300 and _du_cache[1][0]:
        return _du_cache[1]
    _du_cache = (time.monotonic(), _desktop_user())
    return _du_cache[1]


def _desktop_user():
    rc, out = run(["loginctl", "list-sessions", "--no-legend", "--json=short"])
    try:
        for s in json.loads(out):
            if s.get("seat") == "seat0":
                return s["user"], int(s["uid"])
    except (ValueError, KeyError, TypeError):
        pass
    return None, None


def user_cmd(args):
    """Command prefix that runs `args` inside the desktop user's session env."""
    user, uid = desktop_user()
    if not user:
        return None
    import glob as _g
    socks = sorted(x for x in _g.glob(f"/run/user/{uid}/wayland-[0-9]*") if not x.endswith(".lock"))
    wl = os.path.basename(socks[0]) if socks else "wayland-0"
    env = [f"XDG_RUNTIME_DIR=/run/user/{uid}", f"WAYLAND_DISPLAY={wl}", "DISPLAY=:0",
           f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus"]
    # setpriv instead of runuser: no PAM session, so no journal entry per call
    import pwd
    try:
        home = pwd.getpwnam(user).pw_dir
    except KeyError:
        home = f"/home/{user}"
    return ["setpriv", f"--reuid={uid}", f"--regid={uid}", "--init-groups",
            "env", f"HOME={home}", f"USER={user}", *env, *args]


class ManagedFile:
    """A config file owned by a tweak: backed up before first write, restored on revert."""

    def __init__(self, path):
        self.path = Path(path)
        self.backup = BACKUP_DIR / (str(self.path).strip("/").replace("/", "__"))
        self.absent_marker = self.backup.with_suffix(".absent")

    def write(self, content):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        if not self.backup.exists() and not self.absent_marker.exists():
            if self.path.exists():
                shutil.copy2(self.path, self.backup)
            else:
                self.absent_marker.touch()
        if read(self.path) == content.strip():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".endura-tmp")
        tmp.write_text(content)
        os.replace(tmp, self.path)
        run(["restorecon", str(self.path)])
        return True

    def restore(self):
        changed = False
        if self.backup.exists():
            shutil.copy2(self.backup, self.path)
            self.backup.unlink()
            changed = True
        elif self.absent_marker.exists():
            self.path.unlink(missing_ok=True)
            self.absent_marker.unlink()
            changed = True
        if changed:
            run(["restorecon", str(self.path)])
        return changed

    @property
    def managed(self):
        return self.backup.exists() or self.absent_marker.exists()
