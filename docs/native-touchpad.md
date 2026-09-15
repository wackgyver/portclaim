# Native Linux Magic Trackpad

## Architecture

```
hid-magicmouse → complete evdev SYN_REPORT frames → TP10/N1 UDP
              → receiver uinput Type-B touchpad → libinput → Wayland/application
```

The Linux native backend does **not** run `GestureEngine`, emit a relative mouse,
round pointer deltas, synthesize wheel notches, or impose a scroll-inertia model.
It preserves contact identity, coordinates, pressure, full contact size, and
orientation. libinput owns pointer acceleration, tap/click, palm/thumb filtering,
and gestures. Applications own kinetic scrolling and pinch behavior; not all
applications implement them equally.

The virtual device is named **PortClaim Magic Trackpad**. Its vendor/product and
geometry come from the physical device. This preserves libinput's existing Apple
quirks without a new system-wide udev rule. It exposes five virtual slots (the
wire limit), mapped by tracking ID rather than the physical slot number. The hub
still opens both USB interfaces before grabbing either one. Bluetooth needs one.

## Compatibility and protocol

- Linux defaults to `USB_LOOM_TP_BACKEND=native`.
- `USB_LOOM_TP_BACKEND=legacy` explicitly selects the previous mouse/gesture engine.
- Windows keeps the existing `GestureEngine` backend.
- The hub must have both the descriptor API and `proto/tp_native.py` installed.
- Native initialization waits with bounded backoff if metadata is unavailable.
  It does not silently substitute guessed geometry or the legacy mouse backend.

TP10/N1 is backward compatible on the wire:

1. Original 80-byte TP10 packet, including its EV_REL trailer.
2. `<4sIQ>` extension: `TN01`, 32-bit stream epoch, 64-bit capture timestamp in
   **hub monotonic microseconds**.
3. Five `<iHHh>` records: full 32-bit tracking ID, 16-bit touch major/minor,
   signed 16-bit orientation, aligned with the original contact order.

Total: **146 bytes**, below the LAN MTU. Legacy receivers ignore the tail.
Native receivers require it: the old fields clip sizes to 255 and IDs to 32767.
Source timestamps are diagnostic timestamps, not synchronized receiver clocks.

Authenticated `GET /v1/devices/magictrackpad/descriptor` returns schema 1,
transport `TP10/N1`, device identity, maximum contact count, and kernel axis
min/max/resolution. No site IP, credential, or calibration is built into the
receiver. Unknown or implausible geometry fails closed.

A Magic Trackpad claim accepts `native_touchpad: true`. Native claims get
immediate physical reports plus a 100 ms idle heartbeat. Legacy claims keep
125 Hz heartbeats for the old gesture engine's inertia timer. New Linux native
claims set this flag automatically; upgrading an existing claim requires an
explicit reclaim preserving its owner/destination. Starting the receiver does
not seize a device from another client.

## Failure behavior

- Sequence numbers are checked with wrap-around handling; duplicates and old
  packets are ignored. Stream epochs distinguish capture restarts.
- The native receiver accepts packets only from the configured hub's IPv4 address.
  This is **not cryptographic authentication**; keep the existing narrow firewall
  rules and LAN-only deployment.
- After 500 ms without a fresh valid packet, active input is cancelled by removing
  the virtual device. Removal avoids synthesizing a tap or leaving a drag lock
  held. The device is recreated lazily when valid input resumes.
- Initialization/recreation discards queued historical input, rather than replaying
  gestures performed while the receiver was unavailable.
- `SYN_DROPPED` on the hub triggers kernel-state resynchronization and a new epoch.
- An incomplete USB interface pair is not grabbed. Its sibling wait starts when
  the first interface actually appears, not before the device is plugged in.
- Idle packets do not generate repeated uinput reports. Logging is summarized,
  not one flushed journal write per touched frame.

## Omarchy / Hyprland 0.56 Lua profile

This is a **device-specific** example, not an installer action. Back up and edit
`~/.config/hypr/input.lua`, then validate `hyprctl reload` and `hyprctl configerrors`.
Check the exact normalized device name with `hyprctl devices`.

```lua
hl.device({
  name = "portclaim-magic-trackpad",
  sensitivity = 0.0,
  accel_profile = "adaptive",
  natural_scroll = true,
  scroll_method = "2fg",
  scroll_factor = 1.0,
  tap_to_click = true,
  clickfinger_behavior = true,
  tap_and_drag = false,
  drag_3fg = 1, -- requires compositor/libinput support (libinput 1.31 here)
  drag_lock = 0,
  disable_while_typing = false,
})
```

This intentionally replaces the legacy gesture contract. Native physical
click-and-drag and tap mappings follow libinput. The old PortClaim tracking,
inversion, scroll-force, and friction sliders do not apply. The native GUI says
where settings live instead of displaying ineffective sliders. Three-finger
drag and three-finger workspace swipes compete; do not enable both blindly.

No global acceleration, keyboard mapping, display mode, or fallback pointer
needs to be changed. Four-finger desktop gestures can be configured separately.

## Upgrade and rollback

1. Back up local source changes, the receiver unit, and the Hyprland profile.
2. Run the tests before touching live receivers or the hub.
3. Save the authenticated registry snapshot. The hub stores routes **in memory**;
   restarting it without restoring approved routes interrupts all claims.
4. Deploy `hub/{hid,trackpad,server}.py` and `proto/{tp10,tp_native}.py` together.
   The normal hub installer includes the extension but also restarts the service;
   review its wider scope before using it on an existing appliance.
5. Restart the hub only in an approved interruption window and restore the
   original clients and routes. Change only the intended trackpad route to native.
6. Apply the device-specific compositor profile. Restart the local receiver when
   dictation/capture is idle. Check the native descriptor, input classification,
   actual pointer/scroll/drag behavior, microphone, and Xbox path.
7. Verify tray hide/show does not restart any stream.

Receiver rollback: select `USB_LOOM_TP_BACKEND=legacy` in its environment and
restart it. Remove only the added device profile if desired. A legacy receiver
can still read the upgraded hub packets. If restoring the legacy gesture engine,
reclaim that trackpad with `native_touchpad: false` to restore its 125 Hz inertia
heartbeat. Full hub rollback also restores its source backup and saved routes.
Do not reset shared audio, input permissions, or desktop settings.

## Tests

From the repository root, with the receiver's Python environment:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -t .
```

`tests/linux/test_native_touchpad.py` uses fake uinput to verify exact event
lifecycles, identity changes, fractional-scale-free coordinates and cancellation.
`tests/common/test_protocols.py` covers full-width wire data, legacy compatibility
and sequencing. `tests/hub/test_capture.py` tests report boundaries, hot-plug
timing and overflow resynchronization without touching USB. Audio, unchanged
Xbox reports, configuration, legacy gestures and visibility tests live in the
corresponding common/Linux/Windows partitions. See [platform separation](platform-separation.md)
for independent OS gates; mocked tests are not physical input acceptance.

An optional real-libinput observer is `tools/native-libinput-check.c`. Compile
as shown in that file. It opens **only** the supplied test input node and must
be used with a synthetic device disabled in the compositor. Reading evdev may
require explicit temporary permission for that node; do not grant broad input
group membership for a test. Its synthetic motion/scroll/click results are
integration evidence, not a physical input-to-display latency measurement.
