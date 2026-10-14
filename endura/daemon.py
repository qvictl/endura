"""endurad: applies enabled tweaks, follows AC/battery, records telemetry,
serves a JSON-lines API on /run/endura/endura.sock (group wheel)."""
import grp
import json
import logging
import os
import signal
import socket
import threading
import time
from pathlib import Path

from . import tweaks as T
from .i18n import set_lang
from .telemetry import Recorder
from .util import CONFIG_PATH, on_battery, run

SOCK = Path("/run/endura/endura.sock")
log = logging.getLogger("endurad")

from .defaults import OPTIONS as DEFAULT_OPTS


class Daemon:
    def __init__(self):
        self.lock = threading.RLock()
        self.cfg = self.load_cfg()
        self.tweaks = T.build(self.cfg["options"])
        self.rec = Recorder()
        set_lang(self.cfg["options"].get("language"))
        if "busy_watchdog" in self.tweaks:
            self.tweaks["busy_watchdog"].recorder = self.rec
            self.tweaks["busy_watchdog"].on_mute = self.mute_app
        self.running = True
        self.last_bat = None
        self.errors = {}
        self.probe = {"state": "idle"}
        self.status_cache = {}   # tweak id -> (monotonic ts, status dict)

    # ---------------------------------------------------------- config --
    def load_cfg(self):
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except (OSError, ValueError):
            cfg = {}
        cfg.setdefault("enabled", {})
        opts = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_OPTS.items()}
        opts.update(cfg.get("options", {}))
        cfg["options"] = opts
        return cfg

    def save_cfg(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        out = dict(self.cfg)
        out["options"] = {k: v for k, v in self.cfg["options"].items() if DEFAULT_OPTS.get(k) != v}
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False))
        os.replace(tmp, CONFIG_PATH)

    def is_enabled(self, t):
        return self.cfg["enabled"].get(t.id, t.default)

    # ----------------------------------------------------------- apply --
    def quiet(self):
        t = self.tweaks.get("ac_quiet")
        return bool(t and self.is_available(t) and self.is_enabled(t))

    def write_mode(self, bat):
        mode = "battery" if bat else ("ac-quiet" if self.quiet() else "ac")
        p = Path("/run/endura/mode")
        if not p.exists() or p.read_text() != mode:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(mode)
            os.chmod(p, 0o644)

    def apply_all(self, force=False):
        bat = on_battery()
        quiet = self.quiet()
        changed_power = bat != self.last_bat or quiet != getattr(self, "last_quiet", None)
        self.last_bat, self.last_quiet = bat, quiet
        self.write_mode(bat)
        with self.lock:
            for t in self.tweaks.values():
                if not self.is_available(t):
                    continue
                # reboot-scoped tweaks edit boot config: only on explicit user
                # action (set_enabled), never re-asserted, so a firmware hang
                # guard that turned one off is respected.
                if t.reboot:
                    continue
                # spawning systemctl every cycle wakes PID 1: do those only
                # at start-up and on AC/battery changes
                if not t.periodic and not (force or changed_power):
                    continue
                try:
                    if self.is_enabled(t):
                        eff = bat or (quiet and t.quiet_ac)   # quiet mode: AC acts like battery
                        if t.battery_only and not eff:
                            if changed_power or force:
                                t.revert()
                        else:
                            t.ensure(eff if t.quiet_ac else bat)
                    self.errors.pop(t.id, None)
                except Exception as e:  # keep the loop alive
                    self.errors[t.id] = str(e)
                    log.exception("tweak %s", t.id)

    def set_enabled(self, tid, on):
        t = self.tweaks[tid]
        self.status_cache.pop(tid, None)
        self.__dict__.setdefault("avail_cache", {}).pop(tid, None)
        with self.lock:
            self.cfg["enabled"][tid] = bool(on)
            self.save_cfg()
            if on:
                bat = on_battery()
                eff = bat or (self.quiet() and t.quiet_ac)
                if not (t.battery_only and not eff):
                    t.ensure(eff if t.quiet_ac else bat)
            else:
                t.revert()
        if tid == "ac_quiet":
            self.apply_all(force=True)     # re-evaluate every quiet-capable tweak

    # options a GUI client may change, with their validators
    SETTABLE = {
        "battery_epp": lambda v: v if v in ("power", "balance_power", "balance_performance", "performance") else None,
        "battery_boost": bool,
        "abm_level": lambda v: min(4, max(1, int(v))),
        "language": lambda v: v if v in ("auto", "en", "zh") else None,
        "budget_w": lambda v: min(200.0, max(0.0, round(float(v), 2))),
        "alert_app_w": lambda v: min(100.0, max(0.0, round(float(v), 2))),
        "alert_minutes": lambda v: min(120, max(1, int(v))),
        "alert_wakeups": lambda v: min(100000, max(0, int(v))),
        "alert_on_ac": bool,
        "alert_ignore": lambda v: sorted({str(x).strip()[:128] for x in v if str(x).strip()})[:200],
    }

    def set_option(self, key, value):
        if key not in self.SETTABLE:
            raise ValueError(f"option {key!r} cannot be changed here")
        value = self.SETTABLE[key](value)
        if value is None:
            raise ValueError(f"bad value for {key}")
        with self.lock:
            self.cfg["options"][key] = value
            self.save_cfg()
        if key == "language":
            set_lang(value)
        self.status_cache.clear()
        self.apply_all()

    def mute_app(self, app):
        """'Never alert for this app' clicked in a notification."""
        cur = list(self.cfg["options"].get("alert_ignore") or [])
        if app not in cur:
            self.set_option("alert_ignore", cur + [app])

    # ----------------------------------------------------------- probe --
    def app_cgroups(self, name):
        from .telemetry import app_name, CG
        out = []
        for root, dirs, files in os.walk(CG):
            leaf = root.rsplit("/", 1)[-1]
            if (leaf.endswith(".scope") or leaf.endswith(".service")) and \
                    not leaf.startswith("user@") and app_name(root) == name:
                out.append(Path(root))
                dirs[:] = []
        return out

    def start_probe(self, name, rounds, win):
        if self.probe.get("state") == "running":
            return {"error": "probe already running"}
        cgs = [c for c in self.app_cgroups(name) if (c / "cgroup.freeze").exists()]
        protected = ("endurad", "session", "plasma", "kwin", "sshd", "pipewire", "dbus", "systemd")
        if not cgs or any(p in name for p in protected):
            return {"error": f"cannot freeze '{name}'"}
        self.probe = {"state": "running", "name": name, "rounds": [], "total": rounds}
        threading.Thread(target=self._probe, args=(cgs, rounds, win), daemon=True).start()
        return self.probe

    def _probe(self, cgs, rounds, win):
        from .telemetry import battery, soc_power

        def measure():
            b, s = [], []
            for _ in range(win):
                time.sleep(1)
                v = battery()["w"]
                if v is not None:
                    b.append(v)
                sp = soc_power()
                if sp and sp[0] is not None:
                    s.append(sp[0])
            return sum(b) / max(1, len(b)), sum(s) / max(1, len(s))

        def freeze(on):
            for c in cgs:
                try:
                    (c / "cgroup.freeze").write_text("1" if on else "0")
                except OSError:
                    pass
        try:
            for _ in range(rounds):
                freeze(False); time.sleep(3)
                a = measure()
                freeze(True); time.sleep(3)
                b = measure()
                freeze(False)
                self.probe["rounds"].append({"running_w": a[0], "frozen_w": b[0],
                                             "running_sock": a[1], "frozen_sock": b[1]})
            d = [r["running_w"] - r["frozen_w"] for r in self.probe["rounds"]]
            self.probe.update(state="done", saving_w=sum(d) / len(d), deltas=d)
        except Exception as e:
            self.probe.update(state="error", error=str(e))
        finally:
            freeze(False)

    # ------------------------------------------------------------- api --
    def status(self):
        out = []
        for t in self.tweaks.values():
            d = t.info()
            d["available"] = self.is_available(t)
            d["enabled"] = self.is_enabled(t)
            try:
                d.update(self.cached_status(t) if d["available"] else {})
            except Exception as e:
                d["detail"] = f"status error: {e}"
            if t.id in self.errors:
                d["error"] = self.errors[t.id]
            out.append(d)
        opts = {k: self.cfg["options"].get(k) for k in self.SETTABLE}
        return {"tweaks": out, "options": opts, "now": self.rec.last}

    def is_available(self, t, ttl=300):
        """available() probes spawn processes (systemctl show, command -v ...);
        cache them so a GUI polling every 5 s does not hammer PID 1 / D-Bus."""
        cache = self.__dict__.setdefault("avail_cache", {})
        ts, v = cache.get(t.id, (0, None))
        if v is None or time.monotonic() - ts > ttl:
            v = bool(t.available())
            cache[t.id] = (time.monotonic(), v)
        return v

    def cached_status(self, t):
        """Status of tweaks that spawn processes (systemctl, grub2-editenv) is
        cached for a minute so an open GUI does not keep waking PID 1."""
        if t.periodic and not t.reboot:
            return t.status()
        ts, st = self.status_cache.get(t.id, (0, None))
        if st is None or time.monotonic() - ts > 60:
            st = t.status()
            self.status_cache[t.id] = (time.monotonic(), st)
        return st

    def handle(self, req):
        op = req.get("op")
        if op == "status":
            return self.status()
        if op == "set":
            self.set_enabled(req["id"], req["enabled"])
            return self.status()
        if op == "option":
            try:
                self.set_option(req["key"], req["value"])
            except (ValueError, TypeError) as e:
                return {"error": str(e)}
            return self.status()
        if op == "history":
            return {"history": self.rec.history(req.get("seconds", 3600))}
        if op == "apps":
            return self.rec.app_summary(req.get("seconds", 3600), req.get("battery_only", True))
        if op == "now":
            return self.rec.last
        if op == "probe_app":
            return self.start_probe(req["name"], int(req.get("rounds", 3)), int(req.get("win", 20)))
        if op == "probe_result":
            return self.probe
        if op == "revert_all":
            for t in self.tweaks.values():
                if self.is_available(t) and self.is_enabled(t):
                    self.set_enabled(t.id, False)
            return self.status()
        return {"error": f"unknown op {op}"}

    def serve(self):
        SOCK.parent.mkdir(parents=True, exist_ok=True)
        SOCK.unlink(missing_ok=True)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(SOCK))
        # world-connectable; every connection is authorised by peer credentials
        os.chmod(SOCK, 0o666)
        s.listen(8)
        while self.running:
            conn, _ = s.accept()
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    ADMIN_GROUPS = ("wheel", "sudo", "admin")

    def authorised(self, conn):
        """root, the user of the graphical seat0 session, or an admin-group member."""
        import struct
        from .util import desktop_user
        pid, uid, gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                            struct.calcsize("3i")))
        if uid == 0 or uid == desktop_user()[1]:
            return True
        try:
            import pwd
            name = pwd.getpwuid(uid).pw_name
        except KeyError:
            return False
        for g in self.ADMIN_GROUPS:
            try:
                gr = grp.getgrnam(g)
            except KeyError:
                continue
            if name in gr.gr_mem or gid == gr.gr_gid:
                return True
        return False

    def _client(self, conn):
        if not self.authorised(conn):
            with conn:
                conn.sendall(b'{"error": "not authorised: run the GUI as the desktop user or an admin"}\n')
            return
        with conn, conn.makefile("rwb") as f:
            for line in f:
                try:
                    resp = self.handle(json.loads(line))
                except Exception as e:
                    resp = {"error": str(e)}
                f.write((json.dumps(resp, ensure_ascii=False) + "\n").encode())
                f.flush()

    # ------------------------------------------------------------ main --
    def run(self):
        signal.signal(signal.SIGTERM, lambda *a: setattr(self, "running", False))
        threading.Thread(target=self.serve, daemon=True).start()
        from .inputwatch import InputWatcher
        InputWatcher().start()
        self.apply_all(force=True)
        t = self.tweaks.get("tsc_sync")
        if t and self.is_available(t) and self.is_enabled(t):
            try:
                t.install_hook()          # keep the resume-from-hibernation hook in place
            except OSError:
                pass
        self.rec.tick_fast()
        self.rec.flush()          # so clients have data right away
        # 5 s loop: power_supply reads are ACPI EC transactions, so keep them rare
        n = 0
        while self.running:
            time.sleep(5)
            n += 5
            self.rec.tick_fast()
            if on_battery() != self.last_bat:
                self.apply_all()
            for t in self.tweaks.values():
                if hasattr(t, "tick") and self.is_enabled(t):
                    try:
                        t.tick()
                    except Exception:
                        log.exception("tick %s", t.id)
            if n % self.rec.interval == 0:
                self.rec.flush()
            if n % 120 == 0:
                self.rec.flush_wakeups()
            if n % 30 == 0:
                self.apply_all()  # re-assert (power-profiles-daemon may override)
        self.rec.maybe_commit(time.time(), force=True)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    Daemon().run()


if __name__ == "__main__":
    main()
