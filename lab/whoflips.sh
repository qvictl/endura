#!/usr/bin/bash
# Freeze each user app cgroup for 4 s and count display flips. Run as root.
T=/sys/kernel/tracing
U=$(ls -d /sys/fs/cgroup/user.slice/user-*.slice/user@*.service | head -1)
vb(){ echo > $T/trace; echo 1 > $T/events/drm/drm_vblank_event/enable; echo 1 > $T/tracing_on; sleep 4; echo 0 > $T/tracing_on; echo 0 > $T/events/drm/drm_vblank_event/enable; grep -vc "^#" $T/trace; }
echo "baseline: $(vb) flips/4s"
for c in $U/app.slice/app-* $U/session.slice/plasma-plasmashell.service; do
  [ -e "$c/cgroup.freeze" ] || continue
  echo 1 > "$c/cgroup.freeze"; sleep 0.5; n=$(vb); echo 0 > "$c/cgroup.freeze"
  echo "$n  $(basename "$c")"
done | sort -n
