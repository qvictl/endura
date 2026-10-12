"""Publish "last user input" time for endura-nap without polling.

Watches /dev/input/event* (keyboards, mice, touchpads). On activity it touches
/run/endura/input (mtime = last input) at most once a second, then ignores the
devices for a second so continuous mouse motion costs ~1 wakeup/s.
"""
import glob
import os
import select
import threading
import time
from pathlib import Path

MARK = Path("/run/endura/input")


class InputWatcher(threading.Thread):
    daemon = True

    def __init__(self):
        super().__init__(name="inputwatch")
        self.fds = {}

    def _rescan(self):
        paths = set(glob.glob("/dev/input/event*"))
        for p in list(self.fds):
            if p not in paths:
                os.close(self.fds.pop(p))
        for p in paths - set(self.fds):
            try:
                self.fds[p] = os.open(p, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                pass

    def _drain(self, fds):
        for fd in fds:
            try:
                while os.read(fd, 4096):
                    pass
            except (BlockingIOError, OSError):
                pass

    def touch(self):
        MARK.parent.mkdir(parents=True, exist_ok=True)
        MARK.touch()
        os.chmod(MARK, 0o644)

    def run(self):
        self.touch()
        last_scan = 0
        while True:
            if time.monotonic() - last_scan > 30:
                self._rescan()
                last_scan = time.monotonic()
            fds = list(self.fds.values())
            if not fds:
                time.sleep(5)
                continue
            r, _, _ = select.select(fds, [], [], 30)
            if r:
                self._drain(r)
                self.touch()
                time.sleep(1.0)               # rate limit during continuous input
                self._drain(list(self.fds.values()))
