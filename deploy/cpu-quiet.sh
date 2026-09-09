#!/bin/sh
# PVE kernels default to governor=performance. On this X200 that pins the
# P8800 at 2.66 GHz while Proxmox shows 2% CPU. ondemand parks at 800 MHz.
# Do not use powersave on acpi-cpufreq — that locks min clock.
# Idempotent. Does not touch usb-loom-hub.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root" >&2
  exit 1
fi

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
GOVERNOR="ondemand"

apply_governor() {
  found=""
  for node in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    [ -f "$node" ] || continue
    avail=$(cat "$(dirname "$node")/scaling_available_governors" 2>/dev/null || true)
    case " $avail " in
      *" $GOVERNOR "*) ;;
      *)
        echo "usb-loom cpufreq: $GOVERNOR not in: $avail" >&2
        exit 1
        ;;
    esac
    echo "$GOVERNOR" > "$node"
    found=1
  done
  if [ -z "$found" ]; then
    echo "usb-loom cpufreq: no scaling_governor nodes" >&2
    exit 1
  fi
}

install -d /usr/local/sbin
cat > /usr/local/sbin/usb-loom-cpufreq <<EOF
#!/bin/sh
# Applied at boot by usb-loom-cpufreq.service. Do not write powersave.
set -eu
for node in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
  [ -f "\$node" ] || continue
  echo $GOVERNOR > "\$node"
done
EOF
chmod 0755 /usr/local/sbin/usb-loom-cpufreq

install -m 0644 "$HERE/usb-loom-cpufreq.service" /etc/systemd/system/usb-loom-cpufreq.service
systemctl daemon-reload
systemctl enable usb-loom-cpufreq.service
apply_governor

echo "usb-loom cpufreq: $GOVERNOR"
for node in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
  [ -f "$node" ] || continue
  cpu=$(echo "$node" | sed -n 's|.*/\(cpu[0-9]*\)/.*|\1|p')
  freq=$(cat "$(dirname "$node")/scaling_cur_freq")
  echo "  $cpu $(cat "$node") ${freq} kHz"
done
