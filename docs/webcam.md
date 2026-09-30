# Webcam — explicit Linux receiver preview

**Video only. One USB camera, one owner, no recording or automatic activation.**
Windows virtual-camera receiving is not implemented; existing Windows input and
audio are unchanged. This is not desktop video, raw USB forwarding, or an OBS
integration. Do not describe a passing build as real camera/app acceptance.

## Path and boundaries

```text
USB UVC camera on the existing Linux hub
  -> native V4L2 MMAP capture of baseline MJPEG (no hub encoder)
  -> authenticated, lease-bound HTTP multipart stream on the existing control port
  -> receiver FFmpeg decoder / color conversion
  -> dedicated exclusive-caps v4l2loopback: PortClaim Camera
  -> a camera application's selected input
```

The hub needs only its existing Python runtime, the kernel UVC driver and camera
access. No FFmpeg/GStreamer, image library or new UDP listener is needed on the
hub. Enabling webcam support also requires an explicit microphone pin, so its
composite USB audio interface cannot displace the intended condenser. The receiver uses a maintained **FFmpeg 5.1+** with MJPEG,
`scale` and rawvideo support. No new Python package is required.

The initial hardware motivation is a Sonix `0c45:6366` USB 2.0 Camera. Cached USB
descriptors advertise MJPEG 1920x1080 and 1280x720 at up to 30 fps; that is
**advertised capability**, not measured throughput or proof of picture quality.
The preview requests 1280x720/30 by default. Choices are 640x480, 1280x720 or
1920x1080 and 5/10/15/20/25/30 fps. The capture driver must accept the requested
MJPEG resolution and rate exactly. No silent switch to raw USB video, another
resolution, camera, microphone, or software encoding.

The Sonix device can append 1–7 zero bytes after JPEG EOI to align its V4L2
buffer to eight bytes. A bounded live sample observed every padding length from
0 through 7; a four-byte assumption was too narrow. The capture boundary removes
**only** that exact padding pattern from an eight-byte-aligned buffer. Full JPEG validation still follows;
nonzero/long trailers, concatenated images and padding received over the network
remain rejected. Hardware framing normalization is not a relaxed wire contract.

## Safety and ownership

- Hub support defaults **disabled**. The operator must set
  `USB_LOOM_CAMERA_ENABLED=1` privately before use. If `--adapter` is supplied to
  the hub, its list must also include `webcam`. No camera thread starts at boot.
- Discovery reads sysfs, not video frames. With no source selector, exactly one
  UVC video-index0 candidate is required. Multiple candidates fail closed.
  `USB_LOOM_CAMERA_DEVICE` can name a stable `/dev/v4l/by-id/…` or
  `/dev/v4l/by-path/…` link. Numeric `/dev/videoN` source selectors are rejected.
- A claim reserves the selected source for five seconds but does **not** open it.
  An authenticated stream request with that claim's private lease opens it.
  USB identity/devnum are checked again; hotplug never silently changes source.
- A busy camera returns HTTP 409. Release it before a different receiver claims
  it. The old stream must close before another capture can open. There is no
  force-steal or automatic reconnect in this preview.
- Webcam is deliberately absent from `CONNECT`, `GENESIS` and `DEFAULT_PORTS`.
  Use its explicit Start/Stop controls. The generic UDP claim CLI refuses it.
- **Hiding the receiver does not stop an explicitly activated camera.** Use Stop
  Camera, Quit, or stop its owning process. No automatic camera claim after a
  receiver restart, service start, login or reconnection.
- The receiver opens only a separately provisioned virtual output with driver
  `v4l2 loopback`, exact label `PortClaim Camera`, character-device major 81,
  virtual sysfs ancestry and unused exclusive OUTPUT capabilities. It never
  creates a device, loads/unloads a module, grants groups/ACLs or touches a
  physical webcam/another application's loopback output.
- Format is YUYV/BT.709 limited range. The receiver verifies the format and fps,
  disables frame duplication and verifies a 1000 ms kernel timeout. It writes
  synthetic black on normal start/stop; the dedicated node's **default NULL
  timeout image** is the crash backstop. Provision a fresh dedicated node; do not
  reuse a device with another application's custom timeout image. Applications
  may retain their own last displayed frame; PortClaim cannot erase their caches.
- Hub capture has a bounded buffer ring and forwards the latest available frame.
  Frames are at most 4 MiB. Network writes, per-frame reads and decoder writes
  have deadlines; slow consumers terminate rather than grow an application
  queue. A frame carries a strictly increasing stream sequence. These bounds
  are not an end-to-end latency guarantee.
- Lease secrets occur only in the claim response and request headers—not
  `/v1/routes`, inventory, URLs, FFmpeg arguments/environment or logs. Stream
  responses are `no-store`. The client never follows redirects.
- The stream uses the existing shared hub secret **plus** a random per-claim
  lease bound to the requesting source IP. This is LAN authentication, **not
  encryption** or a multi-tenant identity system. Plain HTTP exposes headers and
  pictures to network observers. Use only the trusted private LAN; no public
  forwarding. HTTPS is supported at the client with normal certificate checks
  when a separately managed TLS endpoint is present. Do not bypass verification.

## Preserve the condenser microphone

Configure the **hub's private env**, not the receiver's Pulse/PipeWire sink:

```text
# DCMT Technology USB Condenser Microphone (existing supported model)
USB_LOOM_MIC_USB_ID=31b2:0011
```

Use the actual VID:PID for other hardware. With a pin configured, `hub/audio.py`
requires exactly one matching ALSA capture PCM. It resolves the current stable
ALSA card ID and opens `hw:CARD=<id>,DEV=<pcm>` rather than a changing numeric
card slot. USB identity/generation are checked before opening and again before
forwarding the first PCM frame. No change to 44.1 kHz, mono S16_LE, gain or AU10
wire bytes. No system/default microphone changes.

If the condenser is missing, the identity is unavailable, the pin is malformed
(including blank), or more than one capture PCM matches, no microphone is opened
or substituted. The existing `mic` route remains reserved and the hub waits for
the pinned microphone. Repeated unchanged missing-source warnings are suppressed.
Inventory audio rows include `mic_selection` for diagnosis. These metadata are
selection state, not proof that usable audio is arriving.

With `USB_LOOM_CAMERA_ENABLED=1`, an unset microphone pin also disables mic
capture until configured. Legacy first-USB/internal fallback is retained **only**
when the pin is genuinely unset and webcam support is off. VID:PID pins a model,
not a cryptographically authenticated individual unit; duplicate matching PCMs
fail closed rather than choosing the first. This does not add stereo webcam-mic
support or automatic format negotiation.

Changing env on disk does not alter a running hub process. Apply the pin only in
an approved, route-preserving deployment. Confirm the condenser is actually
present and its PCM works before declaring the combined setup accepted.

## Arch / Omarchy prerequisites (separate approval)

See [optional native packages](../deploy/linux/packages-camera.arch.txt):
`ffmpeg`, `v4l-utils`, `v4l2loopback-dkms`, `v4l2loopback-utils`, plus **headers
matching the running kernel exactly**. DKMS pulls its build tools. The receiver's
Python venv and ordinary Tk/PipeWire requirements remain as documented in the
[Linux guide](../deploy/linux/README.md).

Review package availability, `uname -r`, DKMS compatibility/signing, current
loopback devices and their owners before installation. Omarchy package tools
handle their own privilege prompt; do not wrap them in another sudo. Kernel
changes, Secure Boot changes, logout/reboot and broad `video`/`input` group
membership are not part of receiver setup.

**No commands below have been executed by the implementation tests.** They are
operator examples for an approved provisioning window, not an unattended script.

For a machine with **no existing loaded v4l2loopback** and a verified unused
video number, a one-session dedicated device can be provisioned as follows:

```sh
# Example number only: inspect first. Requires separate privileged approval.
sudo modprobe v4l2loopback devices=1 video_nr=42 \
  card_label="PortClaim Camera" exclusive_caps=1
```

If v4l2loopback is already loaded for OBS or another application, **do not unload
it** or replace its module options. Stop and plan a separate dynamic dedicated
node using the installed version's `v4l2loopback-ctl add --help`; options vary.
Do not adopt another producer's node. Persistent module loading is a separate
opt-in configuration change; the example above is not persistent.

Verify active-session access to the new node without capturing anything. If
read/write access is absent, use a reviewed node-specific/session permission
plan, not `chmod 666` or broad group membership. The normal user—not root—runs
the receiver. A restored system must recreate/revalidate the node; do not copy
kernel modules or old device numbers blindly.

Privately add the chosen output to the receiver env:

```text
USB_LOOM_CAMERA_OUTPUT=/dev/video42
```

The receiver installer does not provision this. Editing env requires an approved
receiver restart to affect an already running process. Native Linux touchpad,
mic ownership, disabled login autostart and `Restart=no` stay unchanged.

## Hub upgrade and activation checkpoint

Keep the existing hub. **Do not run the general hub installer merely to add a
camera to a working deployment.** It rewrites hub env, performs appliance setup
and restarts the service. It is intended for reviewed whole-hub installation,
not safe live feature migration.

For an approved targeted update:

1. Check active dictation/calls and announce the input/audio interruption. Keep a
   fallback keyboard/mouse available. Read `/v1/routes` and `/v1/devices` with
   credentials in headers; privately save the exact clients/routes. Preserve
   owners, destinations, ports and native-touchpad flags. The hub registry is
   in memory and is lost on restart. **Also verify the actual ALSA capture source.**
   Restoring a `mic` route alone does not pin it: older hubs use first-USB/fallback
   selection. Configure the microphone pin above with the new audio module.
   If the condenser is missing, resolve its connection before an uninterrupted
   microphone service can be promised. Never substitute the webcam microphone.
2. Back up the deployed modules, private env and service configuration outside
   the repo. Do not export a token, serial-bearing source link or route backup
   to Git/chat.
3. Deploy the reviewed hub `server.py`, `audio.py` (microphone pin), new
   `webcam.py`, `v4l2_capture.py` and protocol `mjpeg.py` alongside the existing matching flat hub modules in
   `/usr/local/lib/usb-loom/`. The general installer includes these new files for
   fresh installations. No new hub packages, udev change or USB reset is needed
   merely for this already-enumerated UVC device.
4. Retain all private env fields; set `USB_LOOM_MIC_USB_ID` to the intended
   microphone, then opt in with `USB_LOOM_CAMERA_ENABLED=1` and the reviewed
   stable video source selector. Preserve the existing hub bind/firewall.
5. Restart the **same** hub once, verify health, then restore the saved clients
   and routes exactly, checking for concurrent ownership changes. Do not run
   bulk Connect to overwrite other destinations. Camera remains unclaimed.
6. After receiver output provisioning and an approved receiver update/restart,
   select **Webcam → Start & claim camera**. Start with 720p/30. Its status counts
   frames actually written to the virtual device, not just bytes received.
7. Select **PortClaim Camera** in the intended application. Prove picture,
   orientation, color, fps and latency; then prove Stop, close/quit, unplug,
   network loss and restart behavior. Confirm no recording files appeared.
   Validate 1080p/30 separately while watching hub CPU, USB/LAN load and input
   responsiveness. Parser/synthetic tests cannot establish these outcomes.

For an explicit foreground receiver instead of the GUI, from the reviewed source
and its dependency environment:

```sh
python client/webcam_sink.py --start --size 1280x720 --fps 30
# Ctrl+C stops this camera, blanks/closes its output and releases its lease.
```

Use either the GUI camera or this foreground process, not both. The lease/output
checks refuse a second owner. No microphone is selected by these commands. A UVC
camera may also enumerate an ALSA microphone; this preview does not integrate it.
The pinned hub microphone remains independent of camera activation. Confirm its
actual audio source and signal independently—especially after USB enumeration changes.

## HTTP contract (MJPEG/1)

All endpoints still require `X-Usb-Loom-Token`. No new firewall port is introduced.
The receiver pulls video from the same hub TCP control port; existing UDP device
ports and bytes are untouched.

- `POST /v1/devices/webcam/claim`: object containing `client_id` and optional
  `mode: {width, height, fps}`. No arbitrary capture path, URL, destination or
  codec in this body. Response includes mode, fixed `stream_path` and a private
  `lease`. Do not log the response.
- `GET /v1/devices/webcam/stream`: same hub token plus
  `X-PortClaim-Camera-Lease` from that response. Requires the same peer IP and
  permits one stream. Responds `multipart/x-mixed-replace` with boundary
  `portclaim-mjpeg-v1`. Each part has `Content-Type: image/jpeg`, bounded
  `Content-Length`, and increasing `X-PortClaim-Sequence`, followed by JPEG+CRLF.
  Baseline 8-bit, single-scan JPEG only; no concatenated/progressive images or
  unchecked dimension changes. No bytes are written to disk.
- `POST /v1/devices/webcam/release`: same credentials, empty object. A stale
  lease cannot release another owner's camera. No force-release via generic CLI.
- `/v1/routes` and inventory show public ownership/mode/stream state only. Pending
  leases expire after 5 s; streaming leases expire 3 s after the last completed
  network frame write. Disconnect/error closes capture and revokes the lease.

## Implementation and validation

| Path | Responsibility |
| --- | --- |
| `hub/webcam.py` | sysfs discovery, stable selection, single-owner lease lifecycle |
| `hub/audio.py` | explicit USB microphone pin, stable ALSA target and pre-forward identity check |
| `hub/v4l2_capture.py` | UVC MMAP capture; no encoding or audio |
| `hub/server.py` | authenticated stream and claim integration |
| `proto/mjpeg.py` | mode bounds, framing and JPEG validation |
| `client/common/camera.py` | credential-safe HTTP control/stream client |
| `client/linux/camera.py` | dedicated loopback, decoder, bounded cleanup/status |
| `client/webcam_sink.py`, `client/ui.py` | explicit CLI/GUI activation |
| `tools/check_camera_abi.py` | offline struct/offset/ioctl proof against Linux headers |

```sh
python -m unittest tests.common.test_camera tests.hub.test_webcam tests.hub.test_audio_pin tests.linux.test_camera -v
python tools/check_camera_abi.py
python -m unittest discover -s tests -t .
```

Tests use fabricated sysfs, loopback/capture mocks, loopback-address HTTP with a
fake capture source, and FFmpeg-generated solid-color frames. No camera or
virtual video device is opened, no module is installed/loaded, and no live hub
is contacted. CI installs only userspace FFmpeg/compiler prerequisites for these
tests. V4L2 loopback safety-control semantics were reviewed against upstream
v4l2loopback 0.15.2/0.15.4 documentation/source; actual installed-kernel behavior still needs
acceptance. Windows common tests and import/build gates must pass before shipping
shared changes; a Windows camera backend is a separate future implementation.

## Rollback / reinstall

Stop the camera owner first. Leave other PortClaim routes/services running when
only disabling webcam use. Disabling the hub feature persistently requires an
approved env change/restart **with the same route preservation steps**. Reverting
code requires restoring the matched reviewed module set, not deleting imports
out from under a running service. Receiver source symlink deployments pick up
checkout changes on their next restart—do not switch branches casually.

Remove only a separately created, unused PortClaim virtual node after explicit
approval; never unload a module serving other loopbacks. Keep the packages if
other applications use them. Do not reset desktop camera/audio defaults.

For a fresh Omarchy install, obtain a **reviewed revision containing these files**
(the default branch may lag a review branch). Recreate scoped module/device
access and the private env, then run the same activation checks. The receiver
installer and source artifact include camera code, but neither bundles FFmpeg,
a kernel module or private pairing settings. Keep credentials in a secure
separate backup, not Git, and do not restore the old venv or source symlinks.
