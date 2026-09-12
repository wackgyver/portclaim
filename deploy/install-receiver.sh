#!/bin/sh
# PortClaim Receiver on Arch / Omarchy. Does not install the usb-loom hub.
#   git clone https://github.com/wackgyver/portclaim.git
#   cd portclaim
#   ./deploy/install-receiver.sh
set -eu

if [ "$(id -u)" -eq 0 ]; then
  echo "run as your desktop user (the script uses sudo where needed)" >&2
  exit 1
fi

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$HERE/.." && pwd)"
USER_NAME="$(id -un)"
CFG="${XDG_CONFIG_HOME:-$HOME/.config}/portclaim"
LIB="$HOME/.local/lib/portclaim"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [ ! -f /etc/os-release ] || ! grep -qiE 'arch|omarchy' /etc/os-release; then
  echo "this installer targets Arch / Omarchy (pacman)." >&2
  echo "clone the repo and adapt the packages, or continue at your own risk." >&2
fi

# Arch package names drift; try listed Extra packages.
if command -v pacman >/dev/null 2>&1; then
  extra=""
  for pkg in python tk python-pip python-evdev python-libevdev pipewire pipewire-pulse pulseaudio-utils; do
    if pacman -Si "$pkg" >/dev/null 2>&1; then
      extra="$extra $pkg"
    fi
  done
  if [ -n "$extra" ]; then
    # shellcheck disable=SC2086
    sudo pacman -S --needed --noconfirm $extra
  else
    echo "pacman could not resolve Receiver packages; install python, tk, pipewire-pulse, pulseaudio-utils by hand." >&2
  fi
else
  echo "pacman not found; skip package install." >&2
fi

if ! python -c "import evdev" 2>/dev/null && ! python -c "import libevdev" 2>/dev/null; then
  python -m pip install --user evdev || true
fi
if ! python -c "import vgamepad" 2>/dev/null; then
  python -m pip install --user vgamepad || echo "vgamepad pip failed; xboxelite sink needs it + /dev/uinput" >&2
fi

sudo install -m 0644 "$HERE/99-portclaim-uinput.rules" /etc/udev/rules.d/99-portclaim-uinput.rules
if command -v udevadm >/dev/null 2>&1; then
  sudo udevadm control --reload-rules || true
  sudo udevadm trigger --name-match=uinput || true
fi
if ! id -nG "$USER_NAME" | grep -qw input; then
  sudo usermod -aG input "$USER_NAME"
  echo "added $USER_NAME to group input — log out and back in before claiming the trackpad"
fi

mkdir -p "$CFG" "$LIB/src" "$UNIT_DIR" "$HOME/.local/lib/portclaim"
# Link the clone so updates are git pull, not a second copy of secrets.
ln -sfn "$ROOT/client" "$LIB/src/client"
ln -sfn "$ROOT/proto" "$LIB/src/proto"
install -m 0755 "$HERE/portclaim-virtmic.sh" "$HOME/.local/lib/portclaim/portclaim-virtmic.sh"

if [ ! -f "$CFG/usb-loom.env" ]; then
  install -m 0644 "$HERE/usb-loom-receiver.env.example" "$CFG/usb-loom.env"
  echo "wrote $CFG/usb-loom.env — paste USB_LOOM_TOKEN / HUB / SELF from HANDOVER.omarchy.md"
else
  echo "kept existing $CFG/usb-loom.env"
fi

install -m 0644 "$HERE/portclaim.service" "$UNIT_DIR/portclaim.service"
install -m 0644 "$HERE/portclaim-virtmic.service" "$UNIT_DIR/portclaim-virtmic.service"
systemctl --user daemon-reload
systemctl --user enable portclaim-virtmic.service
systemctl --user start portclaim-virtmic.service || echo "virtmic start failed (PipeWire not up yet); it will retry at login"

if command -v omarchy-pkg-aur-install >/dev/null 2>&1; then
  echo "Handy (optional):  omarchy-pkg-aur-install handy"
elif command -v yay >/dev/null 2>&1; then
  echo "Handy (optional):  yay -S handy"
  echo "If the source build hurts:  yay -S handy-bin"
else
  echo "Handy (optional): install AUR package handy (or handy-bin). Fallback: nerd-dictation on portclaim_mic.monitor"
fi

echo
echo "PortClaim Receiver files are in $ROOT"
echo "Env: $CFG/usb-loom.env"
echo "Start:  systemctl --user enable --now portclaim"
echo "Or:     set -a; . $CFG/usb-loom.env; set +a; python $ROOT/client/receiver_app.py"
echo
echo "Firewall: this host UDP 27182-27185 from the hub; this host TCP 27180 to the hub."
echo "Hyprland Handy bind:  bind = SUPER, Space, exec, handy --toggle-transcription"
echo "Set Handy input to Default (default source is portclaim_mic.monitor). VAD off for first proof."
echo "Do not enable PortClaim speaker monitor — the dock condenser howls."
