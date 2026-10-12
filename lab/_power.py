import glob, os, struct
def _bat():
    for d in sorted(glob.glob("/sys/class/power_supply/*")):
        if open(f"{d}/type").read().strip() == "Battery":
            return d
BAT = _bat()
def bat_w():
    try:
        return abs(int(open(f"{BAT}/power_now").read())) / 1e6
    except OSError:
        i = int(open(f"{BAT}/current_now").read()); u = int(open(f"{BAT}/voltage_now").read())
        return abs(i) * u / 1e12
GM = next((p for p in sorted(glob.glob("/sys/class/drm/card*/device/gpu_metrics"))
           if open(os.path.join(os.path.dirname(p), "vendor")).read().strip() == "0x1002"), None)
def sock_w():
    if not GM:
        return None
    b = open(GM, "rb").read()
    if b[2] != 2:
        return None
    v = struct.unpack_from("<H", b, 40)[0]
    return None if v == 0xFFFF else v / 1000
