"""endura-nap: App Nap for Linux.

Background chat apps (Feishu, WeChat) wake hundreds of times per second even
when nobody looks at them; measured 0.3-0.5 W each with a static screen. On
battery, while an app is not focused (or there was no input for a minute) and it has no
audio/video stream, its cgroup is duty-cycle frozen (NAP_FROZEN s frozen,
NAP_THAWED s running). Network data queues in the kernel and is handled on the
next thaw, so messages/notifications are delayed by at most NAP_FROZEN s.
Focusing the app (KWin script -> D-Bus Focus()) thaws it immediately.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

from .defaults import load_options

APPS = load_options()["nap_apps"]   # flatpak id / name -> globs, window classes, tier
DEEP_IDLE = 300           # s without input: every listed app naps, with long freezes
DEEP_SKIP = 3             # deep-idle apps skip this many thaws (~9 s frozen per 0.4 s)
NULL_SINK = "endura_silence"   # streams parked there by the audio gate are not "busy"
NAP_FROZEN = 2.0
NAP_THAWED = 0.4
UNFOCUSED_GRACE = 30      # s after losing focus before napping starts
IDLE_NAP = 60             # s without keyboard/mouse input: even the focused app naps
INPUT_MARK = Path("/run/endura/input")   # mtime = last input (written by endurad)
CG = Path(f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/app.slice")
BUS = "io.github.Endura.Nap"


def on_battery():
    from .hw import on_battery as ob
    return ob()


def app_cgroups(app):
    out = []
    for g in APPS[app]["globs"]:
        out += [c for c in CG.glob(g) if (c / "cgroup.freeze").exists() and c not in out]
    return out


def freeze(app, on):
    for c in app_cgroups(app):
        try:
            (c / "cgroup.freeze").write_text("1" if on else "0")
        except OSError:
            pass


def app_of_pid(pid):
    import fnmatch
    try:
        leaf = Path(f"/proc/{pid}/cgroup").read_text().strip().rsplit("/", 1)[-1]
    except OSError:
        return None
    for app, a in APPS.items():
        if any(fnmatch.fnmatchcase(leaf, g) for g in a["globs"]):
            return app
    return None


_pulse = None


def apps_with_streams():
    """Flatpak app ids that currently play or record audio (one persistent
    pulse connection, no subprocess)."""
    global _pulse
    try:
        import pulsectl
        if _pulse is None:
            _pulse = pulsectl.Pulse("endura-nap")
        clients = {c.index: c.proplist for c in _pulse.client_list()}
        park = {sk.index for sk in _pulse.sink_list() if sk.name == NULL_SINK}
        busy = set()
        for st in _pulse.sink_input_list() + _pulse.source_output_list():
            if getattr(st, "sink", None) in park:
                continue      # silent stream parked by the audio gate
            props = dict(clients.get(st.client) or {})
            props.update(st.proplist)
            aid = props.get("pipewire.access.portal.app_id")
            binary = props.get("application.process.binary", "")
            for app, a in APPS.items():
                if aid == app or (a.get("binary") and binary == a["binary"]):
                    busy.add(app)
        return busy
    except Exception:
        _pulse = None
        return set(APPS)  # unknown -> be safe, nap nothing


class Nap(dbus.service.Object):
    def __init__(self, bus):
        super().__init__(bus, "/io/github/Endura/Nap")
        self.session = dbus.SessionBus()
        self.focused = None
        self.last_focus = {a: time.monotonic() for a in APPS}
        self.frozen = set()
        self.napping = set()
        self.deep = set()       # napping apps in deep-idle mode
        self.skips = {}
        self.busy = set()
        self.phase_frozen = False
        GLib.timeout_add_seconds(10, self.reevaluate)
        from gi.repository import Gio
        self.monitor = Gio.File.new_for_path(str(INPUT_MARK)).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect("changed", self.on_input)
        self.schedule(NAP_THAWED)

    @dbus.service.method(BUS, in_signature="is", out_signature="")
    def Focus(self, pid, cls):
        app = app_of_pid(int(pid)) if pid else None
        if app is None:
            low = str(cls).lower()
            app = next((a for a, d in APPS.items() if any(k in low for k in d["cls"])), None)
        prev, self.focused = self.focused, app
        if prev:
            self.last_focus[prev] = time.monotonic()
        if app:
            self.last_focus[app] = time.monotonic()
            self.wake(app, "focus")

    @dbus.service.method(BUS, in_signature="", out_signature="s")
    def Status(self):
        return json.dumps({"focused": self.focused, "napping": sorted(self.napping),
                           "frozen_now": sorted(self.frozen), "deep": sorted(self.deep), "busy_av": sorted(self.busy),
                           "on_battery": on_battery()})

    def wake(self, app, why):
        if app in self.napping:
            self.napping.discard(app)
            print(f"wake {app} ({why})", flush=True)
        self.deep.discard(app)
        if app in self.frozen:
            freeze(app, False)
            self.frozen.discard(app)

    def locked(self):
        try:
            ss = self.session.get_object("org.freedesktop.ScreenSaver", "/ScreenSaver")
            return bool(ss.GetActive(dbus_interface="org.freedesktop.ScreenSaver"))
        except dbus.DBusException:
            return False

    def idle_seconds(self):
        try:
            return time.time() - INPUT_MARK.stat().st_mtime
        except OSError:
            return 0.0

    def on_input(self, *_):
        """inotify on the input mark: the user is back. Wake the focused app and
        everything that was only napping because of idleness."""
        if self.locked():
            return
        for app in list(self.napping):
            if app == self.focused or APPS[app].get("tier", 1) == 2 or app in self.deep:
                self.wake(app, "input")

    def reevaluate(self):
        now = time.monotonic()
        bat = on_battery()
        self.busy = apps_with_streams() if bat else set()
        locked = self.locked() if bat else False
        idle = self.idle_seconds() if bat else 0.0
        deep_idle = locked or idle > DEEP_IDLE
        for app, a in APPS.items():
            unfocused = app != self.focused and now - self.last_focus[app] > UNFOCUSED_GRACE
            if a.get("tier", 1) == 1:
                cond = unfocused or (app == self.focused and (locked or idle > IDLE_NAP))
            else:
                cond = deep_idle
            want = bat and app not in self.busy and bool(app_cgroups(app)) and cond
            if want and deep_idle:
                self.deep.add(app)
            else:
                self.deep.discard(app)
            if want and app not in self.napping:
                self.napping.add(app)
                print(f"nap {app}", flush=True)
            elif not want and app in self.napping:
                self.wake(app, "conditions")
        return True

    def schedule(self, secs):
        GLib.timeout_add(int(secs * 1000), self.tick)

    def tick(self):
        self.phase_frozen = not self.phase_frozen
        if self.phase_frozen:
            for app in self.napping:
                freeze(app, True)
                self.frozen.add(app)
            self.schedule(NAP_FROZEN)
        else:
            for app in list(self.frozen):
                if app in self.deep and app in self.napping:
                    n = self.skips.get(app, 0)
                    if n < DEEP_SKIP:          # stay frozen through this thaw window
                        self.skips[app] = n + 1
                        continue
                self.skips[app] = 0
                freeze(app, False)
                self.frozen.discard(app)
            self.schedule(NAP_THAWED)
        return False

    def thaw_all(self):
        for app in APPS:
            freeze(app, False)


def main():
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    name = dbus.service.BusName(BUS, bus)  # noqa: F841 (keeps the name)
    nap = Nap(bus)
    # reload the KWin relay so it reports the window that is active right now
    try:
        kw = bus.get_object("org.kde.KWin", "/Scripting")
        kw.unloadScript("endura-focus", dbus_interface="org.kde.kwin.Scripting")
        kw.start(dbus_interface="org.kde.kwin.Scripting")
    except dbus.DBusException as e:
        print("kwin relay reload failed:", e, flush=True)
    loop = GLib.MainLoop()

    def stop(*_):
        nap.thaw_all()
        loop.quit()
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, stop)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, stop)
    try:
        loop.run()
    finally:
        nap.thaw_all()


def thaw_cli():
    for app in APPS:
        freeze(app, False)


if __name__ == "__main__":
    thaw_cli() if sys.argv[1:] == ["--thaw"] else main()
