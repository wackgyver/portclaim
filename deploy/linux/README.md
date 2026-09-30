# Linux receiver packaging

The hub is separate. This installer never deploys/restarts it or changes claims.
It also never uses sudo, edits input groups/udev, sets an audio default, remaps a
key, starts/enables a service, or applies a compositor profile.

## Prerequisites

- Linux desktop user session with a working systemd user manager.
- Python 3.10+ with Tk, venv, and pip support.
- PipeWire/Pulse clients: `pactl` and `paplay`.
- Existing, appropriately scoped write access to `/dev/uinput`. Check session
  ACLs first. The installer refuses missing access rather than granting broad
  membership in the `input` group or changing permissions itself.
- For the optional tray: GTK3, AppIndicator/Ayatana AppIndicator typelibs, Cairo,
  and build dependencies compatible with the pinned PyGObject version. Recent
  Arch/Omarchy is the integration target; do not assume every distribution has
  these library versions. `--without-tray` omits these dependencies and keeps
  controls visible instead.

Review/install missing distribution packages yourself. This package does not
perform a system upgrade or install third-party drivers. Python dependencies
are pinned separately from Windows and installed in a private virtual environment.

## First installation

From the checkout or unpacked Linux source artifact:

```sh
./deploy/install-receiver.sh --dry-run
./deploy/install-receiver.sh
# Fill ~/.config/portclaim/usb-loom.env privately; use your real HUB/SELF/token.
portclaim
```

`XDG_CONFIG_HOME` and `XDG_DATA_HOME` are honored. The env file is mode 0600;
existing contents are preserved. The service starts hidden when a usable tray
exists; a failed/missing tray leaves controls visible. `portclaim` shows the
single running instance, while `portclaim hide`, `stop`, and `status` keep their
existing meanings. Starting the service itself does not show a workspace window.
No login autostart or restart loop is enabled by default.

The owned temporary microphone is created by ExecStartPre and cleaned by
ExecStopPost. Ownership includes a random generation token so reused module IDs
cannot cause an unrelated sink to be unloaded. Existing `portclaim_mic` sinks are
borrowed, not owned. Recording applications should select `portclaim_mic.monitor`
(or use app-specific routing), not change the desktop default source.

## Optional webcam (video-only preview)

Camera code is included but remains inactive until explicit **Webcam → Start &
claim camera** (or `client/webcam_sink.py --start`). Bulk Connect and service
startup do not activate it. Hiding an explicitly started camera keeps it live;
Stop Camera or Quit stops it. It does not select a webcam microphone.

See [webcam setup and acceptance](../../docs/webcam.md) and the reference-only
[Arch native package list](packages-camera.arch.txt). FFmpeg and a dedicated
exclusive-caps `v4l2loopback` output are additional optional prerequisites. This
installer **never** installs/loads a kernel module, grants device permissions,
claims a camera or modifies the hub. `USB_LOOM_CAMERA_OUTPUT` has no default and
must identify the separately provisioned `PortClaim Camera` node. Hub enablement,
route-preserving deployment and first capture require separate approval.

## Optional read-only Storage

The explicit **Storage (read-only)** window uses a dedicated restricted OpenSSH
alias (`USB_LOOM_STORAGE_SSH`), never the hub administrative alias/control token.
It browses separately approved read-only USB ext4 mounts, previews bounded text,
and copies selected files to new local destinations. No exports, automatic
mounting/connection, SSH keys/accounts or policy are installed. OpenSSH is an
optional native prerequisite; no additional Python dependency is needed.
Save suggests a safe basename, but still requires local destination confirmation.
Optional **Unmount selected volume…** requires a separately provisioned unmount-only
key (`USB_LOOM_STORAGE_UNMOUNT_SSH`) paired with the configured read alias and an
explicit per-volume hub policy. It refuses active transfers/busy mounts, never
forces or lazily detaches, and never remounts automatically. The receiver installer
does not provision this privileged control.

See [storage setup, limits and acceptance](../../docs/storage.md). Hub helper
provisioning and receiver activation require their own approved deployment.

## Layout and upgrades

```text
~/.local/lib/portclaim/
  install.json              Managed-file hashes and current release
  current -> releases/…     Source/helper snapshot, not a link to a moving checkout
  releases/…/src/            Receiver + protocol, without Windows backend or tests
  environments/…/           Isolated dependencies, reused when their pins are unchanged
  backups/…/                Prior managed files/manifest for rollback
~/.local/bin/portclaim
~/.config/systemd/user/portclaim.service
~/.local/share/applications/portclaim.desktop
```

Stop the receiver explicitly before upgrading; check that dictation is idle.
Then use `./deploy/install-receiver.sh --upgrade`. The installer refuses active
receivers, locally modified managed files, masked/unknown service state, and
unrecognized installations. A prefix lock prevents simultaneous installations.
New source/dependencies are prepared before live-facing files are switched.
Dependency failures do not touch the unit/launcher; activation-file failures
restore the previous files/link. A failed daemon reload is an error, not success.

`python tools/check_linux_unit.py` validates the rendered unit offline using
isolated user paths with spaces and percent signs; it never reloads or starts a
service. Unit verification and temporary-home transaction tests do not replace
clean-host desktop, audio and real input acceptance.

Previous source snapshots and dependency environments are retained for rollback.
They do not run in the background. Review obsolete versions before removing them;
the installer does not sweep directories or silently delete backups.

## Migrating an older/manual installation

This is intentionally **not automatic**. The older bootstrap granted `input`
membership, enabled `portclaim-virtmic.service`, changed the default source, and
used an automatic restart loop. Other manual installations have a safer custom
unit, source symlinks, or launcher that must not be overwritten blindly.

1. Back up the existing source, unit, launchers/helpers, and private configuration.
2. Verify current audio defaults and claims; do not release/reclaim devices.
3. In an approved interruption window, stop only the receiver. If an old separate
   mic service exists, inspect its ownership before disabling/removing that service.
4. Reconcile/archive the old dedicated prefix, launcher, unit, and desktop entry.
   Preserve the env and trackpad settings. Do not reset shared audio/input config.
5. Run the new installer and explicitly start the receiver. Validate tray,
   touchpad, microphone, gamepad, and unchanged defaults. Leave autostart opt-in.

## Rollback

Stop the receiver before restoring files. For an upgrade, `backups/<id>/` contains
`install.json`, `paths.json`, and numbered managed-file backups. Restore only those
recorded files after checking for subsequent user edits, and restore `current` to
the relative release recorded in that manifest. The old unit references its
retained dependency environment. Run `systemctl --user daemon-reload`, then start
explicitly and verify. Never restore a permissive mode on the private env file.

For a first-install removal, stop the receiver, review/remove only the three
managed files listed by `install.json` and the dedicated prefix, then reload the
user manager. Preserve user config unless explicitly discarding it. No system
packages, input permissions, or unrelated audio services need to be removed.
