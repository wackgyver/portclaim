#!/bin/sh
# Headless usb-loom hub on Debian 12/13 or a privileged LXC.
# usb-loom is the engine; PortClaim is the product display name.
set -eu

TOKEN="${USB_LOOM_TOKEN:-}"
CONTROL="${USB_LOOM_CONTROL_PORT:-27180}"

while [ $# -gt 0 ]; do
  case "$1" in
    --host) shift 2 ;; # kept for Deploy-Hub.ps1 compatibility; claims pick dest
    --port) shift 2 ;;
    --adapter) shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$TOKEN" ]; then
  echo "USB_LOOM_TOKEN or --token is required" >&2
  exit 2
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root" >&2
  exit 1
fi

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$HERE/.." && pwd)"

export DEBIAN_FRONTEND=noninteractive
# Only install what is missing. Do not list systemd/openssh — a reinstall
# of those pulls security bumps and can bounce the only console.
need=""
for pkg in python3 alsa-utils; do
  if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
    need="$need $pkg"
  fi
done
if [ -n "$need" ]; then
  apt-get update
  # shellcheck disable=SC2086
  apt-get install -y --no-install-recommends $need
fi

# Lid closed on the Ultrabase must not S3 the hub.
sh "$HERE/always-on.sh"
# PVE kernel defaults to performance; this appliance parks ondemand.
# Fan curve is thinkfan. Neither script restarts usb-loom-hub.
sh "$HERE/cpu-quiet.sh"
sh "$HERE/fan-quiet.sh"

install -d /usr/local/lib/usb-loom
install -m 0755 "$ROOT/hub/hid.py" "$ROOT/hub/server.py" "$ROOT/hub/audio.py" "$ROOT/hub/trackpad.py" /usr/local/lib/usb-loom/
install -m 0644 "$ROOT/proto/sb10.py" /usr/local/lib/usb-loom/sb10.py
install -m 0644 "$ROOT/proto/au10.py" /usr/local/lib/usb-loom/au10.py
install -m 0644 "$ROOT/proto/tp10.py" /usr/local/lib/usb-loom/tp10.py
cat > /usr/local/sbin/usb-loom-hub <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/local/lib/usb-loom/server.py "$@"
EOF
chmod 0755 /usr/local/sbin/usb-loom-hub

cat > /etc/usb-loom.env <<EOF
USB_LOOM_TOKEN=$TOKEN
USB_LOOM_CONTROL_PORT=$CONTROL
USB_LOOM_BIND=0.0.0.0
EOF
chmod 0640 /etc/usb-loom.env

install -m 0644 "$HERE/usb-loom-hub.service" /etc/systemd/system/usb-loom-hub.service
install -m 0644 "$HERE/99-usb-loom.rules" /etc/udev/rules.d/99-usb-loom.rules
sh "$HERE/dock-usb.sh"
udevadm control --reload-rules || true
udevadm trigger --subsystem-match=usb || true

systemctl daemon-reload
udevadm control --reload-rules || true
systemctl enable usb-loom-hub.service
# enable --now will not reload an already-running unit; always bounce.
systemctl restart usb-loom-hub.service

echo
echo "usb-loom hub on :$CONTROL  (token is set, not printed)"
echo "From a client:  python client/claim.py --hub http://HUB:$CONTROL devices"
