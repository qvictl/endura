#!/usr/bin/bash
# NVMe/SATA writes per minute and top writing processes. Run as root.
DEV=${1:-$(lsblk -dno NAME | grep -E '^(nvme0n1|sda)$' | head -1)}
snap(){ for p in /proc/[0-9]*; do w=$(awk '/^write_bytes/{print $2}' $p/io 2>/dev/null); [ -n "$w" ] && echo "${p#/proc/} $w"; done; }
snap | sort > /tmp/.dw1; d1=$(awk -v d=$DEV '$3==d{print $8}' /proc/diskstats); sleep 60
snap | sort > /tmp/.dw2; d2=$(awk -v d=$DEV '$3==d{print $8}' /proc/diskstats)
echo "$DEV writes completed / 60 s: $((d2-d1))"
join /tmp/.dw1 /tmp/.dw2 | awk '{d=$3-$2; if(d>0) print d, $1}' | sort -rn | head -10 | while read d p; do echo "$((d/1024)) KB  $(cat /proc/$p/comm 2>/dev/null)"; done
