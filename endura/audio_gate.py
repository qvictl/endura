"""endura-audio-gate: park digitally-silent playback on a null sink.

Some apps keep an output stream open that plays pure silence for hours
(measured: a Chrome tab). That keeps the codec, the speaker amplifier and the
audio coprocessor powered: ~1.2 W on the reference laptop. When everything a hardware
sink plays has been silent for SILENT_SECS, its streams are moved to a null
sink (the hardware sink then suspends) and moved back as soon as any of them
makes a sound.

Level detection uses one long-lived `parec` on a sink monitor at a very low
sample rate, so the gate itself causes almost no wakeups. Brand-new streams
that land on the null sink (stream-restore remembering it) are moved to the
default sink at once.
"""
import array
import os
import select
import signal
import subprocess
import sys
import time

import pulsectl

NULL = "endura_silence"
SILENT_SECS = 15
LEVEL = 4               # |s16| above this = sound (~ -78 dBFS)
RATE = 1000             # monitor sample rate; enough to see any signal energy


class Monitor:
    """parec on <sink>.monitor; .loud() tells whether sound arrived since last call."""

    def __init__(self, sink_name):
        self.sink = sink_name
        self.p = subprocess.Popen(
            ["parec", f"--device={sink_name}.monitor", "--format=s16le", f"--rate={RATE}",
             "--channels=1", "--latency-msec=200", "--raw", "--client-name=endura-audio-gate",
             "--stream-name=endura-level"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        os.set_blocking(self.p.stdout.fileno(), False)
        self.started = time.monotonic()

    def loud(self, timeout):
        r, _, _ = select.select([self.p.stdout], [], [], timeout)
        if not r:
            return False
        data = self.p.stdout.read(65536) or b""
        a = array.array("h", data[: len(data) // 2 * 2])
        return any(abs(x) > LEVEL for x in a)

    def alive(self):
        return self.p.poll() is None

    def close(self):
        self.p.terminate()
        try:
            self.p.wait(2)
        except subprocess.TimeoutExpired:
            self.p.kill()


class Gate:
    def __init__(self):
        self.pa = pulsectl.Pulse("endura-audio-gate")
        self.mod = None
        self.mon = None          # Monitor
        self.parked = {}         # sink-input index -> original sink name
        self.last_loud = time.monotonic()
        self.running = True

    def null_sink(self):
        for s in self.pa.sink_list():
            if s.name == NULL:
                return s
        self.mod = self.pa.module_load(
            "module-null-sink",
            f"sink_name={NULL} sink_properties='device.description=Endura-silence-park "
            "priority.session=0 node.force-quantum=8192'")
        return next(s for s in self.pa.sink_list() if s.name == NULL)

    def default_hw_sink(self):
        name = self.pa.server_info().default_sink_name
        for s in self.pa.sink_list():
            if s.name == name and name != NULL:
                return s
        hw = [s for s in self.pa.sink_list() if s.name != NULL]
        return hw[0] if hw else None

    def mic_active(self):
        return any(so.proplist.get("application.name") != "endura-audio-gate"
                   for so in self.pa.source_output_list()
                   if not so.proplist.get("stream.monitor") and ".monitor" not in str(so.proplist.get("target.object", "")))

    def streams(self):
        return [si for si in self.pa.sink_input_list()
                if si.proplist.get("application.name") != "endura-audio-gate"]

    def watch(self, sink_name):
        if self.mon and self.mon.sink == sink_name and self.mon.alive():
            return
        if self.mon:
            self.mon.close()
        self.mon = Monitor(sink_name) if sink_name else None

    def log(self, msg):
        print(msg, flush=True)

    # ------------------------------------------------------------ logic --
    def park_all(self, sink):
        null = self.null_sink()
        self.watch(None)  # a monitor capture would keep the hardware sink awake
        for si in self.streams():
            if si.sink == sink.index:
                self.parked[si.index] = sink.name
                self.pa.sink_input_move(si.index, null.index)
                self.log(f"park #{si.index} {si.proplist.get('application.name')} from {sink.name}")
        self.watch(NULL)

    def unpark_all(self, why):
        self.watch(None)
        null = self.null_sink()
        for si in self.streams():
            if si.sink == null.index:
                target = self.parked.get(si.index)
                sink = None
                if target:
                    sink = next((s for s in self.pa.sink_list() if s.name == target), None)
                sink = sink or self.default_hw_sink()
                if sink:
                    self.pa.sink_input_move(si.index, sink.index)
                    self.log(f"unpark #{si.index} {si.proplist.get('application.name')} -> {sink.name} ({why})")
        self.parked.clear()
        self.last_loud = time.monotonic()

    def step(self):
        # During a call (something records) nothing would be parked anyway: drop
        # the level monitor and poll rarely, so the gate costs nothing in meetings.
        if self.mic_active() and not self.parked:
            self.watch(None)
            self.last_loud = time.monotonic()
            time.sleep(5)
            return
        null = self.null_sink()
        streams = self.streams()
        alive = {si.index for si in streams}
        self.parked = {k: v for k, v in self.parked.items() if k in alive}
        # streams that arrived on the park sink by themselves go home immediately
        for si in streams:
            if si.sink == null.index and si.index not in self.parked:
                hw = self.default_hw_sink()
                if hw:
                    self.pa.sink_input_move(si.index, hw.index)
                    self.log(f"rescue #{si.index} {si.proplist.get('application.name')} -> {hw.name}")
        streams = self.streams()
        on_hw = [si for si in streams if si.sink != null.index]
        on_null = [si for si in streams if si.sink == null.index]

        if on_null:
            if self.mon is None or self.mon.sink != NULL:
                self.watch(NULL)
            if self.mon.loud(0.25):
                self.unpark_all("sound")
                return
        if on_hw:
            # watch the hardware sink the (first) active stream plays to
            sink = next((s for s in self.pa.sink_list() if s.index == on_hw[0].sink), None)
            if not sink:
                return
            if not on_null:
                self.watch(sink.name)
                if self.mon.loud(0.5):
                    self.last_loud = time.monotonic()
            if self.mic_active():
                self.last_loud = time.monotonic()
            if all(si.corked for si in on_hw):
                return
            if time.monotonic() - self.last_loud >= SILENT_SECS and \
                    time.monotonic() - (self.mon.started if self.mon else 0) >= 2:
                self.park_all(sink)
        elif not on_null:
            self.watch(None)
            self.last_loud = time.monotonic()
            time.sleep(1.0)

    def restore(self):
        try:
            self.unpark_all("shutdown")
            for m in self.pa.module_list():
                if m.name == "module-null-sink" and NULL in (m.argument or ""):
                    self.pa.module_unload(m.index)
        except pulsectl.PulseError:
            pass
        self.watch(None)

    def run(self):
        signal.signal(signal.SIGTERM, lambda *a: setattr(self, "running", False))
        signal.signal(signal.SIGINT, lambda *a: setattr(self, "running", False))
        try:
            while self.running:
                self.step()
        finally:
            self.restore()


def main():
    while True:
        try:
            Gate().run()
            return
        except (pulsectl.PulseDisconnected, pulsectl.PulseError) as e:
            print("pulse:", e, file=sys.stderr, flush=True)
            time.sleep(3)
        except Exception as e:
            print("gate error:", repr(e), file=sys.stderr, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
