# Storage — read-only receiver preview

This is a file-level inspection/import module, **not USB-over-IP, a block-device
export, a general-purpose network share, or a backup system**. It does not mount,
repair, format, index, execute, delete or modify anything on the hub's volume.

The first version provides explicit volume selection, paged directory browsing,
metadata, a bounded UTF-8 text preview, and cancellable imports to a new local
file. An optional, separately authorized selected-volume **unmount** control is
available; it does not grant privileged file browsing or mounting.
Video/audio/photos can be downloaded for inspection in a locally chosen
application. **Embedded media decoding, seekable remote playback, resume,
exports/uploads, overwrite, rename, and delete are not implemented.** No files
or applications open automatically. Hiding the receiver is not cancellation;
use Cancel or close the Storage window. Quit also requests cancellation.

## Transport and platform boundaries

Storage does **not** extend the hub's plain-HTTP control API. File bytes, filenames
and configuration may be sensitive: an independently configured OpenSSH key and
verified host identity provide encrypted transport. The existing HID/audio UDP,
camera HTTP leases, route registry, condenser choice and receiver lifecycle are
unchanged. No new listener/port is added and no hub service restart is required
merely to install the inert helper.

- `hub/storage.py`: standalone unprivileged Linux reader, one bounded request per SSH session.
- `hub/storage_unmount.py`: optional root-only unmount endpoint on a separate key.
- `deploy/storage-unmount-command`: fixed, environment-clearing root entry point.
- `proto/storage_wire.py`: driver-free bounds and JSON/binary framing.
- `client/common/storage.py`: common receiver transport, validation, cancellation
  and checksum-verified no-overwrite local import.
- `client/storage_ui.py`: explicitly opened Tk Storage window, no startup I/O.
- `deploy/storage.json.example`: placeholder-only administrator policy.

Linux and Windows receivers share this code and need an installed `ssh` client.
Common pipe tests do not certify the Windows OpenSSH/frozen GUI integration.
The hub implementation initially supports **USB ext4 only**. NTFS, exFAT, SD,
network filesystems, encrypted-volume unlocking, and mount provisioning need
separate design/acceptance; they are not silently treated as ordinary storage.

## Operator preparation — separate approval required

These are prerequisites, not actions performed by the UI or receiver installer.
Do not run the broad `deploy/install.sh` on an existing configured hub to enable
this feature: it is an appliance installer and can rewrite private config and
interrupt routes. Use a reviewed targeted deployment instead.

1. Identify the intended physical USB volume, filesystem UUID and mount state.
   Keep actual UUIDs/selectors/configuration private. A UUID is a selector, not
   cryptographic device authentication. Recheck identity after reconnects.
2. Review the filesystem's condition. A `needs_recovery` flag is significant even
   if another status field says `clean`. **Do not clear it, run repair, or perform
   journal replay as part of read-only inspection.** A future recovery procedure
   needs separate approval and appropriate protection of the existing data.
3. Provision an explicit dedicated mountpoint with `ro,nodev,nosuid,noexec`.
   For unrecovered ext4 inspection also use `noload` (reported as `norecovery`).
   A plain ext4 `ro` mount can replay the journal; it is not a no-writes guarantee.
   With replay suppressed the view can be incomplete or inconsistent. No
   persistent `/etc/fstab`, autofs, udev automount or broad ACL/group change is
   part of this module. The helper must see the mount in its own mount namespace.
4. Install `hub/storage.py` and `proto/storage_wire.py` as administrator-owned,
   non-writable files in `/usr/local/lib/usb-loom/`. With Python isolated mode,
   the helper explicitly imports only its installed protocol directory and the
   standard library. Copying source alone does not enable access.
5. Provision a **dedicated unprivileged SSH account/key** able to read only the
   approved data and administrator policy. Do not reuse a root/admin alias/key,
   grant raw block-device access, change existing file ownership recursively, or
   weaken host-key checking. The helper refuses to run as root. Use a fixed forced
   command and SSH key restrictions,
   for example the authorized-key options (public key omitted):

   ```text
   restrict,command="/usr/bin/python3 -I /usr/local/lib/usb-loom/storage.py"
   ```

   The server-side forced command is essential: client options alone do not
   restrict a key from being used by another SSH client. Verify no interactive
   shell, PTY, forwarding, environment/code injection or alternative SFTP command
   is available to that key. Configure account/concurrency limits separately;
   per-request bounds do not limit the number of independent SSH connections.
6. Create `/etc/portclaim-storage.json` from the example. It must be a root-owned
   regular file, not group/world writable or world readable; make it readable only by the dedicated
   helper account/group as needed. The fixed helper does not accept a remote
   config/root-path override. Configure at most eight volumes; each has a stable
   UI `id`, display `label`, exact dedicated mountpoint `root`, filesystem `uuid`,
   optional conservative `caveat`, and optional boolean `allow_unmount` (default
   false). Enabling that flag is not sufficient to provision unmount authority.
   Do not publish a filled policy.
7. Configure an explicit receiver SSH alias with its dedicated key and separately
   verified known-host entry. Set `USB_LOOM_STORAGE_SSH` privately to that alias,
   or enter it in Storage. It is independent of `USB_LOOM_HUB`, control tokens and
   any administrative `USB_LOOM_SSH_TARGET`. There is no automatic alias fallback.
8. Validate access with synthetic data first, then perform the approved real-volume
   pilot. Receiver source changes require an approved receiver activation; never
   interrupt input/dictation or assume source packaging means live deployment.

The helper requires a dedicated ext4 mount of its filesystem root, read-only both
at VFS and superblock level. A read-only bind into an otherwise writable filesystem
is rejected. Its actual mount ID/device must match the opened root descriptor and
configured UUID. Internal SATA/system storage and non-USB volumes are rejected.
A mount lacking the required protection flags is unavailable, not auto-fixed.

## Optional unmount control — separate privileged approval

Unmount is a separate operation, not a new permission for the read key. Install
`hub/storage_unmount.py` and `deploy/storage-unmount-command` under
`/usr/local/lib/usb-loom/`, root-owned and not writable by the SSH reader or any
other unprivileged user. The code/protocol directories, ancestors and any imported
`__pycache__` directory/bytecode files must likewise be root-owned and not writable
by unprivileged users (no symlink redirects). Python `-I` does not make application
code or bytecode caches trustworthy; `-B` alone would not disable cache reads.
Production imports the protocol only from its installed directory, never a sibling
`/usr/local/lib/proto` fallback. Keep the existing reader account/key unprivileged.

Provision a **different key**, exclusively restricted server-side to the fixed
root command, preferably also restricted to the receiver's source address:

```text
restrict,command="/usr/local/lib/usb-loom/storage-unmount-command"
```

This entry is for a dedicated unmount-only key, never an administrative key. The
wrapper ignores `SSH_ORIGINAL_COMMAND`/arguments, clears the environment, invokes
Python isolated mode and bounds address space/open descriptors. No sudo grant,
new network listener or permanent root daemon is required. Preserve existing
administrative/cluster authorized keys and SSH configuration; use a separate
already-supported authorized-key file where appropriate. Verify the effective
SSHD restrictions and actual denied shell, SFTP, PTY, forwarding and environment
injection paths. Source copying alone must not enable access.

The hub control has these additional prerequisites and constraints:

- Explicit `allow_unmount: true` for the selected policy volume. The target must
  be **exactly `/run/portclaim-storage/<volume-id>`**, not a client-supplied path.
  Inventory does not advertise unmount capability for a different root.
- Host mount namespace only. `/run/portclaim-storage` and all ancestors must be
  real root-owned directories with no group/other write permission.
- Root-owned regular `/run/portclaim-storage/session.lock`, no symlink/hardlink or
  world permissions. The reader's forced wrapper **must hold an exclusive flock
  on this same inode for its entire request**, e.g. `flock --no-fork --nonblock
  /run/portclaim-storage/session.lock ... python3 -I .../storage.py`. The control
  acquires that lock itself, non-blocking; it must not be wrapped in another
  independent acquisition of the lock. This rejects concurrent reader requests.
  Never replace/delete a live lock file to clear a busy condition.
- Normal existing read-only USB ext4 identity checks still apply: UUID, mount
  generation, protected flags and actual block/sysfs identity. Stacked targets,
  nested mounts and other host-namespace views of the same filesystem are refused.
- Only Linux `umount2` with `UMOUNT_NOFOLLOW` is called. **No force, lazy detach,
  external mount helpers, repair, remount, disk power-off or alternate operation.**
  Kernel `EBUSY` is a refusal, not permission to kill users of the filesystem.
- Our metadata descriptor closes before the syscall so it cannot itself keep the
  mount busy; topology is rechecked before and after. Administrative mount changes
  must use the same lock. Userspace generation/path checks are not atomic against
  an unrelated concurrent root remount. Traditional mount-ID reuse also remains
  a theoretical generation limitation; selectors are not cryptographic hardware
  authentication. A root administrator remains a trusted operator.
- Success means the selected mount/filesystem view is absent from the **hub host
  namespace**. It does not certify all container namespaces, every partition on a
  drive, safe physical removal in all circumstances, or disk power-off.

Configure `USB_LOOM_STORAGE_UNMOUNT_SSH` to a **separate** hardened SSH alias/key
for the same hub, with verified host identity. No default/admin alias is inferred.
The UI pairs it only with the environment-configured `USB_LOOM_STORAGE_SSH` read
alias; editing the read alias disables that pairing until it matches again and
is refreshed. With no endpoint or no policy capability, unmount stays disabled.

Test success/busy/stale paths with mocked syscalls first. On a live volume, use
malformed/stale requests to verify rejection without unmounting it. A real unmount
acceptance test requires an explicit user action or separate approval; installing
the button does not authorize taking a currently mounted volume offline.

## User flow

1. Open **Storage (read-only)** in the receiver.
2. Enter the dedicated SSH alias if not configured; explicitly refresh volumes.
3. Explicitly select an available volume and browse it. No first-volume selection,
   auto-mount, startup scan, Connect claim or reconnect recovery occurs.
4. Read its warning. The module does **not** claim filesystem consistency. When
   journal replay is suppressed it displays the associated incomplete-view risk;
   configured caveats remain visible even when the volume is unavailable.
5. Browse a folder, move up or request another page. Symlinks/special files are
   listed as such but cannot be followed/opened. Unsupported filenames are omitted;
   this is a bounded browser, not a comprehensive filesystem audit.
6. Select a regular file. Text Preview reads at most 64 KiB of UTF-8; binary/control
   data is rejected and not decoded by the hub. Preview does not run scripts,
   render HTML or interpret instructions in files.
7. To import, confirm a **new** local destination in the Save dialog. It suggests a
   safe single basename from the highlighted file (not its remote directories),
   sanitizing path separators, device/drive/stream names, controls and overlong
   names. The suggestion never starts a transfer or permits overwrite. The local
   Downloads directory is a convenience default only when present; choose another
   destination if desired. All downloaded bytes remain private
   to the operation until size, revision, SHA-256 and SSH completion are verified.
   The temporary file is created mode 0600 on POSIX (Windows uses the destination
   directory's ACLs). Same-directory hard-link publication prevents races from
   overwriting an existing file; unsupported destination filesystems fail safely.
   FAT/exFAT destinations without hard links are therefore not supported yet.
8. Cancel or close the window to stop an in-flight operation. Normal failure/cancel
   removes its temporary partial copy. Process/host crashes can leave a private
   `.portclaim-import-*` partial; it is never presented as a completed import.
   No broad startup sweep deletes unknown files. Completed imports are user files,
   not automatically deleted on window close.

9. If provisioned, select the mounted volume and use **Unmount selected volume…**,
   then confirm its label. Finish/cancel downloads first; unmount is disabled while
   the local worker or a destination/confirmation dialog is active. Other reader
   sessions and externally busy mounts are refused by the hub. Browser/generation
   state is invalidated after an attempt; Refresh is explicit. Only a validated
   success response says **Unmounted**. Cancelling/closing or losing SSH cannot
   undo a dispatched unmount and leaves its outcome unknown until checked. No
   automatic retry or remount occurs; mounts remain externally provisioned.

Downloads deliberately do not open a viewer. Choose an appropriate trusted local
application yourself; downloaded executables/documents remain untrusted input.
Local available space is not reserved in advance. Disk-full errors remove the
operation's partial copy; power-loss durability is not promised by publication.

## Protocol and bounds

A single newline-terminated JSON request (maximum 4 KiB) goes to the fixed SSH
reader command. `version: 1`, `op: volumes|list|read`; no write operation exists.
The separate unmount command accepts only `{version, op: "unmount", volume,
generation}` and returns a bounded selection-bound status. It cannot read files,
accept paths/options, or serve inventory. Unknown fields/operations fail closed.
Paths
are lists of at most 32 validated relative components, not shell commands/URLs.
`..`, separators, non-printable names and unsupported encodings are rejected.
Each component is acquired relative to a pinned directory with metadata-only
`O_PATH|O_NOFOLLOW`; all traversed descriptors must remain on the same mount,
including bind mounts of the same device. Only after validating the pinned inode's
mount and regular-file/directory type is it reopened for reading via its descriptor.
Nested mounted entries are omitted from listings. Device open callbacks are never
invoked merely to discover that an out-of-mount leaf should have been rejected.
Only regular files can supply bytes.

Inventory publishes UI IDs/labels, availability, an optional boolean unmount
capability (absence means unsupported), conservative warnings and an
opaque generation hash. It does not publish host mount paths, UUIDs or policy.
The generation binds mount ID, filesystem/root identity, USB connection generation
and host boot. Missing/replaced/unmounted volumes require a refresh and explicit
selection. The helper rechecks identity at read start/end and at most 250 ms apart
during progressing transfers, and file revision before each chunk; existing
open descriptors do not retarget to a new drive after hotplug.

JSON responses are bounded to 512 KiB. Listings return at most 200 entries/page
and examine at most 10,000 entries plus one lookahead, without recursive scanning.
Pagination requires the unchanged directory revision. Ordering is filesystem
iteration order, not a globally sorted snapshot. A file read emits a JSON header,
exactly the declared binary byte count, then a JSON SHA-256 trailer. Size/revision,
framing, checksum, complete process exit and trailing-output checks all precede
local publication. SSH authenticates/encrypts the peer; the checksum detects
transfer inconsistency, not a malicious authorized hub supplying false content.

Read bounds: 64 KiB chunks, 64 KiB text preview, 128 GiB/file, four-hour transfer
ceiling. Helper initial request timeout is ten seconds; no-output progress times
out after thirty seconds. Client pipe buffering is bounded and idle reads time
out after twenty seconds; SSH has independent connection/keepalive bounds. User
cancellation terminates only its owned SSH process. An uninterruptible kernel I/O
stall can outlive userspace deadlines; it is not reported as successful cleanup.

No transcoding runs on the hub. Physical USB read speed is not encrypted network
throughput or playback acceptance. Measure representative real files and camera
coexistence later, without letting bulk transfers starve input/audio. Production
concurrency/resource limits and application playback remain deployment gates.

## Validation and rollback

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.common.test_storage -v
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.common.test_storage_ui -v
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.hub.test_storage -v
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.hub.test_storage_unmount -v
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -t .
```

Tests use synthetic files, mocked mount identity and actual local child/pipe
lifecycles with SSH replaced. They do not mount/unmount volumes, connect to a hub, create
keys, change ACLs or access media. They cover traversal/symlinks/special files,
mount/UUID/generation changes, readonly policy, paging, revisions, bounded text,
checksum/truncation/cancel cleanup, no-clobber publication, safe filename suggestions,
confirmation/lifecycle, unmount authority, real synthetic flock contention and
mocked syscall/verification failures.
Linux packaging must include the receiver modules and protocol without private
policy/keys or the hub's privileged deployment assets. Windows frozen build,
rendered GUI, real SSH restrictions, mount visibility and real-volume acceptance
must still be checked on their actual surfaces.

For a deployed pilot: cancel active imports, close Storage, revoke only
its dedicated read/unmount keys/access and unmount only the approved idle storage
mount when authorized. Removing unmount support does not require an unmount: set
`allow_unmount` false, remove the dedicated receiver alias/env setting and revoke
only its exact key entry; keep browsing, existing keys and later edits intact. Keep
existing input/audio/camera routes and SSH administrative access untouched. No
hub reboot or PortClaim route-registry reset is intrinsic to this module.
