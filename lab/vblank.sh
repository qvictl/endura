#!/usr/bin/bash
# display flips/s over 10 s and amdgpu PSR state. Run as root.
T=/sys/kernel/tracing
echo > $T/trace; echo 1 > $T/events/drm/drm_vblank_event/enable; echo 1 > $T/tracing_on; sleep 10
echo 0 > $T/tracing_on; echo 0 > $T/events/drm/drm_vblank_event/enable
echo "vblank events/s: $(( $(grep -vc '^#' $T/trace) / 10 ))"; echo > $T/trace
for d in /sys/kernel/debug/dri/*/eDP-*; do [ -e $d/psr_state ] && { echo "psr_state $(cat $d/psr_state) residency $(cat $d/psr_residency)"; break; }; done
