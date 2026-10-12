#!/usr/bin/env python3
"""meas.py SECONDS: battery power statistics."""
import sys, time
from _power import bat_w, sock_w
n = int(sys.argv[1]) if len(sys.argv) > 1 else 120
b, s = [], []
for _ in range(n):
    b.append(bat_w()); x = sock_w(); x is not None and s.append(x); time.sleep(1)
bs = sorted(b)
print("bat mean %.2f median %.2f p10 %.2f p90 %.2f" % (sum(b) / n, bs[n // 2], bs[n // 10], bs[n * 9 // 10])
      + ("  soc mean %.2f" % (sum(s) / len(s)) if s else ""))
