"""Power telemetry: battery, SoC (SMU gpu_metrics), CPU busy, per-app CPU time."""
import glob
import os
import re
import sqlite3
import struct
import time
from pathlib import Path

from . import hw
from .util import read, STATE_DIR

DB = STATE_DIR / "telemetry.db"
CG = Path("/sys/fs/cgroup")


def soc_power():
    """(socket_w, soc_w, cpu_w, gfx_activity%) from AMD APU gpu_metrics v2.x.
    Returns None on other hardware."""
    p = hw.gpu_metrics_path()
    if not p:
        return None
    try:
        b = open(p, "rb").read()
        _, frev, _ = struct.unpack_from("<HBB", b, 0)
        if frev != 2:
            return None
        act = struct.unpack_from("<H", b, 28)[0]
        sock, _, soc, gfx = struct.unpack_from("<4H", b, 40)
        f = lambda v: None if v == 0xFFFF else v / 1000
        return f(sock), f(soc), f(gfx), (None if act == 0xFFFF else act / 100)
    except (OSError, TypeError, struct.error):
        return None


def battery():
    return hw.battery_info()


def cpu_jiffies():
    f = list(map(int, open("/proc/stat").readline().split()[1:]))
    return sum(f) - f[3] - f[4], sum(f)


_NAME_RE = [
    (re.compile(r"app-flatpak-([\w.]+?)-\d+\.scope"), r"\1"),
    (re.compile(r"app-(?:gnome|kde)?-?([\w.\\x-]+?)@[0-9a-f]+\.service"), r"\1"),
    (re.compile(r"app-([\w.\\x-]+?)-\d+\.scope"), r"\1"),
    (re.compile(r"app-([\w.\\x-]+?)@autostart\.service"), r"\1"),
    (re.compile(r"(?:plasma-)?([\w.@\\x-]+?)\.(?:service|scope)"), r"\1"),
]


def app_name(cg):
    leaf = cg.rstrip("/").split("/")[-1]
    for rx, rep in _NAME_RE:
        m = rx.fullmatch(leaf)
        if m:
            return m.expand(rep).replace("\\x2d", "-")
    return leaf


def app_cpu_usec():
    """usage_usec of every leaf .scope/.service cgroup, grouped by app name."""
    out = {}
    for root, dirs, files in os.walk(CG):
        leaf = root.rsplit("/", 1)[-1]
        if not (leaf.endswith(".scope") or leaf.endswith(".service")) or \
                re.fullmatch(r"user@\d+\.service", leaf):
            continue
        dirs[:] = []  # do not descend into a unit's own sub-cgroups
        try:
            for line in open(os.path.join(root, "cpu.stat")):
                if line.startswith("usage_usec"):
                    name = app_name(root)
                    out[name] = out.get(name, 0) + int(line.split()[1])
                    break
        except OSError:
            pass
    return out


def wakeups_by_app():
    """Context-switch-in counts (schedstat field 3) of every thread, per app."""
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cg = open(f"/proc/{pid}/cgroup").read().split("::", 1)[-1].strip()
            tasks = os.listdir(f"/proc/{pid}/task")
        except OSError:
            continue
        n = 0
        for t in tasks:
            try:
                n += int(open(f"/proc/{pid}/task/{t}/schedstat").read().split()[2])
            except (OSError, IndexError, ValueError):
                pass
        name = app_name(cg) if cg else "kernel"
        out[name] = out.get(name, 0) + n
    return out


class Recorder:
    """Samples every `interval` s, keeps rolling state and writes to sqlite."""

    def __init__(self, interval=10):
        self.interval = interval
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(DB, check_same_thread=False)
        # telemetry is disposable: no fsync, WAL, and commits batched (see flush)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=OFF")
        self.last_commit = time.monotonic()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sample(ts REAL PRIMARY KEY, on_bat INT, bat_w REAL,
              sock_w REAL, soc_w REAL, gfx_w REAL, cpu_pct REAL, pct INT, profile TEXT);
            CREATE TABLE IF NOT EXISTS app(ts REAL, name TEXT, cpu_pct REAL);
            CREATE INDEX IF NOT EXISTS app_ts ON app(ts);
            CREATE TABLE IF NOT EXISTS wake(ts REAL, name TEXT, per_s REAL);
            CREATE INDEX IF NOT EXISTS wake_ts ON wake(ts);
        """)
        self.prev_j = cpu_jiffies()
        self.prev_apps = app_cpu_usec()
        self.prev_t = time.monotonic()
        self.bat_acc = []
        self.soc_acc = []
        self.last = {}
        self.prev_wake = (time.monotonic(), wakeups_by_app())
        self.wake_rate = {}

    def tick_fast(self):
        """Called every 5 s. Each battery read is an ACPI EC query, so read
        only power_now here; the full battery() is read once per flush."""
        # on AC power_now is the *charge* rate: only record while discharging
        if hw.on_battery():
            w = hw.battery_power_w()
            if w is not None:
                self.bat_acc.append(w)
        s = soc_power()
        if s:
            self.soc_acc.append(s)

    def flush(self, profile=""):
        now = time.monotonic()
        dt = now - self.prev_t
        j = cpu_jiffies()
        busy = (j[0] - self.prev_j[0]) / max(1, j[1] - self.prev_j[1]) * 100
        apps = app_cpu_usec()
        app_pct = {}
        for k, v in apps.items():
            d = v - self.prev_apps.get(k, v)
            if d > 0:
                app_pct[k] = d / (dt * 1e6) * 100  # % of one CPU
        self.prev_j, self.prev_apps, self.prev_t = j, apps, now
        b = battery()
        on_bat = b["status"] == "Discharging"
        avg = lambda xs: sum(xs) / len(xs) if xs else None
        bat_w = avg(self.bat_acc)
        socs = [x for x in self.soc_acc if x[0] is not None]
        sock = avg([x[0] for x in socs])
        soc = avg([x[1] for x in socs if x[1] is not None])
        gfx = avg([x[2] for x in socs if x[2] is not None])
        self.bat_acc, self.soc_acc = [], []
        ts = time.time()
        self.last = {"ts": ts, "on_battery": on_bat, "bat_w": bat_w, "sock_w": sock,
                     "soc_w": soc, "gfx_w": gfx, "cpu_pct": busy, "battery": b,
                     "apps": dict(sorted(app_pct.items(), key=lambda kv: -kv[1])[:25])}
        self.db.execute("INSERT OR REPLACE INTO sample VALUES(?,?,?,?,?,?,?,?,?)",
                        (ts, on_bat, bat_w, sock, soc, gfx, busy, b["pct"], profile))
        self.db.executemany("INSERT INTO app VALUES(?,?,?)",
                            [(ts, k, round(v, 2)) for k, v in app_pct.items() if v >= 0.2])
        self.maybe_commit(ts)
        return self.last

    def maybe_commit(self, ts, force=False):
        """Commit at most every 5 min so the SSD is not woken every 5 s."""
        if force or time.monotonic() - self.last_commit > 300:
            self.db.execute("DELETE FROM sample WHERE ts < ?", (ts - 30 * 86400,))
            self.db.execute("DELETE FROM app WHERE ts < ?", (ts - 30 * 86400,))
            self.db.execute("DELETE FROM wake WHERE ts < ?", (ts - 30 * 86400,))
            self.db.commit()
            self.last_commit = time.monotonic()

    def flush_wakeups(self):
        """Every ~30 s: per-app scheduler wakeups per second."""
        t, w = time.monotonic(), wakeups_by_app()
        t0, w0 = self.prev_wake
        self.prev_wake = (t, w)
        dt = t - t0
        rate = {k: (v - w0.get(k, v)) / dt for k, v in w.items()}
        rate = {k: r for k, r in rate.items() if r >= 1}
        self.wake_rate = dict(sorted(rate.items(), key=lambda kv: -kv[1]))
        ts = time.time()
        self.db.executemany("INSERT INTO wake VALUES(?,?,?)",
                            [(ts, k, round(r, 1)) for k, r in rate.items()])

    def history(self, seconds=3600, step=None):
        since = time.time() - seconds
        rows = self.db.execute(
            "SELECT ts,on_bat,bat_w,sock_w,soc_w,gfx_w,cpu_pct,pct FROM sample WHERE ts>=? ORDER BY ts",
            (since,)).fetchall()
        return [dict(zip(("ts", "on_bat", "bat_w", "sock_w", "soc_w", "gfx_w", "cpu_pct", "pct"), r))
                for r in rows]

    def app_summary(self, seconds=3600, battery_only=True):
        """Average CPU% per app over window and an estimated watt share.

        Watts are attributed by splitting the average (socket - idle floor)
        power by each app's share of total CPU time: a coarse but honest model.
        """
        since = time.time() - seconds
        cond = "AND s.on_bat=1" if battery_only else ""
        n = self.db.execute(f"SELECT COUNT(*), AVG(sock_w), AVG(bat_w) FROM sample s WHERE ts>=? {cond}",
                            (since,)).fetchone()
        count, sock, bat = n
        if not count:
            return {"samples": 0, "apps": []}
        rows = self.db.execute(
            f"SELECT a.name, SUM(a.cpu_pct) FROM app a JOIN sample s ON s.ts=a.ts "
            f"WHERE a.ts>=? {cond} GROUP BY a.name", (since,)).fetchall()
        wk = dict(self.db.execute(
            "SELECT name, AVG(per_s) FROM wake WHERE ts>=? GROUP BY name", (since,)).fetchall())
        total = sum(r[1] for r in rows) or 1
        dyn = max(0.0, (sock or 0) - 2.0)  # ~2 W socket floor when idle
        apps = sorted(({"name": r[0], "cpu_pct": r[1] / count, "wakeups": wk.get(r[0], 0.0),
                        "est_w": dyn * r[1] / total} for r in rows), key=lambda a: -a["cpu_pct"])
        return {"samples": count, "avg_bat_w": bat, "avg_sock_w": sock, "apps": apps[:30]}
