"""Hardware discovery: no machine-specific paths anywhere else."""
import glob
import os
from functools import lru_cache

from .util import read

PS = "/sys/class/power_supply"


@lru_cache(maxsize=1)
def battery_dir():
    for d in sorted(glob.glob(f"{PS}/*")):
        if read(f"{d}/type") == "Battery" and read(f"{d}/scope", "System") != "Device":
            return d
    return None


@lru_cache(maxsize=1)
def ac_dirs():
    return [d for d in sorted(glob.glob(f"{PS}/*")) if read(f"{d}/type") == "Mains"]


def battery_power_w():
    """Instantaneous discharge (or charge) power in W. One sysfs read where
    possible: every read is an ACPI/EC transaction on many laptops."""
    b = battery_dir()
    if not b:
        return None
    v = read(f"{b}/power_now")
    if v:
        return abs(int(v)) / 1e6
    i, u = read(f"{b}/current_now"), read(f"{b}/voltage_now")
    if i and u:
        return abs(int(i)) * int(u) / 1e12
    return None


def battery_info():
    b = battery_dir()
    if not b:
        return {"w": None, "energy_wh": 0, "full_wh": 0, "status": "Unknown", "pct": 0}

    def wh(name_energy, name_charge):
        e = read(f"{b}/{name_energy}")
        if e:
            return int(e) / 1e6
        c, u = read(f"{b}/{name_charge}"), read(f"{b}/voltage_min_design") or read(f"{b}/voltage_now")
        return int(c) * int(u) / 1e12 if c and u else 0
    return {"w": battery_power_w(), "energy_wh": wh("energy_now", "charge_now"),
            "full_wh": wh("energy_full", "charge_full"),
            "status": read(f"{b}/status", "Unknown"), "pct": int(read(f"{b}/capacity", "0") or 0)}


def on_battery():
    acs = ac_dirs()
    if acs:
        return not any(read(f"{d}/online") == "1" for d in acs)
    b = battery_dir()
    return bool(b) and read(f"{b}/status") == "Discharging"


@lru_cache(maxsize=1)
def gpu_metrics_path():
    """AMD APU SMU telemetry (socket power), if available."""
    for p in sorted(glob.glob("/sys/class/drm/card*/device/gpu_metrics")):
        if read(os.path.join(os.path.dirname(p), "vendor")) == "0x1002":
            return p
    return None


@lru_cache(maxsize=1)
def drm_card():
    p = gpu_metrics_path()
    if p:
        return os.path.dirname(os.path.dirname(p))
    g = sorted(glob.glob("/sys/class/drm/card[0-9]"))
    return g[0] if g else None


def edp_connector():
    g = sorted(glob.glob("/sys/class/drm/card*-eDP-*"))
    return g[0] if g else None


def edp_debugfs():
    """debugfs dir of the internal panel connector (PSR state), amdgpu only."""
    c = edp_connector()
    if not c:
        return None
    conn = "-".join(c.split("/")[-1].split("-")[1:])        # eDP-1
    for d in sorted(glob.glob(f"/sys/kernel/debug/dri/*/{conn}")):
        if os.path.exists(f"{d}/psr_state"):
            return d
    return None


@lru_cache(maxsize=1)
def backlight():
    g = sorted(glob.glob("/sys/class/backlight/*"))
    for d in g:                      # prefer the firmware/raw panel device
        if read(f"{d}/type") in ("raw", "firmware"):
            return d
    return g[0] if g else None


@lru_cache(maxsize=1)
def cpu_vendor():
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("vendor_id"):
                return line.split(":")[1].strip()
    except OSError:
        pass
    return ""


def is_amd():
    return cpu_vendor() == "AuthenticAMD"
