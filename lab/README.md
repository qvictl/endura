# Measurement lab

Scripts used to produce every number in the main README. Run them on battery,
with the screen on and nobody touching the machine (use `kde-inhibit --power
--screenSaver sleep 1800 &` or your desktop's equivalent so it does not dim).

| Script | What it does |
|---|---|
| `ab.py --a CMD --b CMD` | alternates two system states, measures battery W (and AMD SoC W) for each, prints per-round deltas |
| `meas.py SECONDS` | mean / median / p10 / p90 battery power |
| `spikes.py` | per-second power with the busiest processes in seconds above 6 W |
| `vblank.sh` | display flips per second and PSR state (amdgpu) |
| `whoflips.sh` | freezes each app cgroup in turn to find what keeps the screen repainting |
| `wakeups.sh` | context switches, timer interrupts, IPIs, top wakers (ftrace) |
| `diskwrites.sh` | NVMe writes/min and top writing processes |
| `slack.sh PID NS` | wakeups/s of a process before and after setting timer slack |
