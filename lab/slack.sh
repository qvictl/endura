#!/usr/bin/bash
# slack.sh PID NS: wakeups/s before/after setting timer slack on every thread. Run as root.
P=$1; NS=$2
w(){ cat /proc/$P/task/*/schedstat 2>/dev/null | awk '{s+=$3} END{print s}'; }
a=$(w); sleep 5; b=$(w); echo "before: $(( (b-a)/5 ))/s"
for t in /proc/$P/task/*; do echo $NS > /proc/$(basename $t)/timerslack_ns; done
sleep 1; a=$(w); sleep 5; b=$(w); echo "after:  $(( (b-a)/5 ))/s (slack ${NS} ns)"
