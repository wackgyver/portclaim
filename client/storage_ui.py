"""Read-only SSH browser with separately authorized, explicit volume unmount.

Importing/constructing performs no storage I/O. Only the worker touches the
client; only the Tk thread touches widgets. Closing never joins the worker and
cannot undo an unmount request that may already have reached the endpoint.
"""
from __future__ import annotations

import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import unicodedata

BG = "#181a1e"
FG = "#e6e6e6"
QUEUE_SIZE = 8
POLL_MS = 75
PREVIEW_CHARS = 65536
FILENAME_BYTES = 240
UNMOUNT_UNKNOWN = (
    "Unmount outcome unknown. Closing or losing SSH cannot undo a request already sent. "
    "Refresh volumes before any further action."
)


def _client_for_alias(alias, unmount_alias=None):
    # Keep even the client import behind an explicit Refresh action.
    from client.common.storage import StorageClient
    return StorageClient(alias, unmount_alias=unmount_alias)


def _safe_filename(name):
    """A portable basename suggestion, never a destination or a remote path.

    Preserve ordinary names exactly. Strip invisible/control formatting, replace
    both platforms' path/stream syntax, and keep a short extension when trimming.
    The result is bounded in UTF-8 bytes, not merely Unicode code points.
    """
    if not isinstance(name, str):
        return "download"
    forbidden = '<>:"/\\|?*'
    printable = "".join(c for c in name if c.isprintable()
                        and not unicodedata.category(c).startswith("C"))
    if not any(c not in forbidden + " ." for c in printable):
        return "download"
    cleaned = "".join("_" if c in forbidden else c for c in printable).rstrip(" .")
    # Tk file dialogs can interpret a leading tilde as a home-directory path.
    if cleaned.startswith("~"):
        cleaned = "_" + cleaned
    devices = {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"}
    devices.update(prefix + suffix for prefix in ("COM", "LPT") for suffix in "123456789¹²³")
    if cleaned.split(".", 1)[0].rstrip(" ").upper() in devices:
        cleaned = "_" + cleaned
    if len(cleaned.encode("utf-8")) <= FILENAME_BYTES:
        return cleaned or "download"
    stem, dot, extension = cleaned.rpartition(".")
    suffix = dot + extension if stem and 0 < len(extension.encode("utf-8")) <= 32 else ""
    if suffix:
        cleaned = cleaned[:-len(suffix)]
    budget = FILENAME_BYTES - len(suffix.encode("utf-8"))
    cleaned = cleaned.encode("utf-8")[:budget].decode("utf-8", "ignore").rstrip(" .")
    result = (cleaned or "download") + suffix
    # Trimming may expose a device name: e.g. 'NUL' + spaces + a late 'x'.
    # Recheck the final suggestion, not only the original cleaned basename.
    if result.split(".", 1)[0].rstrip(" ").upper() in devices:
        result = "_" + result
    return result


def _downloads_directory():
    # Called only for the first explicit Save dialog. Never create a directory.
    try:
        directory = Path.home() / "Downloads"
        return str(directory) if directory.is_dir() else None
    except (OSError, RuntimeError):
        return None


def _display(value, limit=2048):
    """Bound display data and strip terminal/control and bidi formatting codes."""
    text = str(value)
    clean = "".join(c if c in "\n\t" or not unicodedata.category(c).startswith("C")
                    else "\ufffd" for c in text[:limit])
    return clean + ("\n[display truncated]" if len(text) > limit else "")


class StorageWindow:
    def __init__(self, root):
        self.window = tk.Toplevel(root)
        self.window.title("PortClaim — Storage (read-only)")
        self.window.configure(bg=BG)
        self.window.geometry("920x820")
        self.window.minsize(720, 640)
        self._closed = False
        self._busy = False
        self._modal = False
        self._operation = None
        self._save_dialog_used = False
        self._thread = None
        self._cancel = threading.Event()
        self._events = queue.Queue(maxsize=QUEUE_SIZE)
        self._completion = None
        self._after_id = None
        self._client = None
        self._inventory_alias = None
        self._inventory_unmount_alias = None
        self._configured_read_alias = os.environ.get("USB_LOOM_STORAGE_SSH", "").strip()
        self._unmount_alias = os.environ.get("USB_LOOM_STORAGE_UNMOUNT_SSH", "").strip()
        self._volumes = {}
        self._volume_selection = ()
        self._volume = None
        self._entries = {}
        self._path = []
        self._revision = None
        self._next_offset = None
        self._on_success = None
        self.alias_var = tk.StringVar(master=self.window,
                                      value=os.environ.get("USB_LOOM_STORAGE_SSH", ""))
        self.status_var = tk.StringVar(master=self.window, value="Enter an SSH alias, then Refresh volumes.")
        self.path_var = tk.StringVar(master=self.window, value="No volume opened.")
        self.warning_var = tk.StringVar(master=self.window, value="Select a volume to inspect its warning.")
        self.unmount_hint_var = tk.StringVar(master=self.window)
        self._build()
        self._alias_trace = self.alias_var.trace_add("write", lambda *_args: self._controls())
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._controls()
        self._after_id = self.window.after(POLL_MS, self._poll)

    def _build(self):
        host = ttk.Frame(self.window, padding=12)
        host.pack(fill="both", expand=True)
        # Reserve the phase/status line before the resizable browser content.
        ttk.Label(host, textvariable=self.status_var, wraplength=850).pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Label(host, text="Storage — read-only", style="Head.TLabel").pack(anchor="w")
        ttk.Label(host, text="Encrypted SSH only. No hub writes, exports, or mounts. No automatic media opening.\n"
                  "Save copies only to a NEW local file; existing destinations are refused.",
                  wraplength=850).pack(anchor="w", pady=6)
        bar = ttk.Frame(host)
        bar.pack(fill="x")
        ttk.Label(bar, text="SSH alias").pack(side="left")
        self.alias_entry = ttk.Entry(bar, textvariable=self.alias_var, width=36)
        self.alias_entry.pack(side="left", padx=8, fill="x", expand=True)
        self.refresh_btn = ttk.Button(bar, text="Refresh volumes", command=self._refresh)
        self.refresh_btn.pack(side="left")
        ttk.Label(host, text="Alias changes apply only on Refresh. Select a volume, then Open volume.").pack(anchor="w")
        self.volumes_tree = self._tree(host, ("label", "available", "warning"),
                                       ("Volume", "Available", "Warning (select for full text)"), height=4)
        self.volumes_tree.bind("<<TreeviewSelect>>", self._select_volume)
        volume_actions = ttk.Frame(host)
        volume_actions.pack(fill="x", pady=4)
        self.open_volume_btn = ttk.Button(volume_actions, text="Open volume", command=self._open_volume)
        self.open_volume_btn.pack(side="left")
        self.unmount_btn = ttk.Button(volume_actions, text="Unmount selected volume…", command=self._unmount)
        self.unmount_btn.pack(side="left", padx=8)
        ttk.Label(host, textvariable=self.unmount_hint_var, wraplength=850).pack(anchor="w", fill="x")
        # This warning stays visible during all folder/preview/download operations.
        ttk.Label(host, textvariable=self.warning_var, wraplength=850).pack(anchor="w", fill="x", pady=4)
        nav = ttk.Frame(host)
        nav.pack(fill="x", pady=4)
        self.up_btn = ttk.Button(nav, text="Up", command=self._up)
        self.up_btn.pack(side="left")
        self.next_btn = ttk.Button(nav, text="Next page", command=self._next_page)
        self.next_btn.pack(side="left", padx=6)
        ttk.Label(nav, textvariable=self.path_var, wraplength=650).pack(side="left")
        self.entries_tree = self._tree(host, ("name", "type", "size"), ("Name", "Type", "Bytes"), height=8)
        self.entries_tree.bind("<<TreeviewSelect>>", lambda _event: self._controls())
        self.entries_tree.bind("<Double-1>", self._open_directory)
        actions = ttk.Frame(host)
        actions.pack(fill="x", pady=6)
        self.open_dir_btn = ttk.Button(actions, text="Open directory", command=self._open_directory)
        self.open_dir_btn.pack(side="left")
        self.preview_btn = ttk.Button(actions, text="Preview text (bounded)", command=self._preview)
        self.preview_btn.pack(side="left", padx=6)
        self.save_btn = ttk.Button(actions, text="Save / download…", command=self._save)
        self.save_btn.pack(side="left")
        self.cancel_btn = ttk.Button(actions, text="Cancel", command=self.cancel)
        self.cancel_btn.pack(side="right")
        self.preview_text = tk.Text(host, height=8, wrap="word", state="disabled", bg=BG, fg=FG)
        self.preview_text.pack(fill="both", expand=True)

    def _tree(self, parent, columns, headings, height):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=4)
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse", height=height)
        for column, title in zip(columns, headings):
            tree.heading(column, text=title)
            tree.column(column, width=130 if column in ("available", "type", "size") else 280)
        vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return tree

    def _selected(self, tree, mapping):
        selected = tree.selection()
        return mapping.get(selected[0]) if selected else None

    def _controls(self):
        if self._closed:
            return
        idle = not self._busy and not self._modal
        volume = self._selected(self.volumes_tree, self._volumes)
        entry = self._selected(self.entries_tree, self._entries)
        browsable = bool(self._client and self._volume)
        unmount_problem = self._unmount_problem()
        self.unmount_hint_var.set(unmount_problem or
            "Unmount uses the separately configured endpoint; confirmation is required. No force or remount.")
        for widget, enabled in (
            (self.refresh_btn, idle), (self.alias_entry, idle),
            (self.open_volume_btn, idle and volume and volume.get("available") is True
             and isinstance(volume.get("generation"), str)),
            (self.up_btn, idle and browsable and self._path),
            (self.next_btn, idle and browsable and self._next_offset is not None),
            (self.open_dir_btn, idle and browsable and entry and entry["type"] == "directory"),
            (self.preview_btn, idle and browsable and entry and entry["type"] == "file"),
            (self.save_btn, idle and browsable and entry and entry["type"] == "file"),
            (self.unmount_btn, idle and unmount_problem is None),
            (self.cancel_btn, self._busy and not self._modal and self._operation != "unmount"
             and not self._cancel.is_set()),
        ):
            widget.configure(state="normal" if enabled else "disabled")

    def _paired_unmount_alias(self, alias):
        if (self._configured_read_alias and alias == self._configured_read_alias
                and self._unmount_alias and self._unmount_alias != alias):
            return self._unmount_alias
        return None

    def _unmount_problem(self):
        alias = self.alias_var.get().strip()
        if not self._configured_read_alias or not self._unmount_alias:
            return "Unmount disabled: configure both USB_LOOM_STORAGE_SSH and USB_LOOM_STORAGE_UNMOUNT_SSH."
        if alias != self._configured_read_alias:
            return "Unmount disabled: the read alias differs from its configured endpoint pair."
        if self._unmount_alias == alias:
            return "Unmount disabled: a separate, dedicated unmount SSH alias is required."
        if (not self._client or self._inventory_alias != alias
                or self._inventory_unmount_alias != self._unmount_alias):
            return "Unmount disabled: Refresh volumes using the configured read alias first."
        volume = self._selected(self.volumes_tree, self._volumes)
        if not volume:
            return "Unmount disabled: explicitly select a mounted volume."
        if volume.get("available") is not True:
            return "Unmount disabled: selected volume is unavailable; this UI never mounts it."
        if volume.get("can_unmount") is not True:
            return "Unmount disabled: the selected volume does not advertise unmount support."
        if not isinstance(volume.get("generation"), str) or not volume["generation"]:
            return "Unmount disabled: missing volume generation; Refresh volumes."
        return None

    def _dialog(self, status, dialog, **options):
        """Guard against nested Tk events while a native modal dialog is open."""
        if self._closed or self._busy or self._modal:
            return False, None
        self._modal = True
        self.status_var.set(status)
        self._controls()
        try:
            return True, dialog(parent=self.window, **options)
        except Exception:
            return False, None  # No raw native-dialog/OS errors in the UI.
        finally:
            self._modal = False
            if not self._closed:
                self._controls()

    def _clear_preview(self, text=""):
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", text)
        self.preview_text.configure(state="disabled")

    def _clear_browser(self):
        self._volume = None
        self._entries = {}
        self._path = []
        self._revision = None
        self._next_offset = None
        self.entries_tree.delete(*self.entries_tree.get_children())
        self.path_var.set("No volume opened.")
        self._clear_preview()

    def _refresh(self):
        if self._closed or self._busy or self._modal:
            return
        alias = self.alias_var.get().strip()
        if not alias:
            self.status_var.set("Enter an explicit SSH alias; no default hub or root alias is used.")
            return
        self._client = None
        self._inventory_alias = None
        self._inventory_unmount_alias = None
        self._volumes = {}
        self._volume_selection = ()
        self.volumes_tree.delete(*self.volumes_tree.get_children())
        self._clear_browser()
        self.warning_var.set("No volume selected.")

        unmount_alias = self._paired_unmount_alias(alias)

        def fetch(cancel, progress):
            client = _client_for_alias(alias, unmount_alias=unmount_alias)
            return client, client.volumes(cancel=cancel)

        def show(result):
            self._client, volumes = result
            self._inventory_alias = alias
            self._inventory_unmount_alias = unmount_alias
            for index, volume in enumerate(volumes):
                iid = str(index)
                self._volumes[iid] = volume
                self.volumes_tree.insert("", "end", iid=iid, values=(
                    _display(volume["label"]), "Yes" if volume["available"] else "No",
                    _display(volume.get("warning") or "No warning reported.")))
            self.status_var.set("Select a volume explicitly, then Open volume. No volume is opened automatically.")
        self._start("Refreshing volumes", fetch, show)

    def _select_volume(self, _event=None):
        if self._closed:
            return
        selected = self.volumes_tree.selection()
        if selected == self._volume_selection:
            return
        if self._busy or self._modal:
            # Keep the highlight and warning pinned to the operation's volume.
            # Restoring selection queues another virtual event; equality above
            # prevents that event from clearing the browser or recursing.
            self.volumes_tree.selection_set(self._volume_selection)
            return
        self._volume_selection = selected
        self._clear_browser()
        volume = self._selected(self.volumes_tree, self._volumes)
        if volume:
            self.warning_var.set(_display(volume.get("warning") or "No warning reported.", 8192))
            if volume.get("available") is not True:
                self.status_var.set("Volume unavailable. Browsing is disabled; this UI never mounts it.")
        else:
            self.warning_var.set("No volume selected.")
        self._controls()

    def _open_volume(self):
        if self._closed or self._busy or self._modal:
            return
        volume = self._selected(self.volumes_tree, self._volumes)
        if not self._client or not volume or volume.get("available") is not True:
            return
        if not isinstance(volume.get("generation"), str):
            self.status_var.set("Volume generation missing; Refresh volumes before browsing.")
            return
        self._volume_selection = self.volumes_tree.selection()
        self._clear_browser()
        self._volume = dict(volume)
        self.warning_var.set(_display(volume.get("warning") or "No warning reported.", 8192))
        self._list([])

    def _list(self, path, offset=0, revision=None):
        if self._closed or self._busy or self._modal or not self._volume:
            return
        client, volume, path = self._client, dict(self._volume), list(path)

        def show(result):
            self._path = path
            self._revision = result["revision"]
            self._next_offset = result["next_offset"]
            self._entries = {}
            self.entries_tree.delete(*self.entries_tree.get_children())
            for index, entry in enumerate(result["entries"]):
                iid = str(index)
                self._entries[iid] = entry
                self.entries_tree.insert("", "end", iid=iid, values=(
                    _display(entry["name"]), entry["type"], entry.get("size", "")))
            self._clear_preview()
            self.path_var.set(_display(volume["label"] + " / " + " / ".join(path)))
            self.status_var.set("Page starting at %d.%s" % (offset,
                " Listing limited; not all entries may be shown." if result["limited"] else ""))
        self._start("Listing directory", lambda cancel, progress: client.list(
            volume["id"], volume["generation"], path, offset=offset, revision=revision, cancel=cancel), show)

    def _up(self):
        if self._path:
            self._list(self._path[:-1])

    def _next_page(self):
        if self._next_offset is not None:
            self._list(self._path, self._next_offset, self._revision)

    def _open_directory(self, _event=None):
        if self._closed or self._busy or self._modal:
            return
        entry = self._selected(self.entries_tree, self._entries)
        if entry and entry["type"] == "directory":
            self._list(self._path + [entry["name"]])

    def _file_request(self):
        if self._closed or self._busy or self._modal or not self._volume:
            return None
        entry = self._selected(self.entries_tree, self._entries)
        if not entry or entry["type"] != "file":
            return None
        return (self._volume["id"], self._volume["generation"],
                self._path + [entry["name"]], entry["revision"])

    def _preview(self):
        args = self._file_request()
        if args is None:
            return
        client = self._client

        def show(result):
            text = result["text"]
            self._clear_preview(_display(text, PREVIEW_CHARS))
            self.status_var.set("Text preview only (truncated)." if result["truncated"] or len(text) > PREVIEW_CHARS
                                else "Text preview only. Nothing executed or opened externally.")
        self._start("Previewing text", lambda cancel, progress: client.preview(*args, cancel=cancel), show)

    def _save(self):
        args = self._file_request()
        if args is None:
            return
        client, alias = self._client, self.alias_var.get()
        selected_volume = self.volumes_tree.selection()
        selected_entry = self.entries_tree.selection()
        options = {"title": "Save to a NEW local file (no overwrite)",
                   "initialfile": _safe_filename(args[2][-1])}
        if not self._save_dialog_used:
            initialdir = _downloads_directory()
            if initialdir:
                options["initialdir"] = initialdir
        self._save_dialog_used = True
        shown, destination = self._dialog(
            "Choose destination … download has not started.", filedialog.asksaveasfilename, **options)
        if self._closed:
            return
        if not shown:
            self.status_var.set("Could not choose destination. Download has not started.")
            return
        if not destination:
            self.status_var.set("Download cancelled. No download started.")
            return
        if (self._busy or self._client is not client or self.alias_var.get() != alias
                or self.volumes_tree.selection() != selected_volume
                or self.entries_tree.selection() != selected_entry or self._file_request() != args):
            self.status_var.set("Selection or SSH alias changed. Download has not started; select the file again.")
            return
        # Only the explicit native-dialog result is a destination. The suggestion
        # is never joined to any remote directory or used as a fallback path.
        self._start("Downloading local copy", lambda cancel, progress: client.download(
            *args, destination, cancel=cancel, progress=progress),
            lambda result: self.status_var.set("Saved local copy: %d bytes; SHA-256 %s" % (
                result["bytes"], _display(result["sha256"], 64))))

    def _unmount(self):
        if self._closed or self._busy or self._modal:
            return
        problem = self._unmount_problem()
        if problem:
            self.status_var.set(problem)
            return
        self._select_volume()
        volume = dict(self._selected(self.volumes_tree, self._volumes))
        selected = self.volumes_tree.selection()
        client, alias = self._client, self.alias_var.get()
        shown, confirmed = self._dialog(
            "Confirm selected-volume unmount … no request has been sent.", messagebox.askyesno,
            title="Unmount selected volume?", icon=messagebox.WARNING, default=messagebox.NO,
            message=("Unmount this selected volume?\n\n" + _display(volume["label"], 128)
                     + "\n\nNo force/lazy unmount or automatic remount. This does not power off the disk "
                       "or establish that other partitions are safe.\n\nOnce sent, closing this window "
                       "or losing SSH cannot undo or reliably cancel the request."))
        if self._closed:
            return
        if not shown:
            self.status_var.set("Could not confirm unmount. No request was sent.")
            return
        if confirmed is not True:
            self.status_var.set("Unmount not requested. No request was sent.")
            return
        if (self._busy or self._client is not client or self.alias_var.get() != alias
                or self.volumes_tree.selection() != selected or self._unmount_problem()
                or self._selected(self.volumes_tree, self._volumes) != volume):
            self.status_var.set("Selection or SSH alias changed; unmount not requested. Refresh volumes before retrying.")
            return
        # Invalidate before dispatch, even if transport or worker startup fails.
        # Preserve the selected volume's warning; nothing refreshes automatically.
        self._clear_browser()
        self._client = None
        self._inventory_alias = None
        self._inventory_unmount_alias = None
        self._volumes = {}
        self._volume_selection = ()
        self.volumes_tree.delete(*self.volumes_tree.get_children())

        def show(result):
            messages = {
                "unmounted": "Unmounted selected volume. This does not power off the disk or establish that "
                             "other partitions are safe. Refresh volumes to check current state.",
                "busy": "Unmount refused: volume is busy. Stop readers/transfers and Refresh volumes before retrying. "
                        "No force/lazy unmount is used.",
                "stale": "Unmount refused: stale volume selection/generation. Refresh volumes and select again.",
                "unavailable": "Unmount target unavailable. Refresh volumes to check its current state.",
                "denied": "Unmount denied by endpoint/policy. Check the dedicated endpoint configuration and Refresh volumes.",
                "failed": "Unmount failed; current mount state is unknown. Refresh volumes before any further action.",
            }
            if (not isinstance(result, dict) or result.get("volume") != volume["id"]
                    or result.get("generation") != volume["generation"]
                    or result.get("status") not in messages):
                raise ValueError("invalid unmount response")
            self.status_var.set(messages[result["status"]])

        self._start("Requesting selected-volume unmount", lambda cancel, progress: client.unmount(
            volume["id"], volume["generation"], cancel=cancel), show, operation="unmount")

    def _start(self, label, work, on_success, *, operation="read"):
        if self._closed or self._busy or self._modal:
            return False
        self._busy = True
        self._operation = operation
        self._cancel = threading.Event()
        self._on_success = on_success
        self._completion = None
        events, cancel = self._events, self._cancel
        self.status_var.set(label + ("… Cancel unavailable. Closing or losing SSH cannot undo this request."
                                     if operation == "unmount" else "…"))
        self._controls()

        def progress(done, total):
            if cancel.is_set():
                return
            try:
                events.put_nowait(("progress", (done, total)))
            except queue.Full:
                pass  # Lossy progress: never block an SSH reader on the GUI.

        def run():
            try:
                result = ("result", work(cancel, progress))
            except Exception:
                # Never expose exception strings: SSH stderr may include secrets,
                # paths, terminal controls, or connection/configuration details.
                result = ("error", None)
            # A terminal result must survive a queue full of progress messages.
            while True:
                try:
                    events.put_nowait(result)
                    break
                except queue.Full:
                    try:
                        events.get_nowait()
                    except queue.Empty:
                        pass

        self._thread = threading.Thread(target=run, name="storage-ui", daemon=True)
        try:
            self._thread.start()
        except RuntimeError:
            self._thread = None
            self._busy = False
            self._operation = None
            self._on_success = None
            self.status_var.set("Could not start unmount worker. No request was sent. Refresh volumes."
                                if operation == "unmount" else "Could not start storage worker.")
            self._controls()
            return False
        return True

    def _poll(self):
        self._after_id = None
        if self._closed:
            return
        for _ in range(QUEUE_SIZE):
            try:
                kind, value = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                if not self._cancel.is_set() and self._operation != "unmount":
                    done, total = value
                    self.status_var.set("Downloading local copy: %s / %s bytes" % (done, total))
            else:
                self._completion = (kind, value)
        # Do not allow a second worker until the first has actually exited.
        if self._completion is not None and self._thread is not None and not self._thread.is_alive():
            kind, value = self._completion
            self._completion = None
            self._thread = None
            self._busy = False
            callback, self._on_success = self._on_success, None
            operation, self._operation = self._operation, None
            if operation == "unmount" and (self._cancel.is_set() or kind == "error"):
                self.status_var.set(UNMOUNT_UNKNOWN)
            elif self._cancel.is_set():
                self.status_var.set("Cancelled. A local copy completed before cancellation may still exist.")
            elif kind == "error":
                self.status_var.set("Storage request failed. Check the SSH alias, availability, and selection; then Refresh volumes.")
            else:
                try:
                    callback(value)
                except Exception:
                    self.status_var.set(UNMOUNT_UNKNOWN if operation == "unmount" else
                                        "Invalid storage response. Refresh volumes before retrying.")
            self._controls()
        if not self._closed:
            self._after_id = self.window.after(POLL_MS, self._poll)

    def cancel(self):
        if self._closed or not self._busy or self._modal or self._operation == "unmount":
            return
        self._cancel.set()
        self.status_var.set("Cancelling… Waiting for the current SSH operation to stop.")
        self._controls()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._cancel.set()
        self._on_success = None
        try:
            self.alias_var.trace_remove("write", self._alias_trace)
        except tk.TclError:
            pass
        if self._after_id is not None:
            try:
                self.window.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        try:
            self.window.destroy()
        except tk.TclError:
            pass
