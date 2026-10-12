#!/usr/bin/env python3
"""Per-second battery power; lists the busiest processes in seconds above a threshold."""
import collections, os, sys, time
from _power import bat_w
THR = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
def procs():
    d = {}
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                s = open(f"/proc/{p}/stat").read(); f = s[s.rindex(")") + 2:].split()
                d[p] = (s[s.index("(") + 1:s.rindex(")")], int(f[11]) + int(f[12]))
            except Exception:
                pass
    return d
prev, agg, rows = procs(), collections.Counter(), []
for _ in range(120):
    time.sleep(1); w = bat_w(); cur = procs(); top = collections.Counter()
    for p, (n, t) in cur.items():
        if p in prev and t > prev[p][1]:
            top[n] += t - prev[p][1]
    prev = cur; rows.append(w); agg.update(top)
    if w > THR:
        print(f"{w:5.2f}W  " + ", ".join(f"{n}:{v}" for n, v in top.most_common(5)))
print("mean %.2f W; CPU ticks overall: %s" % (sum(rows) / len(rows), agg.most_common(12)))
