# ARM64 support and validation

Endura detects kernel and driver interfaces rather than promising that every
AMD-specific feature has an ARM equivalent. A missing TSC or amdgpu control is
not fixed by lowering CPU frequency, changing display quality, or editing the
device tree. Existing tweaks with a latency, audio, data-loss, or notification
tradeoff retain their individual switches and warnings.

## Verified machine

Huawei Matebook E Go (GK-W7X), aarch64, Fedora 45, Plasma 6.8.3 Wayland.
The CPU driver is `qcom-cpufreq-hw`, with policies 0 and 4. Both already use
`schedutil`, EAS is enabled, and their maximum frequencies equal the hardware
limits. The GPU devfreq driver already uses its only available governor,
`simple_ondemand`, with an observed idle frequency of 270 MHz. No additional
power reduction is implied by making the CPU policy show as available.

On an EPP-less machine, the CPU policy supports the advertised `schedutil` and
`ondemand` governors. Defaults keep schedutil and the original minimum frequency.
The optional `battery_min_freq_mhz` setting requests a latency floor, bounded by
the existing maximum and never below the original minimum. The maximum is not
changed. Setting the option to zero restores the original minimum. Governor,
minimum and ondemand up-threshold changes are recorded before writes and restored
when the policy is reverted, including across restarts or temporarily missing
policies. Ondemand uses `up_threshold=25`; the previous value is restored before
switching away from that governor. AC quiet mode can explicitly keep the policy
active on AC.

The tested kernel rejects utilization clamps (`uclampset`: Operation not
supported) and reports an ondemand sampling interval of 8000 microseconds and rejected a request for 1000 microseconds.
Untuned schedutil failed the 10 ms burst criterion (p90 about 36 ms). Governor
selection alone is therefore not a promise of responsiveness. Short screening
runs found minimum-frequency profiles that can meet the frame budget; they must
be evaluated with the full benchmark and power measurements. Settings exposes
the tradeoff, and no frequency floor is enabled by default. Ondemand may disable
EAS's energy-aware task placement, so its broader workload behaviour also needs
validation.

Kernel power controls are checked independently: an absent `nmi_watchdog` no
longer hides a successfully configured dirty-page writeback interval. Missing
controls are skipped during both apply and revert.

## Tweak inventory

The x86 column describes implementation prerequisites, not a second-machine
measurement. ARM64 results below are from this machine. All existing tweaks
keep their GUI toggle and revert path.

| Tweak | x86 / generic interface | ARM64 result and alternative |
| --- | --- | --- |
| Stop frequency cappers | Named systemd units | Unavailable because none of the named units is installed; no replacement needed. |
| Responsive CPU policy | EPP, boost, cpufreq, firmware profile | Supports advertised schedutil/ondemand and an optional minimum response frequency without EPP. The original schedutil setting alone failed short-task latency; original governor, minimum and tuning are saved and restored. |
| TSC synchronization | AMD TSC, GRUB, tscsync | Not applicable; the ARM clocksource is `arch_sys_counter`. No x86 boot fix is installed. |
| Panel self refresh | amdgpu dcdebugmask | No amdgpu interface. Qualcomm DPU/panel power states belong to the display driver; there is no equivalent verified writable switch. Refresh rate and display quality are unchanged. |
| Display memory stutter | amdgpu dcdebugmask | No amdgpu interface. Interconnect/devfreq handling is driver-managed; no AMD bits or device-tree changes are applied. |
| Hide panel monitors | Plasma scripting | Available and active; restoring may briefly redraw the panel. |
| Adaptive backlight | AMD panel_power_savings | No matching interface. Dimming or changing colour reproduction is not substituted for this feature. |
| Silent audio gate | PipeWire, pulsectl, parec | Available and active. Existing warning: the first roughly 0.2 seconds after silence may be lost; disable if unacceptable. |
| PCI runtime PM | PCI power/control | Available, all four devices already auto. Other buses require separate driver/wakeup validation. |
| PCIe ASPM | pcie_aspm policy | Available, powersupersave active. Revert uses the existing default-policy path. |
| Coalesced writes | btrfs/ext4, VM sysctls | Available and active. Existing risk: a larger window of non-fsynced data on power loss. |
| Kernel power sysctls | NMI watchdog, VM writeback | Writeback is available and active; missing ARM NMI watchdog is excluded from the status calculation. |
| Trim background services | Named systemd services | Available. Existing WWAN protection is retained; stopped services can be restored. |
| Trim remote desktops | ToDesk/TeamViewer/AnyDesk units | Named units absent; not an instruction-set limitation. RDP, VPN and SSH are not stopped. |
| Fix forking vendor unit | sunloginclient.service | Named unit absent. A differently named remote-control process is not evidence that this fix applies. |
| Timer slack | Per-thread /proc timerslack_ns | Available. Timer expiries may be coalesced; event wakeups are separate. Existing settings and revert remain. |
| Runaway alerts | CPU/wakeup telemetry and notifications | Available. Per-application watts remain estimates, not hardware measurements. |
| App Nap | cgroup v2, input/focus/audio tracking | Available. Existing background-message/download delay warnings remain; input wakeup needs interactive validation. |
| Crash-loop guard | systemd restart counters | Available. It can stop a failing service; disabling restores services it stopped. |
| Browser energy saver | Chromium-family policy and binary | Not available: this machine has Firefox, not a supported Chromium-family binary. Existing frame-rate tradeoffs are not made a new default. |
| Browser hardware video | Chromium-family launcher plus working VA-API | No supported browser and no V4L2 device node found. ARM V4L2/firmware support must be verified before adding an alternative; VA-API flags are not blindly imposed. |
| AC quiet mode | ACPI/platform profile, vendor fan interface | No supported platform-profile or fan interface. No firmware or thermal limits are changed. |

Wi-Fi power save, USB autosuspend, platform runtime PM and GPU devfreq are not
separate tweaks in the existing registry. This machine reports Wi-Fi power save
off and several runtime-enabled USB controllers/PHYs forced on. The remaining
USB device forced on exposes HID interfaces (Huawei 12d1:10b8). Consequently,
blanket USB/PHY autosuspend could affect input or wakeup, and changing Wi-Fi
power mode could affect the remote connection. Those settings are not changed
without latency, suspend/resume and device-wakeup evidence. GPU scaling and EAS
already have dynamic policies and are not forced to lower limits.

## Reproducing checks without deployment

Use an isolated worktree and a virtual environment with the distribution's Qt
and D-Bus modules. Run:

```sh
python -m unittest discover -s tests -v
python -m build --no-isolation --outdir .dev-artifacts/dist
python -m twine check .dev-artifacts/dist/*
gcc -O2 -Wall -Wextra -o .dev-artifacts/burst endura/data/tools/burst.c
```

The regression suite covers heterogeneous EPP-less policies, restoring saved
values across object lifetimes, preserving the maximum, restoring/clearing a requested minimum, a no-op when
already at the default, unsupported governors, AC handling, existing EPP behaviour,
and missing or present kernel controls. Use the produced wheel for the existing
package-resource smoke checks; do not run the system installer merely to test.

A performance-versus-schedutil power experiment is a comparison of two explicit
governor settings. Candidate minimum-frequency profiles are separate configurations. It must not be labelled as savings over this machine's
original configuration, which was already schedutil. Record battery/AC state,
brightness, background work, samples and variability, and restore the original
governors in a finally block. The existing UI-burst criteria are p90 <= 8.3 ms
for a 2 ms reference task and <= 16.7 ms for a 10 ms reference task. A headless
Qt smoke check does not replace interactive display, audio and input checks.

References: [CPUFreq policy/governor interfaces](https://docs.kernel.org/admin-guide/pm/cpufreq.html),
[Energy Aware Scheduling](https://docs.kernel.org/scheduler/sched-energy.html),
and [runtime PM and driver/wakeup responsibilities](https://docs.kernel.org/power/runtime_pm.html).

## Measured 30-sample burst validation

Each task size uses 30 samples. Energy covers the same 90-burst workload, sampled at 1 Hz; background activity and measurement noise limit comparisons. This is not a claim of savings over the original schedutil configuration. All governors and limits were restored.

| Configuration | 2 ms p90 (ms) | 10 ms p90 (ms) | Workload energy (J) | Frame budget |
| --- | ---: | ---: | ---: | --- |
| schedutil_0 | 9.28 | 22.41 | 87.4 | fail |
| schedutil_1800 | 3.2 | 15.94 | 87.9 | pass |
| schedutil_2000 | 2.9 | 14.21 | 89.2 | pass |
| ondemand_1200 | 4.68 | 15.87 | 79.5 | pass |

## Repeated validation and selected device profile

The initial ondemand 1200 MHz candidate did not pass every repeat: one of
three runs reached 16.94 ms p90 for the 10 ms task. It is not the selected
profile. For the tested Matebook E Go / qcom-cpufreq-hw device, select
**schedutil with a requested 2000 MHz minimum** for responsiveness. The driver
rounded the two policies to 2112 and 2073.6 MHz. This retains schedutil/EAS
and the original maximum frequencies. The portable default remains schedutil
with the original minimum (0); other ARM devices need their own measurements.

Both comparisons used baseline/candidate/candidate/baseline/baseline/candidate
order. Every run executed 30 samples of each of the same 2/10/50 ms reference
tasks. Energy is the integral of approximately 1 Hz whole-battery power readings
across the 90-burst workload, including its waits. Brightness stayed at its
existing 820/4095 value. Background desktop/network activity was not isolated.

| Candidate | Repeat | 2 ms p90 (ms) | 10 ms p90 (ms) | Energy (J) | Frame budget |
| --- | ---: | ---: | ---: | ---: | --- |
| ondemand 1200 | 1 | 4.68 | 16.06 | 83.52 | pass |
| ondemand 1200 | 2 | 4.98 | 14.99 | 89.11 | pass |
| ondemand 1200 | 3 | 4.68 | 16.94 | 84.99 | fail |
| schedutil 2000 | 1 | 2.88 | 14.14 | 89.46 | pass |
| schedutil 2000 | 2 | 2.86 | 14.21 | 99.16 | pass |
| schedutil 2000 | 3 | 2.86 | 14.23 | 137.94 | pass |


ondemand 1200 comparison, workload energy mean +/- sample standard deviation: baseline 87.22 +/- 6.03 J; candidate 85.87 +/- 2.90 J.

schedutil 2000 comparison, workload energy mean +/- sample standard deviation: baseline 120.74 +/- 38.50 J; candidate 108.85 +/- 25.65 J.

Power variability is too large to establish an energy or battery-life gain.
The result supports the measured latency improvement; it does not establish
that this frequency floor saves energy, or that all interactive workloads are
unchanged. Keep the floor optional and reversible. Disabling CPU policy or moving to AC restores the recorded governor,
minimum and ondemand controls. Setting only the minimum back to 0 restores
the recorded minimum while keeping the selected governor.
Every experiment restored both governors and frequency limits exactly. The
installed system daemon was not replaced or restarted by the development test.
