"""endura-install / endura-uninstall: system-wide setup from the Python package.

The daemon and user agents run from a self-contained copy in /usr/lib/endura
using the system python3, so they keep working no matter where pip/pipx put
this package. Must run as root:  sudo endura-install
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent
DATA = PKG / "data"
LIB = Path("/usr/lib/endura")
LIBEXEC = Path("/usr/libexec/endura")

VENDOR = LIB / "vendor"            # pure-Python deps the distro does not package
# module -> distro package per package manager (None = not packaged there)
DEPS = {
    "PyQt6":    {"dnf": "python3-pyqt6", "apt-get": "python3-pyqt6", "pacman": "python-pyqt6", "zypper": "python3-PyQt6"},
    "pulsectl": {"dnf": "python3-pulsectl", "apt-get": "python3-pulsectl", "pacman": None, "zypper": None},
    "dbus":     {"dnf": "python3-dbus", "apt-get": "python3-dbus", "pacman": "python-dbus", "zypper": "python3-dbus-python"},
    "gi":       {"dnf": "python3-gobject", "apt-get": "python3-gi", "pacman": "python-gobject", "zypper": "python3-gobject"},
}
TOOLS = {"dnf": ["gcc", "pulseaudio-utils"], "apt-get": ["gcc", "pulseaudio-utils"],
         "pacman": ["gcc", "libpulse"], "zypper": ["gcc", "pulseaudio-utils"]}
INSTALL_CMD = {"dnf": ["dnf", "-y", "install"], "apt-get": ["apt-get", "install", "-y"],
               "pacman": ["pacman", "-S", "--needed", "--noconfirm"], "zypper": ["zypper", "-n", "install"]}
# importable name -> PyPI name, installed into VENDOR when the distro lacks it
# (pulsectl is pure Python; PyQt6 ships manylinux wheels with Qt bundled, e.g.
# for Ubuntu 22.04 which has no python3-pyqt6). dbus-python and PyGObject have
# no wheels and must come from the distribution.
PURE_PY = {"pulsectl": "pulsectl", "PyQt6": "PyQt6"}
# shared libraries the PyPI PyQt6 wheel needs from the system (desktops have them;
# minimal or server installs may not)
QT_RUNTIME = {"dnf": ["mesa-libGL", "mesa-libEGL", "libxkbcommon", "fontconfig"],
              "apt-get": ["libgl1", "libegl1", "libxkbcommon0", "libfontconfig1"],
              "pacman": ["libglvnd", "libxkbcommon", "fontconfig"],
              "zypper": ["libGL1", "libEGL1", "libxkbcommon0", "fontconfig"]}
FEATURES = {"PyQt6": "GUI (endura-gui)", "pulsectl": "audio gate, App Nap audio check",
            "dbus": "App Nap", "gi": "App Nap"}
PYPATH = f"{LIB}:{VENDOR}"
GUI_WRAPPER = f"#!/usr/bin/bash\nPYTHONPATH={PYPATH} exec /usr/bin/python3 -m endura.gui \"$@\"\n"


def sh(*cmd, check=True, **kw):
    print("+", " ".join(map(str, cmd)))
    return subprocess.run(list(map(str, cmd)), check=check, **kw)


def have(cmd):
    return shutil.which(cmd) is not None


def importable(mod):
    env = dict(os.environ, PYTHONPATH=str(VENDOR))
    return subprocess.run(["/usr/bin/python3", "-c", f"import {mod}"], capture_output=True, env=env).returncode == 0


def install_deps():
    """Install what the distro packages, vendor pure-Python leftovers, and
    report (not fail on) anything still missing: the daemon itself only needs
    the standard library."""
    pm = next((p for p in INSTALL_CMD if have(p)), None)
    want = [m for m in DEPS if not importable(m)]
    pkgs = [DEPS[m][pm] for m in want if pm and DEPS[m].get(pm)]
    pkgs += [t for t in TOOLS.get(pm, []) if not (t == "gcc" and have("gcc"))
             and not (t in ("pulseaudio-utils", "libpulse") and have("parec"))]
    if pm and pkgs:
        if pm == "apt-get":
            sh("apt-get", "update", check=False)
        if sh(*INSTALL_CMD[pm], *pkgs, check=False).returncode != 0:
            for p in pkgs:                      # one by one: skip names this release lacks
                sh(*INSTALL_CMD[pm], p, check=False)
    for mod, dist in PURE_PY.items():
        if not importable(mod):
            VENDOR.mkdir(parents=True, exist_ok=True)
            pip = [sys.executable, "-m", "pip"]
            if subprocess.run(pip + ["--version"], capture_output=True).returncode != 0:
                pip = ["/usr/bin/python3", "-m", "pip"]
            sh(*pip, "install", "--quiet", "--upgrade", "--target", VENDOR, dist, check=False)
            if mod == "PyQt6" and pm:
                sh(*INSTALL_CMD[pm], *QT_RUNTIME.get(pm, []), check=False)
    missing = [m for m in DEPS if not importable(m)]
    for m in missing:
        print(f"WARNING: python module '{m}' unavailable for /usr/bin/python3 -> disabled: {FEATURES[m]}")
    if not have("gcc"):
        sys.exit("gcc is required to build the smoothness benchmark; install it and rerun.")
    if not have("parec"):
        print("WARNING: 'parec' not found (pulseaudio-utils / libpulse) -> audio gate unavailable")
    return missing


def put(src, dst, mode=0o644):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    os.chmod(dst, mode)


def calibrate():
    ref = Path("/var/lib/endura/burst_ref")
    if ref.exists() and ref.read_text().strip():
        return
    print("calibrating the smoothness reference (5 s at full performance)...")
    epps = {}
    for f in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/energy_performance_preference"):
        try:
            epps[f] = f.read_text().strip()
            f.write_text("performance")
        except OSError:
            pass
    try:
        out = subprocess.run([LIBEXEC / "burst", "calib"], capture_output=True, text=True).stdout.strip()
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(out + "\n")
    finally:
        for f, v in epps.items():
            try:
                f.write_text(v)
            except OSError:
                pass


def install():
    if os.geteuid() != 0:
        sys.exit("run as root: sudo endura-install  (with pipx: sudo $(which endura-install))")
    # self-contained copy of the package for the system services
    missing = install_deps()
    if (LIB / "endura").exists():
        shutil.rmtree(LIB / "endura")
    shutil.copytree(PKG, LIB / "endura", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for d in ("/var/lib/endura", "/etc/endura"):
        Path(d).mkdir(parents=True, exist_ok=True)
    LIBEXEC.mkdir(parents=True, exist_ok=True)
    sh("gcc", "-O2", "-o", LIBEXEC / "burst", DATA / "tools/burst.c")
    put(DATA / "tools/endura-bench", LIBEXEC / "endura-bench", 0o755)
    put(DATA / "tscsync/set-mode.sh", LIBEXEC / "tscsync-set-mode", 0o755)
    Path("/usr/bin/endura-gui").write_text(GUI_WRAPPER)
    os.chmod("/usr/bin/endura-gui", 0o755)
    for src, dst in (("endurad.service", "/etc/systemd/system/endurad.service"),
                     ("endura-audio-gate.service", "/etc/systemd/user/endura-audio-gate.service"),
                     ("endura-nap.service", "/etc/systemd/user/endura-nap.service")):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_text((DATA / "packaging" / src).read_text()
                             .replace("PYTHONPATH=/usr/lib/endura", f"PYTHONPATH={PYPATH}"))
    put(DATA / "packaging/io.github.endura.desktop", "/usr/share/applications/io.github.endura.desktop")
    kw = Path("/usr/share/kwin/scripts/endura-focus")
    put(DATA / "kwin/endura-focus/metadata.json", kw / "metadata.json")
    put(DATA / "kwin/endura-focus/contents/code/main.js", kw / "contents/code/main.js")
    if have("restorecon"):
        sh("restorecon", "-R", LIB, LIBEXEC, "/usr/bin/endura-gui", "/etc/systemd/system/endurad.service",
           "/etc/systemd/user", kw, check=False)
    calibrate()
    sh("systemctl", "daemon-reload")
    sh("systemctl", "enable", "--now", "endurad")
    sh("systemctl", "restart", "endurad")
    print('\nEndura installed. Open "Endura" from the menu or run: endura-gui')
    if missing:
        print("Some features are disabled, see the warnings above.")



def uninstall():
    if os.geteuid() != 0:
        sys.exit("run as root: sudo endura-uninstall")
    sock = "/run/endura/endura.sock"
    if os.path.exists(sock):
        import socket
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(900)
        s.connect(sock)
        s.sendall(b'{"op":"revert_all"}\n')
        s.makefile().readline()
        print("all tweaks reverted")
    sh("systemctl", "disable", "--now", "endurad", check=False)
    for p in (LIB, LIBEXEC, "/usr/bin/endura-gui", "/etc/systemd/system/endurad.service",
              "/etc/systemd/user/endura-audio-gate.service", "/etc/systemd/user/endura-nap.service",
              "/usr/share/kwin/scripts/endura-focus", "/usr/share/applications/io.github.endura.desktop"):
        p = Path(p)
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    sh("systemctl", "daemon-reload", check=False)
    print("removed (config kept in /etc/endura, telemetry and backups in /var/lib/endura)")


def gui():
    """`endura-gui` from a pip/pipx install: prefer the system copy (has PyQt6)."""
    if Path("/usr/bin/endura-gui").exists() and Path(sys.argv[0]).resolve() != Path("/usr/bin/endura-gui"):
        os.execv("/usr/bin/endura-gui", ["/usr/bin/endura-gui", *sys.argv[1:]])
    from .gui import main
    main()


if __name__ == "__main__":
    {"install": install, "uninstall": uninstall}.get(sys.argv[1] if len(sys.argv) > 1 else "", install)()
