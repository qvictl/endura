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

SYSTEM_DEPS = {
    "dnf": ["python3-pyqt6", "python3-pulsectl", "python3-dbus", "python3-gobject", "gcc", "pulseaudio-utils"],
    "apt-get": ["python3-pyqt6", "python3-pulsectl", "python3-dbus", "python3-gi", "gcc", "pulseaudio-utils"],
    "pacman": ["python-pyqt6", "python-pulsectl", "python-dbus", "python-gobject", "gcc", "libpulse"],
}
INSTALL_CMD = {"dnf": ["dnf", "-y", "install"], "apt-get": ["apt-get", "install", "-y"],
               "pacman": ["pacman", "-S", "--needed", "--noconfirm"]}
GUI_WRAPPER = "#!/usr/bin/bash\nPYTHONPATH=/usr/lib/endura exec /usr/bin/python3 -m endura.gui \"$@\"\n"


def sh(*cmd, check=True, **kw):
    print("+", " ".join(map(str, cmd)))
    return subprocess.run(list(map(str, cmd)), check=check, **kw)


def have(cmd):
    return shutil.which(cmd) is not None


def system_python_has(*mods):
    code = "; ".join(f"import {m}" for m in mods)
    return subprocess.run(["/usr/bin/python3", "-c", code], capture_output=True).returncode == 0


def install_deps():
    if system_python_has("PyQt6", "pulsectl", "dbus", "gi") and have("gcc") and have("parec"):
        return
    for pm, pkgs in SYSTEM_DEPS.items():
        if have(pm):
            sh(*INSTALL_CMD[pm], *pkgs)
            return
    sys.exit("Install PyQt6, pulsectl, dbus-python, PyGObject (for /usr/bin/python3), gcc and parec, then rerun.")


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
    install_deps()
    # self-contained copy of the package for the system services
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
    put(DATA / "packaging/endurad.service", "/etc/systemd/system/endurad.service")
    put(DATA / "packaging/endura-audio-gate.service", "/etc/systemd/user/endura-audio-gate.service")
    put(DATA / "packaging/endura-nap.service", "/etc/systemd/user/endura-nap.service")
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
