#!/usr/bin/bash
# System-wide wakeup census over 10 s plus top wakers (ftrace). Run as root.
s(){ awk -v k="$1" '$1==k{s=0;for(i=2;i<=NF;i++) if($i~/^[0-9]+$/) s+=$i; print s}' /proc/interrupts; }
c1=$(awk '/^ctxt/{print $2}' /proc/stat); l1=$(s LOC:); i1=$(s CAL:); sleep 10
c2=$(awk '/^ctxt/{print $2}' /proc/stat); l2=$(s LOC:); i2=$(s CAL:)
echo "ctxt/s $(( (c2-c1)/10 ))  local-timer/s $(( (l2-l1)/10 ))  call-IPI/s $(( (i2-i1)/10 ))"
T=/sys/kernel/tracing
echo > $T/trace; echo 1 > $T/events/sched/sched_wakeup/enable; echo 1 > $T/tracing_on; sleep 3
echo 0 > $T/tracing_on; echo 0 > $T/events/sched/sched_wakeup/enable
grep -v "^#" $T/trace | grep -oE "comm=[^ ]+" | sort | uniq -c | sort -rn | head -25; echo > $T/trace
