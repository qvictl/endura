"""Tweak registry. Every tweak is idempotent, reversible and hardware-checked.

A tweak has:
  available()         does this machine have the thing at all
  ensure(on_battery)  bring the system to the tweaked state (may be called often)
  revert()            undo everything ensure() did
  status()            dict(active=bool, pending_reboot=bool, detail=str)
Reboot-scoped tweaks only edit config files; status() compares config with the
running kernel and reports pending_reboot.
"""
import glob
import json
import os
import pwd
import re
import time
from pathlib import Path

from . import hw
from .i18n import T
from .util import ManagedFile, read, run, write, user_cmd, desktop_user, STATE_DIR

CPU = "/sys/devices/system/cpu"


class Tweak:
    id = ""
    title = T("", "")
    desc = T("", "")
    group = "system"
    reboot = False          # needs reboot to take effect
    battery_only = False    # ensure() only on battery, revert() on AC
    default = False
    risk = None             # caveat shown in the GUI
    periodic = True         # re-assert every cycle (cheap sysfs writes only)

    def __init__(self, opts=None):
        self.opts = opts if opts is not None else {}

    def available(self):
        return True

    def ensure(self, on_battery):
        pass

    def revert(self):
        pass

    def status(self):
        return {"active": False, "pending_reboot": False, "detail": ""}

    def fmt(self):
        """Values substituted into title/desc by the GUI."""
        return {}

    def info(self):
        d = {k: getattr(self, k) for k in
             ("id", "title", "desc", "group", "reboot", "battery_only", "default", "risk")}
        d["fmt"] = self.fmt()
        d["available"] = self.available()
        return d


_IMPORT_CACHE = {}


def system_importable(*mods):
    """Can the system python (what the user services run with) import these?"""
    key = mods
    if key not in _IMPORT_CACHE:
        code = "; ".join(f"import {m}" for m in mods)
        env = dict(os.environ, PYTHONPATH="/usr/lib/endura:/usr/lib/endura/vendor")
        import subprocess
        _IMPORT_CACHE[key] = subprocess.run(["/usr/bin/python3", "-c", code], env=env,
                                            capture_output=True).returncode == 0
    return _IMPORT_CACHE[key]


def user_home():
    user, _ = desktop_user()
    if not user:
        return None
    try:
        return Path(pwd.getpwnam(user).pw_dir)
    except KeyError:
        return None


# ---------------------------------------------------------------- CPU ----

def _boost_paths():
    per = glob.glob(f"{CPU}/cpu[0-9]*/cpufreq/boost")
    if per:
        return per, "1", "0"
    if os.path.exists(f"{CPU}/cpufreq/boost"):
        return [f"{CPU}/cpufreq/boost"], "1", "0"
    if os.path.exists(f"{CPU}/intel_pstate/no_turbo"):
        return [f"{CPU}/intel_pstate/no_turbo"], "0", "1"
    return [], "1", "0"


class CpuPolicy(Tweak):
    """power-saver profiles usually set EPP=power, which keeps cores near their
    lowest P-state: UI work takes 3-4x longer and, by not racing to idle,
    often costs *more* energy. Use a responsive EPP with boost off instead."""
    id = "cpu_policy"
    title = T("Responsive CPU policy (battery)", "响应式 CPU 策略（电池）")
    desc = T("On battery: EPP {epp}, turbo/boost {boost}, no max-frequency cap. "
             "Replaces EPP=power, which made a 10 ms UI burst take 34.5 ms instead of 13.3 ms "
             "and still drew ~2 W more on the reference laptop.",
             "电池模式下 EPP 用 {epp}、睿频 {boost}、取消最高频率限制。替代 EPP=power"
             "（参考机上 10ms 的 UI 任务要 34.5ms，且整机反而多耗约 2W）。")
    group = "cpu"
    battery_only = True
    default = True

    def available(self):
        return os.path.exists(f"{CPU}/cpu0/cpufreq/energy_performance_preference")

    @property
    def epp(self):
        return self.opts.get("battery_epp", "balance_performance")

    def fmt(self):
        return {"epp": self.epp, "boost": "on" if self.opts.get("battery_boost") else "off"}

    def ensure(self, on_battery):
        if not on_battery:
            return
        for c in glob.glob(f"{CPU}/cpu[0-9]*/cpufreq"):
            if read(f"{c}/energy_performance_preference") not in (None, self.epp):
                write(f"{c}/energy_performance_preference", self.epp)
            mx = read(f"{c}/cpuinfo_max_freq")
            if mx and read(f"{c}/scaling_max_freq") != mx:
                write(f"{c}/scaling_max_freq", mx)
        paths, on, off = _boost_paths()
        want = on if self.opts.get("battery_boost") else off
        for p in paths:
            if read(p) != want:
                write(p, want)

    def revert(self):
        # power-profiles-daemon / tuned re-apply their own profile on restart
        run(["systemctl", "try-restart", "power-profiles-daemon", "tuned"])

    def status(self):
        e = read(f"{CPU}/cpu0/cpufreq/energy_performance_preference")
        paths, on, _ = _boost_paths()
        b = ("on" if read(paths[0]) == on else "off") if paths else "n/a"
        m = read(f"{CPU}/cpu0/cpufreq/scaling_max_freq")
        return {"active": e == self.epp, "pending_reboot": False,
                "detail": f"EPP={e} boost={b} max={int(m or 0)//1000}MHz"}


class StopUnits(Tweak):
    """Stop+disable a set of system units; remembers what was enabled."""
    units = ()
    periodic = False

    @property
    def store(self):
        return STATE_DIR / f"{self.id}.json"

    def _present(self):
        out = []
        for s in self.units:
            rc, o = run(["systemctl", "show", "-p", "LoadState", "--value", s])
            if o.strip() == "loaded":
                out.append(s)
        return out

    def available(self):
        return bool(self._present())

    def ensure(self, on_battery):
        try:
            orig = json.loads(self.store.read_text())
        except (OSError, ValueError):
            orig = {}
        dirty = False
        for s in self._present():
            rc, en = run(["systemctl", "is-enabled", s])
            active = run(["systemctl", "is-active", "--quiet", s])[0] == 0
            if en.strip() == "enabled" or active:
                orig.setdefault(s, en.strip())
                run(["systemctl", "disable", "--now", s])
                dirty = True
        if dirty:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            self.store.write_text(json.dumps(orig))

    def revert(self):
        try:
            orig = json.loads(self.store.read_text())
        except (OSError, ValueError):
            return
        for s, en in orig.items():
            run(["systemctl", "enable", "--now", s] if en == "enabled" else ["systemctl", "start", s])
        self.store.unlink(missing_ok=True)

    def status(self):
        act = [s for s in self._present() if run(["systemctl", "is-active", "--quiet", s])[0] == 0]
        return {"active": not act, "pending_reboot": False,
                "detail": ("running: " + ", ".join(act)) if act else "stopped"}


class NoFreqCappers(StopUnits):
    id = "no_freq_cappers"
    title = T("Stop frequency-capping daemons", "停用压频守护进程")
    desc = T("Stops daemons that clamp scaling_max_freq to save power (e.g. a custom powercapd, "
             "auto-cpufreq). A 1.2 GHz clamp cost ~1.5 W more and made UI 4x slower on the "
             "reference laptop.",
             "停止通过压低 scaling_max_freq 省电的守护进程（如自制 powercapd、auto-cpufreq）。"
             "参考机上 1.2GHz 锁频反而多耗约 1.5W，UI 慢 4 倍。")
    group = "cpu"
    default = True
    units = ("powercapd.service", "auto-cpufreq.service", "cpupower-gui.service")


# ------------------------------------------------------------ clock -----

TSCSYNC_SET_MODE = "/usr/libexec/endura/tscsync-set-mode"


class TscSync(Tweak):
    """Some AMD laptop BIOSes leave CPU0's TSC far behind the other cores. AMD
    has no TSC_ADJUST, so Linux drops the TSC for HPET and every clock_gettime()
    becomes a slow syscall. tscsync (UEFI tool run from GRUB) aligns them."""
    id = "tsc_sync"
    title = T("Fix BIOS TSC desync (keep TSC clocksource)", "修复 BIOS TSC 不同步（保持 TSC 时钟源）")
    desc = T("Firmware left the CPU timestamp counters out of sync, so the kernel uses the slow HPET "
             "for every time read. Uses tscsync (github.com/Zanasin/tscsync) before Linux boots.",
             "固件让各核 TSC 不同步，内核被迫用慢速 HPET，所有程序取时间都变成系统调用。"
             "通过 tscsync（github.com/Zanasin/tscsync）在启动前校准。")
    group = "firmware"
    reboot = True
    risk = T("Runs from GRUB; its hang guard disables it after one failed boot.",
             "在 GRUB 阶段运行；若一次没能进入系统，下次自动禁用。")
    periodic = False

    def available(self):
        return Path("/etc/tscsync.conf").exists() and os.path.exists(TSCSYNC_SET_MODE)

    def _mode(self):
        try:
            conf = dict(l.split("=", 1) for l in Path("/etc/tscsync.conf").read_text().splitlines()
                        if "=" in l and not l.startswith("#"))
        except OSError:
            return "off"
        if conf.get("LOADER", "grub") == "systemd-boot":
            sd = conf.get("STATE_DIR", "")
            if os.path.exists(f"{sd}/disabled"):
                return "off"
            return (read(f"{sd}/options", "mode=measure") or "").split()[0].split("=", 1)[-1]
        rc, out = run([conf.get("EDITENV", "grub2-editenv"), conf.get("GRUBENV", "/boot/grub2/grubenv"), "list"])
        env = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
        if env.get("tscsync_enable") != "1":
            return "off"
        return env.get("tscsync_mode", "mode=measure").split("=", 1)[-1]

    def ensure(self, on_battery):
        if self._mode() != "sync":
            run([TSCSYNC_SET_MODE, "sync"])

    def revert(self):
        if self._mode() != "off":
            run([TSCSYNC_SET_MODE, "off"])

    def status(self):
        cs = read("/sys/devices/system/clocksource/clocksource0/current_clocksource")
        mode = self._mode()
        return {"active": cs == "tsc", "pending_reboot": (mode == "sync") != (cs == "tsc"),
                "detail": f"clocksource={cs}, tscsync={mode}"}


# ---------------------------------------------------------- display -----

AMDGPU_OVERRIDE = "/etc/modprobe.d/zz-endura-amdgpu.conf"
DC_DISABLE_STUTTER = 0x2
DC_DISABLE_PSR = 0x10


def _running_dcmask():
    return int(read("/sys/module/amdgpu/parameters/dcdebugmask", "0") or "0", 0)


def _configured_dcmask():
    """Effective dcdebugmask after all modprobe.d files (last one wins)."""
    mask = 0
    files = sorted(glob.glob("/usr/lib/modprobe.d/*.conf") + glob.glob("/etc/modprobe.d/*.conf"),
                   key=os.path.basename)
    for f in files:
        for m in re.finditer(r"^\s*options\s+amdgpu\s+.*dcdebugmask=(\S+)", read(f, "") or "", re.M):
            mask = int(m.group(1), 0)
    return mask


def _cmdline_dcmask():
    m = re.search(r"amdgpu\.dcdebugmask=(\S+)", read("/proc/cmdline", "") or "")
    return int(m.group(1), 0) if m else None


def _write_override(mask):
    p = Path(AMDGPU_OVERRIDE)
    p.unlink(missing_ok=True)
    base = _configured_dcmask()          # what the remaining files say
    if mask != base:
        p.write_text("# Managed by endura: overrides earlier modprobe.d files (last one wins).\n"
                     "# 0x2 = disable memory stutter, 0x10 = disable PSR\n"
                     f"options amdgpu dcdebugmask=0x{mask:x}\n")
        run(["restorecon", AMDGPU_OVERRIDE])
    # amdgpu usually loads from the initramfs, so options must be there too
    kver = run(["uname", "-r"])[1].strip()
    if os.path.exists("/usr/bin/dracut"):
        run(["dracut", "-f", "--kver", kver], timeout=900)
    elif os.path.exists("/usr/sbin/update-initramfs"):
        run(["update-initramfs", "-u", "-k", kver], timeout=900)
    elif os.path.exists("/usr/bin/mkinitcpio"):
        run(["mkinitcpio", "-P"], timeout=900)


class _DcBit(Tweak):
    bit = 0
    group = "display"
    reboot = True
    periodic = False

    def available(self):
        if not os.path.exists("/sys/module/amdgpu") or _cmdline_dcmask() is not None:
            return False
        # only offered where something actually disabled the feature
        return bool((_running_dcmask() | _configured_dcmask()) & self.bit) or \
            os.path.exists(AMDGPU_OVERRIDE)

    def ensure(self, on_battery):
        cur = _configured_dcmask()
        if cur & self.bit:
            _write_override(cur & ~self.bit)

    def revert(self):
        p = Path(AMDGPU_OVERRIDE)
        if not p.exists():
            return
        cur = _configured_dcmask()
        p.unlink()
        orig = _configured_dcmask()
        want = (cur & ~self.bit) | (orig & self.bit)
        _write_override(want)

    def status(self):
        run_on = not (_running_dcmask() & self.bit)
        cfg_on = not (_configured_dcmask() & self.bit)
        return {"active": run_on, "pending_reboot": run_on != cfg_on,
                "detail": f"running dcdebugmask=0x{_running_dcmask():x}, "
                          f"configured=0x{_configured_dcmask():x}"}


class DisplayPsr(_DcBit):
    id = "amdgpu_psr"
    bit = DC_DISABLE_PSR
    title = T("Enable Panel Self Refresh (amdgpu)", "启用面板自刷新 PSR（amdgpu）")
    desc = T("amdgpu.dcdebugmask disables PSR here. With PSR the panel refreshes itself while the "
             "image is static, so the display engine and eDP link can power down.",
             "当前 amdgpu.dcdebugmask 禁用了 PSR。启用后画面静止时由面板自己刷新，显示引擎和 eDP 链路可断电。")
    risk = T("It was probably disabled to work around flicker; turn off and reboot if it returns.",
             "原本可能是为规避闪屏而禁用；如出现闪烁/卡帧，关闭此项并重启。")


class DisplayStutter(_DcBit):
    id = "amdgpu_stutter"
    bit = DC_DISABLE_STUTTER
    title = T("Enable display memory stutter (amdgpu)", "启用显示内存 stutter（amdgpu）")
    desc = T("Lets the display engine fetch a burst and let DRAM / data fabric sleep. While disabled, "
             "FCLK stays at its maximum (1875 MHz on the reference laptop; 400 MHz after).",
             "让显示控制器批量取帧后让内存/数据总线休眠。被禁用时 FCLK 常驻最高频（参考机 1875MHz，启用后 400MHz）。")
    risk = DisplayPsr.risk


class PanelPowerSavings(Tweak):
    id = "abm"
    title = T("Adaptive backlight ABM (battery)", "自适应背光 ABM（电池）")
    desc = T("AMD ABM lowers the backlight and boosts pixel values to compensate; level {lvl} on "
             "battery. Slightly changes colour accuracy. Measured gain on the reference laptop was small.",
             "AMD 自适应背光：降低背光并提亮像素补偿，电池下 level {lvl}。会轻微影响色准；参考机实测收益很小。")
    group = "display"
    battery_only = True

    def _path(self):
        g = glob.glob("/sys/class/drm/card*-eDP-*/amdgpu/panel_power_savings")
        return g[0] if g else None

    def available(self):
        return self._path() is not None

    def fmt(self):
        return {"lvl": self.opts.get("abm_level", 1)}

    def ensure(self, on_battery):
        p = self._path()
        want = str(self.opts.get("abm_level", 1)) if on_battery else "0"
        if p and read(p) != want:
            write(p, want)

    def revert(self):
        p = self._path()
        if p:
            write(p, "0")

    def status(self):
        p = self._path()
        v = read(p, "0") if p else "n/a"
        return {"active": v not in ("0", "n/a"), "pending_reboot": False, "detail": f"panel_power_savings={v}"}


class PanelMonitorsOnBattery(Tweak):
    """Plasma system-monitor widgets repaint the panel ~40-55 times a second,
    so the screen is never static and PSR never engages."""
    id = "panel_monitors"
    title = T("Hide Plasma panel system monitors on battery", "用电池时隐藏面板系统监视器小部件")
    desc = T("CPU/memory/network/disk graphs in the panel repaint the screen 40-55 times a second, "
             "so Panel Self Refresh never engages. Hidden on battery, restored in place on AC "
             "(the panel blinks ~2 s when restoring).",
             "面板上的 CPU/内存/网络/磁盘图表让屏幕每秒重绘 40~55 次，面板自刷新（PSR）无法生效。"
             "拔电时自动隐藏，插电时原位恢复（恢复时面板会闪约 2 秒）。")
    group = "display"
    battery_only = True
    default = True

    def _state(self):
        h = user_home()
        return h / ".local/state/endura/panel-widgets.json" if h else None

    def available(self):
        return run(["pgrep", "-x", "plasmashell"])[0] == 0

    def _run(self, action):
        c = user_cmd(["env", "PYTHONPATH=/usr/lib/endura:/usr/lib/endura/vendor", "python3", "-m", "endura.panel", action])
        return run(c, timeout=90) if c else (1, "no desktop user")

    def ensure(self, on_battery):
        st = self._state()
        if st is None:
            return
        if on_battery and not st.exists():
            self._run("hide")
        elif not on_battery and st.exists():
            self._run("show")

    def revert(self):
        st = self._state()
        if st is not None and st.exists():
            self._run("show")

    def status(self):
        st = self._state()
        if st is None:
            return {"active": False, "pending_reboot": False, "detail": "no graphical session"}
        return {"active": st.exists(), "pending_reboot": False,
                "detail": "hidden (restored on AC)" if st.exists() else "shown"}


# ------------------------------------------------------------ devices ---

class PciRuntimePm(Tweak):
    id = "pci_runtime_pm"
    title = T("PCI runtime power management", "PCI 设备运行时电源管理")
    desc = T("Lets idle PCI devices (card reader, unplugged Ethernet, ...) runtime-suspend.",
             "让读卡器、未插网线的有线网卡等空闲 PCI 设备自动挂起。")
    group = "devices"
    battery_only = True
    default = True
    STORE = STATE_DIR / "pci_runtime_pm.json"

    def _devs(self):
        return sorted(glob.glob("/sys/bus/pci/devices/*/power/control"))

    def ensure(self, on_battery):
        if not on_battery:
            return self.revert()
        try:
            orig = json.loads(self.STORE.read_text())
        except (OSError, ValueError):
            orig = {}
        changed = False
        for p in self._devs():
            v = read(p)
            if v != "auto":
                orig.setdefault(p, v)
                write(p, "auto")
                changed = True
        if changed:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            self.STORE.write_text(json.dumps(orig))

    def revert(self):
        try:
            orig = json.loads(self.STORE.read_text())
        except (OSError, ValueError):
            return
        for p, v in orig.items():
            write(p, v)
        self.STORE.unlink(missing_ok=True)

    def status(self):
        devs = self._devs()
        auto = sum(read(p) == "auto" for p in devs)
        return {"active": auto == len(devs), "pending_reboot": False, "detail": f"{auto}/{len(devs)} auto"}


class AspmPolicy(Tweak):
    id = "aspm_powersupersave"
    title = T("PCIe ASPM powersupersave (battery)", "PCIe ASPM powersupersave（电池）")
    desc = T("Enables PCIe L1 substates (L1.1/L1.2) so idle links draw almost nothing.",
             "PCIe 链路启用 L1 子状态（L1.1/L1.2），空闲时链路几乎不耗电。")
    group = "devices"
    battery_only = True
    default = True
    P = "/sys/module/pcie_aspm/parameters/policy"

    def available(self):
        return os.access(self.P, os.W_OK) or os.path.exists(self.P)

    def _cur(self):
        m = re.search(r"\[(\w+)\]", read(self.P, "") or "")
        return m.group(1) if m else None

    def ensure(self, on_battery):
        want = "powersupersave" if on_battery else "default"
        if self._cur() != want:
            write(self.P, want)

    def revert(self):
        if self._cur() != "default":
            write(self.P, "default")

    def status(self):
        c = self._cur()
        return {"active": c == "powersupersave", "pending_reboot": False, "detail": f"policy={c}"}


class FsWriteback(Tweak):
    """An idle desktop still produced ~57 NVMe writes/s: dirty-page writeback
    plus filesystem commits every 30 s. Longer commit intervals and noatime let
    rewrites coalesce so the SSD stays in its milliwatt power states."""
    id = "fs_writeback"
    title = T("Coalesce disk writes (battery)", "合并磁盘写入（电池）")
    desc = T("On battery: btrfs/ext4 commit interval 30 -> 120 s, noatime, dirty-page expiry 30 -> 60 s "
             "(remounted at runtime, fstab untouched). Reference laptop: 3443 -> 1541 NVMe writes/min.",
             "用电池时 btrfs/ext4 提交间隔 30→120 秒、noatime、脏页过期 30→60 秒（运行时重新挂载，不改 fstab）。"
             "参考机 SSD 写入 3443→1541 次/分钟。")
    group = "devices"
    battery_only = True
    default = True
    risk = T("On a crash or sudden power loss, up to ~2 minutes of not-fsynced writes can be lost.",
             "突然断电/死机时，最多丢失约 2 分钟内未 fsync 的写入。")
    EXPIRE = ("6000", "3000")

    def _mounts(self):
        out, seen = [], set()
        for line in open("/proc/self/mounts"):
            dev, mnt, fs, opts = line.split()[:4]
            if fs in ("btrfs", "ext4") and mnt in ("/", "/home") and mnt not in seen:
                seen.add(mnt)
                out.append((mnt, fs, opts.split(",")))
        return out

    def available(self):
        return bool(self._mounts())

    def ensure(self, on_battery):
        if not on_battery:
            return self.revert()
        for mnt, fs, opts in self._mounts():
            if "commit=120" not in opts or "noatime" not in opts:
                run(["mount", "-o", "remount,commit=120,noatime", mnt])
        if read("/proc/sys/vm/dirty_expire_centisecs") != self.EXPIRE[0]:
            write("/proc/sys/vm/dirty_expire_centisecs", self.EXPIRE[0])

    def revert(self):
        for mnt, fs, opts in self._mounts():
            if "commit=120" in opts or "noatime" in opts:
                run(["mount", "-o", "remount,commit=30,relatime", mnt])
        if read("/proc/sys/vm/dirty_expire_centisecs") != self.EXPIRE[1]:
            write("/proc/sys/vm/dirty_expire_centisecs", self.EXPIRE[1])

    def status(self):
        ms = self._mounts()
        ok = bool(ms) and all("commit=120" in o and "noatime" in o for _, _, o in ms)
        return {"active": ok, "pending_reboot": False,
                "detail": "; ".join(f"{m}: " + ",".join(x for x in o if x.startswith(("commit", "noatime", "relatime")))
                                    for m, _, o in ms) + f"; dirty_expire={read('/proc/sys/vm/dirty_expire_centisecs')}"}


class Sysctls(Tweak):
    id = "sysctl_power"
    title = T("Kernel power sysctls", "内核省电参数")
    desc = T("Disables the NMI watchdog; dirty writeback interval 15 s (fewer disk wakeups).",
             "关闭 NMI watchdog；脏页回写间隔 15 秒（减少磁盘唤醒）。")
    default = True
    VALUES = {"kernel/nmi_watchdog": ("0", "1"), "vm/dirty_writeback_centisecs": ("1500", "500")}

    def ensure(self, on_battery):
        for k, (on, _) in self.VALUES.items():
            if os.path.exists(f"/proc/sys/{k}") and read(f"/proc/sys/{k}") != on:
                write(f"/proc/sys/{k}", on)

    def revert(self):
        for k, (_, off) in self.VALUES.items():
            write(f"/proc/sys/{k}", off)

    def status(self):
        ok = all(read(f"/proc/sys/{k}") == on for k, (on, _) in self.VALUES.items())
        return {"active": ok, "pending_reboot": False,
                "detail": ", ".join(f"{k.split('/')[-1]}={read('/proc/sys/' + k)}" for k in self.VALUES)}


class TrimServices(StopUnits):
    id = "trim_services"
    title = T("Trim idle background services", "精简无用后台服务")
    desc = T("Stops services that wake the CPU but rarely matter on a laptop: ModemManager "
             "(no WWAN), ABRT crash collectors, smartd.",
             "停用 ModemManager（无蜂窝模块）、ABRT 崩溃收集、smartd 等常驻服务，减少唤醒。")
    default = True
    units = ("ModemManager.service", "abrtd.service", "abrt-journal-core.service",
             "abrt-oops.service", "abrt-xorg.service", "smartd.service")

    def available(self):
        p = self._present()
        if "ModemManager.service" in p and glob.glob("/sys/class/net/ww*"):
            return False      # there is a modem: leave it alone
        return bool(p)


class TrimRemoteDesktop(StopUnits):
    id = "trim_remote_desktop"
    title = T("Stop extra remote-desktop daemons", "停用多余的远程桌面守护进程")
    desc = T("Stops ToDesk / TeamViewer / AnyDesk daemons (each keeps a heartbeat connection). "
             "Leave the one you actually use running.",
             "停用 ToDesk / TeamViewer / AnyDesk 守护进程（各自保持心跳连接），只保留你真正使用的那个。")
    group = "apps"
    units = ("todeskd.service", "teamviewerd.service", "anydesk.service")
    risk = T("You cannot connect in through the stopped tools.", "停用后无法通过这些工具远程连入。")


class ForkingTypeFix(Tweak):
    """Some vendor units say Type=forking while the daemon stays in the
    foreground: every start times out and systemd restarts it forever.
    Seen: sunloginclient.service (113 restarts, ~6 s CPU each)."""
    id = "forking_unit_fix"
    title = T("Fix vendor units stuck in a start-timeout loop", "修复卡在启动超时循环的厂商服务")
    desc = T("Detects units declared Type=forking whose daemon never forks (each start times out and "
             "is restarted forever) and overrides them to Type=simple. Known: {units}.",
             "检测声明为 Type=forking 但守护进程实际不 fork 的服务（每次启动超时被杀、无限重启），"
             "覆盖为 Type=simple。已知：{units}。")
    group = "apps"
    default = True
    periodic = False
    KNOWN = ("sunloginclient.service",)

    def _units(self):
        out = []
        for u in self.KNOWN:
            rc, o = run(["systemctl", "show", "-p", "LoadState,Type", u])
            d = dict(l.split("=", 1) for l in o.splitlines() if "=" in l)
            if d.get("LoadState") == "loaded":
                out.append(u)
        return out

    def _drop(self, u):
        return ManagedFile(f"/etc/systemd/system/{u}.d/50-endura-type.conf")

    def fmt(self):
        return {"units": ", ".join(self.KNOWN)}

    def available(self):
        return bool(self._units())

    def ensure(self, on_battery):
        changed = []
        for u in self._units():
            if self._drop(u).write("[Service]\nType=simple\n"):
                changed.append(u)
        if changed:
            run(["systemctl", "daemon-reload"])
            for u in changed:
                run(["systemctl", "try-restart", u])

    def revert(self):
        changed = [u for u in self.KNOWN if self._drop(u).restore()]
        if changed:
            run(["systemctl", "daemon-reload"])
            for u in changed:
                run(["systemctl", "try-restart", u])

    def status(self):
        parts = []
        for u in self._units():
            rc, o = run(["systemctl", "show", "-p", "Type,NRestarts", u])
            d = dict(l.split("=", 1) for l in o.splitlines() if "=" in l)
            parts.append(f"{u}: Type={d.get('Type')} NRestarts={d.get('NRestarts')}")
        return {"active": all("Type=simple" in p for p in parts), "pending_reboot": False,
                "detail": "; ".join(parts)}


class BackgroundTimerSlack(Tweak):
    """Timer slack lets the kernel defer a thread's timer expiry so it coalesces
    with other wakeups; fd/event wakeups stay instant. Remote-desktop daemons
    that poll every 10 ms get a deep slack, dropped within ~10 s when they start
    using CPU (an incoming session). Reference laptop: sunloginclient 586 -> 16
    wakeups/s."""
    id = "timer_slack"
    title = T("Coalesce timers of background daemons (adaptive slack)",
              "后台进程定时器合并（自适应 timer slack）")
    desc = T("Background daemons that poll on short timers get timer slack ({deep_ms} ms for "
             "remote-desktop daemons, {light_ms} ms for others); network events still wake them at "
             "once, and remote-desktop daemons return to normal within ~10 s when a session starts.",
             "后台守护线程空转轮询时，允许其定时器延后合并触发（远控类 {deep_ms}ms，其它 {light_ms}ms），"
             "网络事件仍即时唤醒；远控会话开始后约 10 秒内恢复正常。")
    group = "apps"
    default = True
    DEFAULT_NS = 50_000
    HOLD = 300
    BUSY_PCT = 15.0
    BUSY_WINDOWS = 2

    def __init__(self, opts=None):
        super().__init__(opts)
        self.cpu, self.busy_until, self.hot, self.cache = {}, {}, {}, []
        self.on_battery = True

    @property
    def deep(self):
        return set(self.opts.get("slack_deep", []))

    @property
    def light(self):
        return set(self.opts.get("slack_light", []))

    def fmt(self):
        return {"deep_ms": int(self.opts.get("slack_deep_ns", 500_000_000) / 1e6),
                "light_ms": int(self.opts.get("slack_light_ns", 50_000_000) / 1e6)}

    def _scan(self):
        out = []
        for pid in os.listdir("/proc"):
            if pid.isdigit():
                c = read(f"/proc/{pid}/comm")
                if c in self.deep or c in self.light:
                    out.append((pid, c in self.deep))
        self.cache = out

    def _procs(self):
        return [(p, d) for p, d in self.cache if os.path.exists(f"/proc/{p}")]

    def _ticks(self, pid):
        try:
            f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
            return int(f[11]) + int(f[12])
        except (OSError, IndexError, ValueError):
            return None

    def _want(self, pid, deep, now):
        if not self.on_battery:
            return self.DEFAULT_NS
        if not deep:
            return int(self.opts.get("slack_light_ns", 50_000_000))
        t, prev = self._ticks(pid), self.cpu.get(pid)
        self.cpu[pid] = (t, now)
        if t is not None and prev and prev[0] is not None and now > prev[1]:
            pct = (t - prev[0]) / os.sysconf("SC_CLK_TCK") / (now - prev[1]) * 100
            self.hot[pid] = self.hot.get(pid, 0) + 1 if pct > self.BUSY_PCT else 0
            if self.hot[pid] >= self.BUSY_WINDOWS:
                self.busy_until[pid] = now + self.HOLD
        if self.busy_until.get(pid, 0) > now:
            return self.DEFAULT_NS
        return int(self.opts.get("slack_deep_ns", 500_000_000))

    def _apply(self):
        now, seen = time.monotonic(), set()
        for pid, deep in self._procs():
            seen.add(pid)
            ns = self._want(pid, deep, now)
            try:
                tids = os.listdir(f"/proc/{pid}/task")
            except OSError:
                continue
            for tid in tids:
                p = f"/proc/{tid}/timerslack_ns"
                if read(p) != str(ns):
                    write(p, ns)
        for d in (self.cpu, self.busy_until, self.hot):
            for k in list(d):
                if k not in seen:
                    d.pop(k)

    def tick(self):
        """Called every loop (5 s) by the daemon."""
        self._apply()

    def ensure(self, on_battery):
        self.on_battery = on_battery
        self._scan()
        self._apply()

    def revert(self):
        self.on_battery = False
        self._scan()
        self._apply()

    def status(self):
        n = deep = busy = 0
        now = time.monotonic()
        for pid, d in self._procs():
            n += 1
            if d:
                deep += 1
                busy += self.busy_until.get(pid, 0) > now
        return {"active": n > 0, "pending_reboot": False,
                "detail": f"{n} processes ({deep} remote-desktop, {busy} in session)"}


class BusyLoopWatchdog(Tweak):
    """Some apps fall into a state where they wake thousands of times a second
    (seen: Feishu's backend, ~3000 wakeups/s, ~4 W). Notify, never kill."""
    id = "busy_watchdog"
    title = T("Busy-loop watchdog", "忙等进程监控")
    desc = T("Notifies when an app keeps waking the CPU more than {thr} times a second for 3 minutes "
             "(seen: a Feishu backend stuck at ~3000/s cost ~4 W; restarting it fixed it).",
             "某个应用持续 3 分钟每秒唤醒超过 {thr} 次时弹出通知（曾见飞书后台卡在约 3000 次/秒，多耗约 4W，重启即恢复）。")
    group = "apps"
    default = True
    THRESHOLD = 1500

    def __init__(self, opts=None):
        super().__init__(opts)
        self.recorder = None
        self.hot_since, self.notified = {}, set()
        self.alerts = {}

    def fmt(self):
        return {"thr": self.THRESHOLD}

    def ensure(self, on_battery):
        rates = getattr(self.recorder, "wake_rate", {}) or {}
        now = time.monotonic()
        ignore = {"kernel", "", "init", "endurad"}
        self.alerts = {k: v for k, v in rates.items() if v > self.THRESHOLD and k not in ignore}
        for k in list(self.hot_since):
            if k not in self.alerts:
                self.hot_since.pop(k)
                self.notified.discard(k)
        for k, v in self.alerts.items():
            self.hot_since.setdefault(k, now)
            if now - self.hot_since[k] > 180 and k not in self.notified:
                c = user_cmd(["notify-send", "-a", "Endura", "-i", "battery-caution",
                              f"{k}: {v:.0f} wakeups/s",
                              "This app keeps the CPU awake; restarting it usually helps. / "
                              "该应用持续唤醒 CPU，重启它通常可以恢复。"])
                c and run(c)
                self.notified.add(k)

    def status(self):
        if not self.alerts:
            return {"active": True, "pending_reboot": False, "detail": "ok"}
        return {"active": True, "pending_reboot": False, "alert": True,
                "detail": "; ".join(f"{k}: {v:.0f}/s" for k, v in self.alerts.items())}


class UserService(Tweak):
    """Enable a systemd --user unit in the desktop user's session."""
    unit = ""
    periodic = False

    def _sc(self, *a):
        c = user_cmd(["systemctl", "--user", *a])
        return run(c) if c else (1, "no desktop user")

    def ensure(self, on_battery):
        if self._sc("is-active", "--quiet", self.unit)[0] != 0:
            self._sc("enable", "--now", self.unit)

    def revert(self):
        self._sc("disable", "--now", self.unit)

    def status(self):
        rc, _ = self._sc("is-active", "--quiet", self.unit)
        return {"active": rc == 0, "pending_reboot": False,
                "detail": f"{self.unit} " + ("running" if rc == 0 else "stopped")}


class AudioGate(UserService):
    id = "audio_gate"
    unit = "endura-audio-gate.service"
    title = T("Silent audio stream gate", "静音音频流闸门")
    desc = T("When an app (e.g. a browser tab) keeps playing pure digital silence, its stream is parked "
             "on a null sink so the codec and speaker amplifier can power down; it moves back within "
             "~0.2 s of real sound. Saved ~1.2 W on the reference laptop.",
             "应用（如某个浏览器标签页）持续输出纯静音时，把流挪到虚拟声卡，让声卡和扬声器功放断电；"
             "出现真实声音后约 0.2 秒切回。参考机实测省约 1.2W。")
    group = "audio"
    default = True
    risk = T("After long silence the first ~0.2 s of sound may be lost.", "长时间静音后恢复播放时，开头约 0.2 秒可能被吞。")

    def available(self):
        return run(["sh", "-c", "command -v parec"])[0] == 0 and system_importable("pulsectl")


class AppNap(UserService):
    id = "app_nap"
    unit = "endura-nap.service"
    title = T("App Nap for background / idle apps (battery)", "App Nap：后台/空闲应用间歇运行（电池）")
    desc = T("Chat apps unfocused for 30 s (or focused but no input for 60 s) run in a 2 s frozen / 0.4 s "
             "running cycle; after 5 min without input, all listed apps (incl. browsers) sleep ~9 s per "
             "0.4 s. Any focus change or keyboard/mouse input wakes them within ~40 ms; apps playing or "
             "recording audio are never napped. Reference laptop, static screen: 5.4 W -> 4.4 W.",
             "聊天应用失焦 30 秒（前台则 60 秒无输入）后按 2 秒冻结/0.4 秒运行节流；5 分钟无输入后，"
             "列表中所有应用（含浏览器）约 9 秒冻结/0.4 秒运行。切换窗口或任何键鼠输入约 40ms 内恢复；"
             "有音视频的应用不节流。参考机静置整机 5.4W→4.4W。")
    group = "apps"
    default = True
    risk = T("Background messages can arrive ~2 s late (~9 s after 5 min away); downloads slow down "
             "while you are away.", "后台消息最多晚约 2 秒（离开 5 分钟后约 9 秒）；离开期间后台下载变慢。")

    def available(self):
        return os.path.exists("/sys/fs/cgroup/cgroup.controllers") and system_importable("dbus", "gi")

    def ensure(self, on_battery):
        if run(["pgrep", "-x", "kwin_wayland"])[0] == 0:
            c = user_cmd(["sh", "-c", "kwriteconfig6 --file kwinrc --group Plugins --key endura-focusEnabled true && "
                                      "dbus-send --session --type=method_call --dest=org.kde.KWin /KWin org.kde.KWin.reconfigure"])
            c and run(c)
        super().ensure(on_battery)

    def revert(self):
        super().revert()
        c = user_cmd(["sh", "-c", "kwriteconfig6 --file kwinrc --group Plugins --key endura-focusEnabled false && "
                                  "dbus-send --session --type=method_call --dest=org.kde.KWin /KWin org.kde.KWin.reconfigure"])
        c and run(c)


class CrashLoopGuard(Tweak):
    id = "crashloop_guard"
    title = T("Crash-loop guard", "崩溃循环守卫")
    desc = T("Stops any system or user service that restarted more than 20 times in 10 minutes and "
             "notifies you (each restart spawns a process and writes the journal). Turning this off "
             "starts them again.",
             "10 分钟内重启超过 20 次的服务（系统或用户级）会被停止并通知（每次重启都要拉起进程、写日志）。关闭此项会重新启动它们。")
    default = True
    LIMIT, WINDOW = 20, 600

    def __init__(self, opts=None):
        super().__init__(opts)
        self.hist, self.stopped, self.last = {}, {}, 0

    def _sc(self, user, *args):
        cmd = ["systemctl", *(["--user"] if user else []), *args]
        if user:
            cmd = user_cmd(cmd)
            if not cmd:
                return ""
        return run(cmd)[1]

    def _show(self, user):
        units = {l.split()[0] for l in self._sc(user, "list-units", "--all", "--type=service",
                                                  "--state=auto-restart", "--no-legend", "--plain").splitlines()
                 if l.strip()}
        units |= {u for (sc, u) in self.hist if (sc == "user") == user}
        if not units:
            return {}
        res, cur = {}, {}
        for line in self._sc(user, "show", "-p", "Id,NRestarts", *sorted(units)).splitlines() + [""]:
            if not line.strip():
                if cur.get("Id"):
                    res[cur["Id"]] = int(cur.get("NRestarts") or 0)
                cur = {}
                continue
            k, _, v = line.partition("=")
            cur[k] = v
        return res

    def ensure(self, on_battery):
        now = time.monotonic()
        if now - self.last < 60:
            return
        self.last = now
        for scope in ("system", "user"):
            for unit, n in self._show(scope == "user").items():
                key = (scope, unit)
                h = [x for x in self.hist.get(key, []) if now - x[0] <= self.WINDOW] + [(now, n)]
                if len(h) > 1 and h[-1][1] == h[0][1] and key not in self.stopped:
                    self.hist.pop(key, None)
                    continue
                self.hist[key] = h
                if key in self.stopped or h[-1][1] - h[0][1] < self.LIMIT:
                    continue
                self._sc(scope == "user", "stop", unit)
                self.stopped[key] = f"{h[-1][1] - h[0][1]} restarts / {int(now - h[0][0])} s"
                c = user_cmd(["notify-send", "-a", "Endura", "-i", "dialog-warning",
                              f"Stopped crash-looping service {unit}", self.stopped[key]])
                c and run(c)

    def revert(self):
        for (scope, unit) in list(self.stopped):
            self._sc(scope == "user", "start", unit)
        self.stopped.clear()

    def status(self):
        if not self.stopped:
            return {"active": True, "pending_reboot": False, "detail": "no crash loops"}
        return {"active": True, "pending_reboot": False, "alert": True,
                "detail": "stopped: " + "; ".join(f"{u} ({r})" for (_, u), r in self.stopped.items())}


class BrowserEnergySaver(Tweak):
    id = "browser_energy_saver"
    title = T("Chrome/Chromium energy saver on battery", "Chrome/Chromium 拔电时省电模式")
    desc = T("Enterprise policy BatterySaverModeAvailability=2: the browser's Energy Saver turns on "
             "whenever unplugged (throttles background tabs, lower frame rate).",
             "通过企业策略 BatterySaverModeAvailability=2：拔电时自动启用浏览器省电模式（限制后台标签、降低帧率）。")
    group = "apps"
    periodic = False
    DIRS = ("/etc/opt/chrome/policies/managed", "/etc/chromium/policies/managed",
            "/etc/opt/edge/policies/managed")
    BINS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge")

    def _files(self):
        return [ManagedFile(f"{d}/endura.json") for d in self.DIRS]

    def available(self):
        return any(run(["sh", "-c", f"command -v {b}"])[0] == 0 for b in self.BINS)

    def ensure(self, on_battery):
        for f in self._files():
            if f.path.parent.parent.parent.exists() or f.path.parent.exists():
                f.write(json.dumps({"BatterySaverModeAvailability": 2}, indent=1) + "\n")

    def revert(self):
        for f in self._files():
            f.restore()

    def status(self):
        on = [str(f.path) for f in self._files() if f.path.exists()]
        return {"active": bool(on), "pending_reboot": False,
                "detail": "restart the browser to apply" if on else ""}


def build(opts):
    ts = [NoFreqCappers(opts), CpuPolicy(opts), TscSync(opts), DisplayPsr(opts), DisplayStutter(opts),
          PanelMonitorsOnBattery(opts), PanelPowerSavings(opts), AudioGate(opts), PciRuntimePm(opts),
          AspmPolicy(opts), FsWriteback(opts), Sysctls(opts), TrimServices(opts),
          TrimRemoteDesktop(opts), ForkingTypeFix(opts), BackgroundTimerSlack(opts),
          BusyLoopWatchdog(opts), AppNap(opts), CrashLoopGuard(opts), BrowserEnergySaver(opts)]
    return {t.id: t for t in ts}
