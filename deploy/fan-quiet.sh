#!/bin/sh
# Take the X200 fan off EC auto. Requires thinkpad_acpi fan_control=1.
# Idempotent. Does not touch usb-loom-hub. Does not re-enable TLP.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root" >&2
  exit 1
fi

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

mkdir -p /etc/modprobe.d
cat > /etc/modprobe.d/usb-loom-thinkpad-fan.conf <<'EOF'
# thinkfan cannot write /proc/acpi/ibm/fan without this.
options thinkpad_acpi fan_control=1
EOF

# Runtime: the sysfs node is often mode-writable but kernel-rejects the write.
if [ -e /sys/module/thinkpad_acpi/parameters/fan_control ]; then
  { echo 1 > /sys/module/thinkpad_acpi/parameters/fan_control; } 2>/dev/null || true
  { echo Y > /sys/module/thinkpad_acpi/parameters/fan_control; } 2>/dev/null || true
fi

fan_writable() {
  echo level auto > /proc/acpi/ibm/fan 2>/dev/null
}

if ! fan_writable; then
  echo "usb-loom fan: reloading thinkpad_acpi with fan_control=1"
  modprobe -r thinkpad_acpi 2>/dev/null || true
  modprobe thinkpad_acpi fan_control=1 || true
fi
if ! fan_writable; then
  echo "usb-loom fan: fan_control not live yet; thinkfan enable will wait for reboot" >&2
fi

export DEBIAN_FRONTEND=noninteractive
need=""
for pkg in thinkfan lm-sensors; do
  if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
    need="$need $pkg"
  fi
done
if [ -n "$need" ]; then
  apt-get update
  # shellcheck disable=SC2086
  apt-get install -y --no-install-recommends $need
fi

install -m 0644 "$HERE/thinkfan.conf" /etc/thinkfan.conf
if [ -f /etc/default/thinkfan ]; then
  if grep -q '^START=' /etc/default/thinkfan; then
    sed -i 's/^START=.*/START=yes/' /etc/default/thinkfan
  else
    echo 'START=yes' >> /etc/default/thinkfan
  fi
fi

systemctl daemon-reload
systemctl enable thinkfan.service
# Do not restart usb-loom-hub. thinkfan only.
if fan_writable && systemctl restart thinkfan.service; then
  echo "usb-loom fan: thinkfan active"
else
  echo "usb-loom fan: thinkfan enabled; start after reboot if fan_control was not live" >&2
  systemctl status thinkfan.service --no-pager || true
fi

cat /proc/acpi/ibm/fan
cat /proc/acpi/ibm/thermal
