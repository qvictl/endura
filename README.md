# Endura

**Measured, reversible battery-life tuning for Linux laptops — with a GUI.**

[中文说明](README_zh.md)

Endura is a small root daemon, two user agents and a PyQt6 app. It applies a set of
power tweaks that were each **A/B-measured on real hardware**, can switch every one of
them on and off individually, and records per-app CPU time and wakeups so you can see
what is actually draining your battery.

It started as an investigation on one laptop that had been "fixed" by clamping the CPU
to 1.2 GHz and still only lasted ~6 hours. After the work below, the same machine idles
at **4.4 W with the screen on at 30 %** (≈ 22 h on its 99 Wh battery) while staying
*more* responsive than before. See the [case study](#case-study).

![overview](docs/screenshot-overview.png)

## What it does

| Tweak | What it changes | Measured effect (reference laptop) |
|---|---|---|
| **Responsive CPU policy** (battery) | EPP `balance_performance`, boost off, no max-freq cap, instead of the power-saver profile's EPP `power` | **−2.07 W** average (8 A/B rounds) *and* a 10 ms UI burst 34.5 → 13.3 ms |
| **Stop frequency cappers** | stops daemons that clamp `scaling_max_freq` | removing a 1.2 GHz clamp: **−1.5 W**, UI 3× faster |
| **TSC resync** (AMD, via [tscsync](https://github.com/Zanasin/tscsync)) | firmware left CPU0's TSC ~0.8 s behind, kernel fell back to HPET; tscsync aligns TSCs before Linux boots | clocksource back to `tsc`; every `clock_gettime()` is vDSO again instead of a syscall + HPET read |
| **Panel Self Refresh / memory stutter** (amdgpu) | undoes an old `amdgpu.dcdebugmask=0x12` workaround via an override file | idle FCLK 1875 → 400 MHz; PSR residency 0 → ~90 % on a static screen |
| **Silent audio stream gate** | a stream playing pure digital silence is parked on a null sink so codec + speaker amp suspend; back within ~0.2 s on real sound | **−1.2 W** (a browser tab played silence for hours) |
| **App Nap** (battery) | unfocused chat apps run in a 2 s frozen / 0.4 s running cycle; after 5 min without input, chat apps and browsers sleep ~9 s per 0.4 s; any focus change or keyboard/mouse input wakes them in ~40 ms; apps with audio are never napped | Feishu in background **−0.40 W**; static screen **5.1 → 4.4 W** |
| **Hide Plasma panel system monitors** (battery) | the panel's CPU/net/disk graphs repainted the screen ~55×/s so PSR never engaged | static screen: 55 → 0 display flips/s |
| **Adaptive timer slack** | daemons polling on 10 ms timers get 500 ms slack (remote-desktop tools) or 50 ms (others); events still wake them instantly; restored within ~10 s when a remote session starts | Sunlogin 586 → 16 wakeups/s, ToDesk 608 → 104 |
| **Coalesce disk writes** (battery) | btrfs/ext4 `commit=120,noatime`, 60 s dirty expiry (runtime remount, fstab untouched) | NVMe writes 3443 → 1541 per minute |
| **Hardware video decoding in Chromium browsers** | adds the VA-API switches to Chrome / Chromium / Edge / Brave launchers (user desktop-file overrides + `*-flags.conf`), only when VA-API actually initialises on the GPU | YouTube 1080p on battery, 30 % brightness: **17.8 → 10.0 W** (SoC 10.6 → 5.1 W) |
| **Quiet & cool mode on AC** | on AC: firmware low-power profile, responsive EPP with boost off, App Nap / timer slack / device PM kept on, ideapad fan "super silent" where available | light load: peak 55.8 → 42 °C vs. a typical balanced+boost setup; 8 threads × 50 % for 5 min: max 56.8 °C, fan never started; UI bursts *faster* than balanced+boost (10 ms task p90 14–15 ms vs 22–25 ms) |
| **Busy-loop watchdog** | notifies when an app wakes the CPU >1500×/s for 3 min | caught a chat-app backend at ~3000 wakeups/s costing **~4 W** |
| **Crash-loop guard** | stops services restarting >20× in 10 min | caught an updater with a broken npm dependency after 1286 restarts |
| **Vendor unit fix** | units declared `Type=forking` that never fork (start timeout → restart forever) | a remote-desktop daemon had restarted 113× |
| PCI runtime PM, ASPM `powersupersave`, NMI watchdog off, trim idle services, browser energy saver policy | the usual suspects | small, kept because they are free |

Tested and **not** adopted (no measurable gain on the reference laptop): AMD ABM levels 1/3,
120 Hz + VRR vs 60 Hz, SMT off, Bluetooth USB autosuspend, cgroup CPU quotas for chatty
apps (quotas do not stop wakeups — only freezing does).

## Smoothness

Saving power by making the machine sluggish was explicitly not allowed. Endura's
criterion, checked by `endura-bench`:

> From idle, a UI-like task that takes 10 ms at full performance must finish with
> **p90 ≤ 16.7 ms** (one 60 Hz frame); a 2 ms task **p90 ≤ 8.3 ms** (one 120 Hz frame).

| Policy | 2 ms task p50 | 10 ms task p50 | verdict |
|---|---|---|---|
| EPP performance + boost (reference) | 2.3 ms | 10.7 ms | — |
| EPP `balance_performance`, boost off (**Endura**) | 3.0 ms | 13.3 ms | pass |
| EPP `balance_power` | 4.8 ms | 21.3 ms | fail |
| EPP `power` (typical power-saver) | 6.9 ms | 34.5 ms | fail |
| EPP `power` + 1.2 GHz clamp | 8.0 ms | 39.8 ms | fail |

## Telemetry and tools

* **Overview**: live battery and SoC power, 1 h graph, every tweak with status
  (active / battery only / needs reboot / not available on this hardware).
* **Apps**: per-app CPU time *and wakeups per second* over 1 h … 7 days (battery time only),
  plus a **freeze probe** that measures an app's real power by freezing and thawing its
  cgroup a few times. Wakeups often matter more than CPU %: a backend at 3 % CPU but
  3000 wakeups/s cost 4 W.
* **Smoothness**: runs the benchmark above.
* [`lab/`](lab/) has the A/B scripts used for every number on this page.

## Requirements

* systemd, cgroup v2, Python 3.10+, PyQt6, pulsectl, dbus-python, PyGObject, PipeWire (pulse)
* Laptop with a battery exposed in `/sys/class/power_supply`
* Best support: AMD Ryzen APUs (SoC power telemetry, amdgpu PSR, TSC fix) and KDE Plasma 6
  on Wayland (focus tracking for App Nap, panel monitor hiding). Generic tweaks work on Intel
  and other desktops; tweaks whose hardware is missing show as *not available*.

## Tested distributions

`endura-install` resolves dependencies per distribution, installs what is packaged, vendors
the rest into `/usr/lib/endura/vendor` (pulsectl; PyQt6 from PyPI where the distro has none),
and disables a feature instead of failing if something is still missing. Verified in clean
containers (dependency installation and imports for `/usr/bin/python3`):

| Distribution | Python | Result | Notes |
|---|---|---|---|
| Fedora 43 | 3.14 | ✅ | reference machine, full feature set verified on hardware |
| Ubuntu 24.04 | 3.12 | ✅ | |
| Ubuntu 22.04 | 3.10 | ✅ | no `python3-pyqt6` in the archive → PyQt6 wheel vendored |
| Debian 12 | 3.11 | ✅ | |
| Arch Linux | 3.14 | ✅ | `pulsectl` is AUR-only → vendored |
| openSUSE Tumbleweed | 3.13 | ✅ | via zypper |

The GUI connects to the daemon as the desktop session's user or any `wheel`/`sudo`/`admin`
member (checked with `SO_PEERCRED`), so no group setup is needed. KDE-specific parts (App Nap
focus tracking, hiding panel monitors) are skipped elsewhere; on other desktops App Nap naps
apps only when there is no keyboard/mouse input.

## Install

From PyPI:

```sh
pipx install endura                # or: pip install --user endura
sudo "$(which endura-install)"     # installs deps (dnf / apt / pacman), daemon, GUI
endura-gui                         # or "Endura" in the application menu
```

From source:

```sh
git clone https://github.com/qvictl/endura.git
cd endura
sudo ./install.sh
```

The system services run from a self-contained copy in `/usr/lib/endura` with the
distribution's `/usr/bin/python3`, so they do not depend on where pip put the package.

Most tweaks are on by default and apply only on battery. Reboot-scoped ones (TSC resync,
PSR, stutter) are off until you enable them in the GUI. For the TSC fix, install
[tscsync](https://github.com/Zanasin/tscsync) first — Endura only toggles it.

```sh
sudo "$(which endura-uninstall)"   # or ./uninstall.sh: reverts every tweak first, then removes Endura
```

Every file a tweak modifies is backed up in `/var/lib/endura/backup`; module options go to a
separate override (`/etc/modprobe.d/zz-endura-amdgpu.conf`) instead of editing yours.

## Configuration

`/etc/endura/config.json`:

```json
{
  "enabled": {"app_nap": true, "amdgpu_psr": true},
  "options": {
    "battery_epp": "balance_performance",
    "slack_deep": ["sunloginclient", "ToDesk_Service"],
    "nap_apps": {
      "com.slack.Slack": {"globs": ["app-flatpak-com.slack.Slack-*.scope"], "cls": ["slack"], "tier": 1}
    }
  }
}
```

Defaults and all keys: [`endura/defaults.py`](endura/defaults.py). `nap_apps` matches the
app's systemd cgroup (flatpak scopes, KDE/GNOME `app-*.service` units); tier 1 naps when
unfocused, tier 2 only after 5 minutes without input.

## Architecture

| Part | Runs as | Role |
|---|---|---|
| `endura-install` | root, once | copies the package to `/usr/lib/endura`, installs units, KWin script, desktop file |
| `endurad` | root (systemd) | applies tweaks, follows AC/battery, telemetry in `/var/lib/endura/telemetry.db`, JSON API on `/run/endura/endura.sock` (group `wheel`), input-activity mark for App Nap |
| `endura-nap` + KWin script `endura-focus` | user (systemd --user) | App Nap: freezes cgroups (`cgroup.freeze`), wakes on focus (KWin → D-Bus) or input (inotify on `/run/endura/input`) |
| `endura-audio-gate` | user | silence detection with one low-rate `parec` monitor; park sink forced to a large quantum |
| `endura-gui` | user | PyQt6 front end |

The daemon itself was tuned too: a 5 s loop, few ACPI EC reads (each `power_supply` read is
an EC transaction on many laptops), batched SQLite commits, no PAM sessions per user command.
It costs ≈ 0.5 % of one core.

## Case study

Reference machine: **Lenovo ThinkBook 14 G8+ AHP** — AMD Ryzen 7 H 255 (Zen 4, Radeon 780M),
32 GB LPDDR5X, 3072×1920 120 Hz IPS panel, Intel AX210, 99.9 Wh battery, Fedora 43, KDE Plasma 6
Wayland, kernel 7.0. Daily apps that had to stay open: Feishu, Chrome, Sunlogin, Discord,
v2rayN, WeChat. Constraints: Wi-Fi and Bluetooth on, 30 % brightness, no loss of smoothness.

**Starting point.** A previous "fix" ran a feedback loop that lowered `scaling_max_freq` until
battery draw fell under 12 W, plus cgroup CPU quotas on the chat apps. It sat at the 1.2 GHz
floor all day; the machine drew 14–16 W and lasted ≈ 6 h. A 10 ms UI task took 40 ms.

**Investigation, in the order things were found** (whole-system battery power, A/B, screen
on, apps open unless noted):

1. **The clamp was counter-productive.** Removing it lowered draw by ~1.5 W: slow cores stay
   in C0 longer and keep the fabric awake. Power-saver's EPP `power` was the next culprit:
   `balance_performance` with boost off was −2.07 W *and* 2.6× faster.
2. **A browser tab played digital silence** for hours, keeping the codec and speaker amp on:
   −1.2 W when the stream was parked on a null sink. This became the audio gate.
3. **The kernel was on HPET.** dmesg: `Measured 3168609404 cycles TSC warp between CPUs,
   turning off TSC clock`. The BIOS boots CPU0 ~0.8 s behind the other 15 threads; AMD has no
   TSC_ADJUST. Every `clock_gettime()` became a syscall plus a slow MMIO read (one chat
   backend did 3000 per second). Fixed with tscsync; offsets after boot ±16 cycles.
4. **`amdgpu.dcdebugmask=0x12`** (an old flicker workaround) disabled both PSR and memory
   stutter: FCLK was pinned at 1875 MHz. Re-enabled; FCLK idles at 400 MHz, no flicker so far.
5. **One chat app's backend was stuck in a busy-wake loop**: its Rust task scheduler woke 15
   workers every 5 ms (~3000 wakeups/s at 3 % CPU). Freezing just that process dropped the
   whole system from 14.8 W to 10.1 W — **~4 W from one process**. An update + restart fixed it;
   cgroup CPU quotas (what the old setup used) did nothing because they do not stop wakeups.
6. **A remote-desktop daemon was in a start-timeout loop** (`Type=forking`, never forks;
   113 restarts) and, once fixed, still polled every 10 ms on 6 threads. Timer slack took it
   from 586 to 16 wakeups/s without affecting remote sessions.
7. **Freeze probes per app** with a static screen: chat app #1 0.51 W, chat app #2 0.30 W,
   browser 0.08 W, Discord ≈ 0, all four together 1.35 W (more than the sum — the package only
   reaches deep idle when *everything* is quiet). This became App Nap: −0.40 W with the chat
   app in the background.
8. **The screen was never static.** Tracing `drm_vblank_event` showed ~55 flips/s on an idle
   desktop. Freezing cgroups one by one pinned it on the Plasma panel's seven system-monitor
   widgets. Hidden on battery → 0 flips/s, PSR residency ~90 %.
9. **The SSD never slept**: 57 writes/s from writeback and 30 s btrfs commits. `commit=120,noatime`
   on battery halved the write count.
10. **Housekeeping**: an updater crash-looping every 10 s (1286 restarts) → crash-loop guard;
    the daemon's own cost cut from 2.5 % to 0.5 % of a core.

**Static screen, 30 % brightness, all apps open:**

| Stage | System power | Runtime on 99 Wh |
|---|---|---|
| Start (frequency clamp) | 14–16 W | ~6 h |
| CPU policy, audio gate, clamp removed | ~8 W (in use) | ~12 h |
| + TSC, PSR, stutter (reboot) | 6.0–6.3 W | ~16 h |
| + App Nap, timer slack | 5.4 W | ~18 h |
| + panel monitors hidden, write coalescing, deep-idle nap | **4.4 W** | **~22 h** |

Remaining floor (all apps frozen, PSR active, CPU < 2 %): SoC ≈ 2.9 W (display engine, memory
controller, fabric), panel ≈ 1.2 W of which the 30 % backlight is ≈ 0.6 W.

Evaluated and rejected: rebuilding the kernel for `CONFIG_RCU_LAZY` / the TEO idle governor
(Fedora ships neither; RCU woke only ~15×/s here — not worth a custom kernel on every update).

## License

MIT. `third_party/tscsync/set-mode.sh` is from [tscsync](https://github.com/Zanasin/tscsync) (MIT).
