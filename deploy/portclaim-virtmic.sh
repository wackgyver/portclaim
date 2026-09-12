#!/bin/sh
# Load the PipeWire/Pulse null sink Handy records (CABLE Output analog).
# Playback: portclaim_mic  |  Capture: portclaim_mic.monitor
set -eu

if ! command -v pactl >/dev/null 2>&1; then
  echo "pactl missing (install pulseaudio-utils / pipewire-pulse)" >&2
  exit 1
fi

i=0
while [ "$i" -lt 30 ]; do
  if pactl info >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 0.5
done

if pactl list short sinks 2>/dev/null | grep -q '^[[:digit:]]*[[:space:]]portclaim_mic'; then
  echo "portclaim_mic already loaded"
else
  pactl load-module module-null-sink \
    sink_name=portclaim_mic \
    sink_properties=device.description=PortClaim_Mic
  echo "loaded portclaim_mic"
fi

# Handy on Linux often only lists Default. Point Default at the monitor.
if pactl list short sources 2>/dev/null | grep -q 'portclaim_mic.monitor'; then
  pactl set-default-source portclaim_mic.monitor || true
fi
