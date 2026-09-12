# PortClaim handover — Omarchy Receiver

Copy to `HANDOVER.omarchy.md` (gitignored) and fill the blanks.
Do not commit a filled copy. Example IPs are not this desk.

## Fabric

| Field | Value |
|-------|--------|
| Client id (`USB_LOOM_CLIENT_ID`) | |
| Hub control (`USB_LOOM_HUB`) | `http://192.168.0.10:27180` |
| This machine Dest (`USB_LOOM_SELF`) | `192.168.0.20` |
| Shared token (`USB_LOOM_TOKEN`) | copy from hub `/etc/usb-loom.env` |
| Overlay (optional) | Tailscale / other Dest the hub can route to |

Paste the three `USB_LOOM_*` values into `~/.config/portclaim/usb-loom.env`
after `./deploy/install-receiver.sh`.

`USB_LOOM_SELF` is the address **the hub uses to send UDP**. Not `127.0.0.1`.

## Firewall

| Direction | Proto | Ports |
|-----------|-------|-------|
| This host → hub | TCP | `:27180` |
| Hub → this host | UDP | `:27182` `:27183` `:27184` `:27185` |

Same L2/L3 or an overlay you control. Guest-isolation Wi-Fi black-holes UDP.

## Handy (dock mic)

Same contract as the Windows Receiver: hub AU10 → PortClaim inject → Handy records the virtual cable.

| Field | Value |
|-------|--------|
| AUR package | `handy` (fallback `handy-bin`, then `nerd-dictation`) |
| Inject sink | `portclaim_mic` |
| Handy input | Default (virtmic sets default source to `portclaim_mic.monitor`) |
| VAD | Off for the first proof |
| Hyprland bind | `bind = SUPER, Space, exec, handy --toggle-transcription` |
| Speaker monitor | Off (dock condenser howls) |

Proof before blaming Handy: AU10 pane shows RMS, then

```bash
parecord --device=portclaim_mic.monitor /tmp/pc-mic.wav
# speak at the dock condenser, stop, play the wav
```

## Proof

```bash
set -a; . ~/.config/portclaim/usb-loom.env; set +a
curl -sS -H "X-Usb-Loom-Token: $USB_LOOM_TOKEN" "$USB_LOOM_HUB/v1/health"
python client/claim.py --hub "$USB_LOOM_HUB" devices
ss -ulnp | grep 2718
# after Claim: one-finger move, two-finger click = right, two-finger scroll
```

Log out once after install so group `input` applies (`/dev/uinput`).

## Out of scope on this box

- usb-loom **hub** (stays on the X200)
- SidestickBridge / Starfield T.A320 map (Windows Receiver)
