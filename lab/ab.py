#!/usr/bin/env python3
"""A/B power test: alternate state A/B commands, measure battery W (and AMD SoC W)."""
import argparse, statistics as st, subprocess, time
from _power import bat_w, sock_w
ap = argparse.ArgumentParser()
ap.add_argument("--a", required=True); ap.add_argument("--b", required=True)
ap.add_argument("--settle", type=int, default=8); ap.add_argument("--win", type=int, default=25)
ap.add_argument("--rounds", type=int, default=4); ap.add_argument("--final", default="a")
a = ap.parse_args()
def busy():
    f = list(map(int, open("/proc/stat").readline().split()[1:])); return sum(f) - f[3] - f[4], sum(f)
res = {"A": [], "B": []}
for r in range(a.rounds):
    for name, cmd in (("A", a.a), ("B", a.b)):
        subprocess.run(cmd, shell=True); time.sleep(a.settle)
        bs, ss = [], []; b0, t0 = busy()
        for _ in range(a.win):
            bs.append(bat_w()); x = sock_w(); x is not None and ss.append(x); time.sleep(1)
        b1, t1 = busy()
        m = (st.mean(bs), st.mean(ss) if ss else float("nan"), (b1 - b0) / (t1 - t0) * 100)
        res[name].append(m)
        print(f"round{r} {name}: bat={m[0]:.2f}W soc={m[1]:.2f}W cpu={m[2]:.1f}%", flush=True)
subprocess.run(a.a if a.final == "a" else a.b, shell=True)
for k in "AB":
    c = list(zip(*res[k])); print(k, "bat %.2f±%.2f soc %.2f cpu %.1f%%" % (st.mean(c[0]), st.pstdev(c[0]), st.mean(c[1]), st.mean(c[2])))
d = [b[0] - x[0] for x, b in zip(res["A"], res["B"])]
print("B-A bat delta per round:", ["%.2f" % x for x in d], "mean %.2f" % st.mean(d))
