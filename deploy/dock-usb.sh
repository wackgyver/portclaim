#!/bin/sh
# Keep Ultrabase / notebook USB awake so dock ports can carry genesis HID.
# usbcore is built-in on PVE, so udev + this runtime pass matter now;
# grub cmdline persists across reboot. Idempotent.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root" >&2
  exit 1
fi

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

# Runtime: never autosuspend USB on this appliance.
if [ -w /sys/module/usbcore/parameters/autosuspend ]; then
  echo -1 > /sys/module/usbcore/parameters/autosuspend || true
fi
if [ -w /sys/module/usbcore/parameters/old_scheme_first ]; then
  echo Y > /sys/module/usbcore/parameters/old_scheme_first || true
fi

for node in /sys/bus/usb/devices/*; do
  if [ -f "$node/power/control" ]; then
    echo on > "$node/power/control" || true
  fi
  if [ -f "$node/power/autosuspend" ]; then
    echo -1 > "$node/power/autosuspend" || true
  fi
done

# Runtime quirks for the next Magic Trackpad plug (builtin usbcore ignores modprobe.d).
# g=DELAY_INIT n=DELAY_CTRL_MSG k=NO_LPM j=IGNORE_REMOTE_WAKEUP
if [ -w /sys/module/usbcore/parameters/quirks ]; then
  echo '05ac:0265:gknj' > /sys/module/usbcore/parameters/quirks || true
fi

# Persist for the next boot (usbcore is built-in; modprobe.d is a fallback).
mkdir -p /etc/modprobe.d /etc/default/grub.d
cat > /etc/modprobe.d/usb-loom-usb.conf <<'EOF'
# Applied only if usbcore is a module. PVE builds it in; see grub.d / kernel cmdline.
options usbcore autosuspend=-1 old_scheme_first=1 quirks=05ac:0265:gknj
EOF

cat > /etc/default/grub.d/usb-loom.cfg <<'EOF'
# usb-loom: dock USB (Ultrabase EHCI hub) must not autosuspend Apple HID.
GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT usbcore.autosuspend=-1 usbcore.old_scheme_first=1 usbcore.quirks=05ac:0265:gknj"
EOF

if command -v update-grub >/dev/null 2>&1; then
  update-grub >/dev/null 2>&1 || true
fi

# This host boots GRUB. grub.d/usb-loom.cfg is the persistent path.
# If /etc/kernel/cmdline exists (some PVE installs), append the same tokens.
USB_LOOM_CMDLINE="usbcore.autosuspend=-1 usbcore.old_scheme_first=1 usbcore.quirks=05ac:0265:gknj"
if [ -f /etc/kernel/cmdline ]; then
  line=$(tr '\n' ' ' </etc/kernel/cmdline)
  for tok in $USB_LOOM_CMDLINE; do
    case " $line " in
      *" $tok "*) ;;
      *) line="$line $tok" ;;
    esac
  done
  printf '%s\n' "$line" | sed 's/  */ /g;s/[[:space:]]*$//' >/etc/kernel/cmdline
  if command -v proxmox-boot-tool >/dev/null 2>&1; then
    proxmox-boot-tool refresh >/dev/null 2>&1 || true
  fi
fi

# Magic Trackpad 2 wireless needs a BT 4.0+ dongle (LMP 0x6+) and USB OOB
# HID reports 0x34/0x35. Do not enable bluetoothd on the BCM2045B.

echo "usb-loom dock USB: Ultrabase powered, autosuspend off"
lsusb | grep -E "17ef:1005|05ac:0265|044f:0406|31b2:0011" || true
