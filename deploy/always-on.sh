#!/bin/sh
# Docked ThinkPad X200: stay up with the lid closed. Idempotent.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root" >&2
  exit 1
fi

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

systemctl set-default multi-user.target || true

# systemd will not enter these units even if something asks.
for unit in \
  sleep.target \
  suspend.target \
  hibernate.target \
  hybrid-sleep.target \
  suspend-then-hibernate.target
do
  systemctl mask "$unit" || true
done

mkdir -p /etc/systemd/logind.conf.d /etc/systemd/sleep.conf.d
install -m 0644 "$HERE/logind-usb-loom.conf" /etc/systemd/logind.conf.d/usb-loom-lid.conf
install -m 0644 "$HERE/sleep-usb-loom.conf" /etc/systemd/sleep.conf.d/usb-loom-nosleep.conf

# acpid / laptop helpers can S3 independently of logind on old ThinkPads.
if [ -d /etc/acpi/events ]; then
  for ev in /etc/acpi/events/lid /etc/acpi/events/lidbtn /etc/acpi/events/sleep /etc/acpi/events/sleepbtn; do
    if [ -e "$ev" ]; then
      mv "$ev" "$ev.usb-loom-disabled"
    fi
  done
fi
if [ -f /etc/default/acpi-support ]; then
  if grep -q '^LID_SLEEP=' /etc/default/acpi-support; then
    sed -i 's/^LID_SLEEP=.*/LID_SLEEP=false/' /etc/default/acpi-support
  else
    echo 'LID_SLEEP=false' >> /etc/default/acpi-support
  fi
fi

systemctl disable --now tlp laptop-mode acpid 2>/dev/null || true
systemctl restart systemd-logind || true
systemctl daemon-reload || true

echo "usb-loom always-on: lid ignore, sleep masked"
systemctl get-default
systemctl is-enabled suspend.target hibernate.target 2>&1 || true
if command -v busctl >/dev/null 2>&1; then
  busctl get-property org.freedesktop.login1 /org/freedesktop/login1 \
    org.freedesktop.login1.Manager HandleLidSwitch || true
  busctl get-property org.freedesktop.login1 /org/freedesktop/login1 \
    org.freedesktop.login1.Manager HandleLidSwitchDocked || true
fi
