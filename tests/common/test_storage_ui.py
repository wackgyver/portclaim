"""Display/hardware/network-free storage UI tests; no local download is made."""
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import threading
import unittest
from unittest.mock import ANY, Mock, create_autospec, patch

from client import storage_ui as ui
from client.common.storage import StorageClient


class Variable:
    def __init__(self, master=None, value=""):
        self.value = value
        self.owner = threading.get_ident()
        self.traces = {}

    def get(self):
        assert threading.get_ident() == self.owner, "Tk variable read from worker"
        return self.value

    def set(self, value):
        assert threading.get_ident() == self.owner, "Tk variable write from worker"
        self.value = value
        for callback in tuple(self.traces.values()):
            callback()

    def trace_add(self, mode, callback):
        assert threading.get_ident() == self.owner
        self.traces["trace"] = callback
        return "trace"

    def trace_remove(self, mode, ident):
        assert threading.get_ident() == self.owner
        del self.traces[ident]


class Widget:
    """Small Tk stand-in that rejects worker access and post-destroy calls."""
    def __init__(self, parent=None, **kwargs):
        self.owner = threading.get_ident()
        self.root = parent.root if parent else self
        self.destroyed = False
        self.options = kwargs
        self.rows = {}
        self.selected = ()
        self.callbacks = {}
        self.bindings = {}
        self.text = ""
        self.count = 0

    def check(self):
        assert threading.get_ident() == self.owner, "Tk widget accessed from worker"
        assert not self.root.destroyed, "Tk widget accessed after destroy"

    def configure(self, **kwargs):
        self.check()
        self.options.update(kwargs)

    def noop(self, *args, **kwargs):
        self.check()

    pack = grid = rowconfigure = columnconfigure = title = geometry = minsize = protocol = noop
    heading = column = yview = xview = set = noop

    def bind(self, event, callback):
        self.check()
        self.bindings[event] = callback

    def insert(self, parent, index, iid=None, values=None):
        self.check()
        if iid is not None:
            self.rows[iid] = values
        else:
            self.text = index

    def delete(self, *items):
        self.check()
        for item in items:
            self.rows.pop(item, None)
        self.selected = tuple(x for x in self.selected if x in self.rows)
        self.text = ""

    def get_children(self):
        self.check()
        return tuple(self.rows)

    def selection(self):
        self.check()
        return self.selected

    def selection_set(self, *items):
        self.check()
        self.selected = tuple(items[0]) if len(items) == 1 and isinstance(items[0], tuple) else tuple(items)

    def after(self, delay, callback):
        self.check()
        self.count += 1
        self.callbacks[self.count] = callback
        return self.count

    def after_cancel(self, ident):
        self.check()
        del self.callbacks[ident]

    def destroy(self):
        self.check()
        self.destroyed = True


def volume(available=True, warning="Source must already be mounted read-only", can_unmount=True):
    return {"id": "vol-1", "label": "Test storage", "available": available,
            "generation": "generation-1" if available else None, "warning": warning,
            "can_unmount": can_unmount}


def unmount_result(status="unmounted"):
    return {"status": status, "volume": "vol-1", "generation": "generation-1"}


def listing(entries=(), revision="directory-revision", next_offset=None, limited=False):
    return {"entries": list(entries), "revision": revision, "next_offset": next_offset, "limited": limited}


def entry(name="report.txt", kind="file", revision="file-revision"):
    return {"name": name, "type": kind, "size": 10, "revision": revision}


class StorageUITests(unittest.TestCase):
    def setUp(self):
        for module, names in ((ui.tk, ("Toplevel", "Text")),
                              (ui.ttk, ("Frame", "Label", "Entry", "Button", "Treeview", "Scrollbar"))):
            for name in names:
                self.enterContext(patch.object(module, name, Widget))
        self.enterContext(patch.object(ui.tk, "StringVar", Variable))
        self.enterContext(patch.dict(os.environ, {
            "USB_LOOM_STORAGE_SSH": "storage-test", "USB_LOOM_STORAGE_UNMOUNT_SSH": "unmount-test"}))
        self.downloads_directory = self.enterContext(patch.object(ui, "_downloads_directory", return_value=None))
        self.confirm = self.enterContext(patch.object(ui.messagebox, "askyesno", return_value=False))
        self.save_dialog = self.enterContext(patch.object(ui.filedialog, "asksaveasfilename", return_value=""))
        self.factory = self.enterContext(patch.object(ui, "_client_for_alias", autospec=True))
        self.client = create_autospec(StorageClient, instance=True)
        self.factory.return_value = self.client
        self.client.volumes.return_value = [volume()]
        self.client.list.return_value = listing()
        self.client.unmount.return_value = unmount_result()
        self.app = ui.StorageWindow(None)
        self.addCleanup(self.app.close)

    def poll(self):
        # Model Tk removing its callback before dispatch, without a display loop.
        ident = self.app._after_id
        self.app.window.callbacks.pop(ident)()

    def finish(self):
        worker = self.app._thread
        self.assertIsNotNone(worker)
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive(), "mock worker failed to finish")
        self.poll()
        self.assertFalse(self.app._busy)

    def refresh(self):
        self.app._refresh()
        self.finish()

    def open_volume(self, entries=(), **page):
        self.refresh()
        self.app.volumes_tree.selection_set("0")
        self.app._select_volume()
        self.client.list.return_value = listing(entries, **page)
        self.app._open_volume()
        self.finish()

    def select_file(self, name="report.txt"):
        self.open_volume([entry(name)])
        self.app.entries_tree.selection_set("0")
        self.app._controls()

    def test_construct_does_not_create_client_or_activate_storage(self):
        self.factory.assert_not_called()
        self.client.unmount.assert_not_called()
        self.downloads_directory.assert_not_called()
        self.assertEqual(self.app.alias_var.get(), "storage-test")
        self.assertEqual(self.app.volumes_tree.selection(), ())
        self.assertIsNone(self.app._volume)
        self.assertEqual(self.app.open_volume_btn.options["state"], "disabled")
        self.assertEqual(self.app._events.maxsize, ui.QUEUE_SIZE)

    def test_empty_environment_does_not_inherit_any_hub_alias(self):
        with patch.dict(os.environ, {}, clear=True):
            other = ui.StorageWindow(None)
            try:
                self.assertEqual(other.alias_var.get(), "")
                other._refresh()
                self.factory.assert_not_called()
                self.assertFalse(other._busy)
                self.assertIn("explicit SSH alias", other.status_var.get())
            finally:
                other.close()

    def test_refresh_never_selects_or_opens_first_volume(self):
        self.refresh()
        self.factory.assert_called_once_with("storage-test", unmount_alias="unmount-test")
        self.client.volumes.assert_called_once_with(cancel=self.app._cancel)
        self.assertEqual(self.app.volumes_tree.selection(), ())
        self.client.list.assert_not_called()
        self.app._open_volume()
        self.client.list.assert_not_called()
        self.assertIsNone(self.app._volume)
        self.assertIn("read-only", self.app.volumes_tree.rows["0"][2])

    def test_selection_alone_never_starts_io_and_unavailable_cannot_open(self):
        self.client.volumes.return_value = [volume(False, "Not mounted; no automatic mount")]
        self.refresh()
        self.app.volumes_tree.selection_set("0")
        self.app._select_volume()
        self.client.list.assert_not_called()
        self.assertIn("no automatic mount", self.app.warning_var.get())
        self.assertEqual(self.app.open_volume_btn.options["state"], "disabled")
        self.app._open_volume()
        self.client.list.assert_not_called()

    def test_available_selection_requires_separate_open_and_generation(self):
        self.refresh()
        self.app.volumes_tree.selection_set("0")
        self.app._select_volume()
        self.client.list.assert_not_called()
        self.assertEqual(self.app.open_volume_btn.options["state"], "normal")
        self.app._open_volume()
        self.finish()
        self.client.list.assert_called_once_with("vol-1", "generation-1", [], offset=0,
                                                revision=None, cancel=self.app._cancel)
        self.assertEqual(self.app.entries_tree.selection(), ())

    def test_directory_pages_up_preserve_components_and_revisions(self):
        self.open_volume([entry("folder with spaces", "directory")], next_offset=20, limited=True)
        self.assertIn("limited", self.app.status_var.get())
        warning = self.app.warning_var.get()
        self.client.list.return_value = listing([entry("other", "directory")])
        self.app._next_page()
        self.finish()
        self.client.list.assert_called_with("vol-1", "generation-1", [], offset=20,
                                           revision="directory-revision", cancel=self.app._cancel)
        self.app.entries_tree.selection_set("0")
        self.client.list.return_value = listing([entry()])
        self.app._open_directory()
        self.finish()
        self.client.list.assert_called_with("vol-1", "generation-1", ["other"], offset=0,
                                           revision=None, cancel=self.app._cancel)
        self.assertEqual(self.app.warning_var.get(), warning)
        self.app._up()
        self.finish()
        self.assertEqual(self.client.list.call_args.args[2], [])

    def test_busy_volume_selection_restores_highlight_and_warning(self):
        self.client.volumes.return_value = [volume(), dict(volume(False), id="vol-2")]
        self.open_volume([entry()])
        warning = self.app.warning_var.get()
        self.app._busy = True
        self.app.volumes_tree.selection_set("1")
        self.app._select_volume()
        self.assertEqual(self.app.volumes_tree.selection(), ("0",))
        self.assertEqual(self.app.warning_var.get(), warning)
        self.assertEqual(self.app._volume["id"], "vol-1")
        self.app._busy = False
        self.app._select_volume()  # delayed event caused by restoring selection
        self.assertEqual(self.app._volume["id"], "vol-1")
        self.assertEqual(len(self.app._entries), 1)
        self.app.volumes_tree.selection_set("1")
        self.app._select_volume()
        self.assertIsNone(self.app._volume)
        self.assertEqual(self.app._entries, {})
        self.assertEqual(self.app.open_volume_btn.options["state"], "disabled")

    def test_preview_explicit_bounded_text_only_and_selected_generation(self):
        self.select_file()
        self.client.preview.assert_not_called()
        self.client.preview.return_value = {"text": "x" * (ui.PREVIEW_CHARS + 1), "truncated": False}
        self.app._path = ["parent", "child"]
        self.app._preview()
        self.finish()
        self.client.preview.assert_called_once_with("vol-1", "generation-1", ["parent", "child", "report.txt"],
                                                   "file-revision", cancel=self.app._cancel)
        self.assertLess(len(self.app.preview_text.text), ui.PREVIEW_CHARS + 100)
        self.assertIn("truncated", self.app.status_var.get())
        self.assertEqual(self.app.preview_text.options["state"], "disabled")
        self.client.download.assert_not_called()

    def test_save_uses_only_explicit_local_destination_not_remote_name(self):
        self.select_file("untrusted-remote-name")
        self.app._path = ["folder"]
        self.client.download.return_value = {"bytes": 10, "sha256": "a" * 64}
        with patch.object(ui.filedialog, "asksaveasfilename", return_value="/chosen/new.txt") as dialog:
            self.app._save()
            self.finish()
        self.assertEqual(dialog.call_args.kwargs["initialfile"], "untrusted-remote-name")
        self.assertNotIn("initialdir", dialog.call_args.kwargs)
        self.client.download.assert_called_once_with("vol-1", "generation-1", ["folder", "untrusted-remote-name"],
            "file-revision", "/chosen/new.txt", cancel=self.app._cancel, progress=ANY)
        self.assertIn("Saved local copy: 10 bytes", self.app.status_var.get())

    def test_save_prefill_is_sanitized_without_changing_remote_selection(self):
        remote_name = "folder\\unsafe:name\u202e.txt"
        self.select_file(remote_name)
        self.app._path = ["remote-parent"]
        self.client.download.return_value = {"bytes": 10, "sha256": "a" * 64}
        self.save_dialog.return_value = "/chosen/new.txt"
        self.app._save()
        self.finish()
        self.assertEqual(self.save_dialog.call_args.kwargs["initialfile"], "folder_unsafe_name.txt")
        self.assertNotIn("initialdir", self.save_dialog.call_args.kwargs)
        self.client.download.assert_called_once_with("vol-1", "generation-1", ["remote-parent", remote_name],
            "file-revision", "/chosen/new.txt", cancel=self.app._cancel, progress=ANY)

    def test_cancelled_save_dialog_never_downloads(self):
        self.select_file()
        with patch.object(ui.filedialog, "asksaveasfilename", return_value=""):
            self.app._save()
        self.client.download.assert_not_called()
        self.assertFalse(self.app._busy)

    def test_existing_destination_client_refusal_is_not_shown_as_success(self):
        self.select_file()
        self.client.download.side_effect = FileExistsError("private/path already exists")
        with patch.object(ui.filedialog, "asksaveasfilename", return_value="/existing/file"):
            self.app._save()
            self.finish()
        self.assertIn("failed", self.app.status_var.get())
        self.assertNotIn("private", self.app.status_var.get())
        self.assertNotIn("Saved", self.app.status_var.get())

    def test_symlinks_and_special_files_cannot_open_preview_or_download(self):
        for kind in ("symlink", "special"):
            with self.subTest(kind=kind):
                self.open_volume([entry("not-a-file", kind)])
                self.app.entries_tree.selection_set("0")
                self.app._controls()
                calls = self.client.list.call_count
                self.app._open_directory()
                self.app._preview()
                with patch.object(ui.filedialog, "asksaveasfilename") as dialog:
                    self.app._save()
                dialog.assert_not_called()
                self.assertEqual(self.client.list.call_count, calls)
                self.client.preview.assert_not_called()
                self.client.download.assert_not_called()
                self.assertEqual(self.app.preview_btn.options["state"], "disabled")

    def test_one_worker_cancel_remains_busy_until_thread_exits(self):
        entered, release = threading.Event(), threading.Event()
        observed = []

        def work(cancel, progress):
            observed.append(threading.get_ident())
            entered.set()
            release.wait(2)
            return "late result"

        callback = Mock()
        self.addCleanup(release.set)
        self.assertTrue(self.app._start("Test", work, callback))
        self.assertTrue(entered.wait(1))
        try:
            first = self.app._thread
            self.assertFalse(self.app._start("Second", Mock(), Mock()))
            self.app._refresh()
            self.factory.assert_not_called()
            self.app.cancel()
            self.assertTrue(self.app._cancel.is_set())
            self.assertTrue(self.app._busy)
            self.poll()
            self.assertIs(self.app._thread, first)
            self.assertEqual(self.app.refresh_btn.options["state"], "disabled")
            self.assertEqual(self.app.cancel_btn.options["state"], "disabled")
        finally:
            release.set()
        self.finish()
        callback.assert_not_called()
        self.assertIn("Cancelled", self.app.status_var.get())
        self.assertNotEqual(observed, [threading.get_ident()])

    def test_progress_queue_bounded_lossy_and_terminal_not_lost(self):
        callback = Mock()
        sizes = []

        def work(cancel, progress):
            for index in range(10000):
                progress(index, 10000)
                sizes.append(self.app._events.qsize())
            return "done"

        self.app._start("Flood", work, callback)
        self.finish()
        self.assertLessEqual(max(sizes), ui.QUEUE_SIZE)
        callback.assert_called_once_with("done")
        self.assertEqual(self.app._events.qsize(), 0)
        self.assertEqual(len(self.app.window.callbacks), 1)

    def test_terminal_result_does_not_unlock_until_worker_really_exits(self):
        class StillAlive:
            def is_alive(self):
                return True
        self.app._busy = True
        self.app._thread = StillAlive()
        self.app._on_success = Mock()
        self.app._events.put_nowait(("result", "done"))
        self.poll()
        self.assertTrue(self.app._busy)
        self.app._on_success.assert_not_called()
        self.assertEqual(self.app._completion, ("result", "done"))

    def test_close_cancels_without_join_and_late_worker_never_touches_tk(self):
        entered, release = threading.Event(), threading.Event()
        callback = Mock()
        self.addCleanup(release.set)

        def work(cancel, progress):
            entered.set()
            release.wait(2)
            progress(1, 1)
            return "late"

        self.app._start("Test", work, callback)
        self.assertTrue(entered.wait(1))
        worker = self.app._thread
        try:
            with patch.object(worker, "join", side_effect=AssertionError("GUI must not join")):
                self.app.close()
            self.assertTrue(self.app._cancel.is_set())
            self.assertTrue(self.app.window.destroyed)
            self.assertEqual(self.app.window.callbacks, {})
            self.app.close()
            self.app._poll()  # Even an already-dispatched callback must be harmless.
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        callback.assert_not_called()

    def test_failures_are_sanitized_and_gui_recovers(self):
        self.client.volumes.side_effect = OSError("ssh secret-token /private/path\x1b[31m")
        self.refresh()
        self.assertIn("failed", self.app.status_var.get())
        for sensitive in ("secret-token", "/private/path", "\x1b"):
            self.assertNotIn(sensitive, self.app.status_var.get())
        self.assertEqual(self.app.refresh_btn.options["state"], "normal")
        self.assertEqual(self.app.volumes_tree.selection(), ())
        self.assertEqual(ui._display("a\x1bb\u202ec"), "a\ufffdb\ufffdc")

    def test_first_save_suggests_known_downloads_then_leaves_native_browsing_alone(self):
        self.select_file("Quarterly report.pdf")
        self.app._path = ["remote", "private"]
        self.downloads_directory.return_value = "/known/Downloads"
        self.app._save()
        self.assertEqual(self.save_dialog.call_args.kwargs["initialdir"], "/known/Downloads")
        self.assertEqual(self.save_dialog.call_args.kwargs["initialfile"], "Quarterly report.pdf")
        self.assertIn("No download started", self.app.status_var.get())
        self.app._save()
        self.assertNotIn("initialdir", self.save_dialog.call_args.kwargs)
        self.downloads_directory.assert_called_once_with()
        self.client.download.assert_not_called()

    def test_save_dialog_phase_and_nested_actions_are_guarded(self):
        self.select_file()
        self.app._path = ["folder"]
        self.app._next_offset = 20
        list_calls, volume_calls = self.client.list.call_count, self.client.volumes.call_count

        def choose(**options):
            self.assertTrue(self.app._modal)
            self.assertIn("Choose destination", self.app.status_var.get())
            self.assertIn("download has not started", self.app.status_var.get())
            for button in (self.app.save_btn, self.app.unmount_btn, self.app.refresh_btn,
                           self.app.open_volume_btn, self.app.open_dir_btn, self.app.preview_btn,
                           self.app.cancel_btn, self.app.up_btn, self.app.next_btn):
                self.assertEqual(button.options["state"], "disabled")
            self.app._refresh()
            self.app._save()
            self.app._unmount()
            self.app._preview()
            self.app._open_volume()
            self.app._open_directory()
            self.app._up()
            self.app._next_page()
            self.app.cancel()
            self.assertFalse(self.app._start("Nested", Mock(), Mock()))
            self.assertEqual(self.client.list.call_count, list_calls)
            self.assertEqual(self.client.volumes.call_count, volume_calls)
            self.client.preview.assert_not_called()
            self.client.download.assert_not_called()
            self.client.unmount.assert_not_called()
            self.confirm.assert_not_called()
            return ""

        self.save_dialog.side_effect = choose
        self.app._save()
        self.save_dialog.assert_called_once()
        self.assertFalse(self.app._modal)
        self.assertFalse(self.app._busy)
        self.assertEqual(self.app.save_btn.options["state"], "normal")
        self.assertIn("No download started", self.app.status_var.get())

    def test_save_rechecks_selection_generation_alias_client_and_busy_after_dialog(self):
        for change in ("entry", "volume", "generation", "alias", "client", "busy"):
            with self.subTest(change=change):
                self.select_file()

                def choose(**options):
                    if change == "entry":
                        self.app.entries_tree.selection_set(())
                    elif change == "volume":
                        self.app.volumes_tree.selection_set(())
                    elif change == "generation":
                        self.app._volume["generation"] = "changed-generation"
                    elif change == "alias":
                        self.app.alias_var.set("different-read-alias")
                    elif change == "client":
                        self.app._client = Mock()
                    else:
                        self.app._busy = True
                    return "/explicit/choice.txt"

                self.save_dialog.side_effect = choose
                self.app._save()
                self.client.download.assert_not_called()
                self.assertIn("Download has not started", self.app.status_var.get())
                self.app._busy = False
                self.app.alias_var.set("storage-test")

    def test_save_dialog_error_is_sanitized_and_does_not_start_download(self):
        self.select_file()
        self.save_dialog.side_effect = ui.tk.TclError("secret /private/path")
        self.app._save()
        self.assertEqual(self.app.status_var.get(), "Could not choose destination. Download has not started.")
        self.assertFalse(self.app._modal)
        self.assertEqual(self.app.save_btn.options["state"], "normal")
        self.client.download.assert_not_called()

    def test_close_during_save_dialog_prevents_download_and_post_destroy_tk(self):
        self.select_file()

        def choose(**options):
            self.app.close()
            return "/explicit/new.txt"

        self.save_dialog.side_effect = choose
        self.app._save()
        self.client.download.assert_not_called()
        self.assertTrue(self.app._closed)
        self.assertEqual(self.app.window.callbacks, {})
        self.assertEqual(self.app.alias_var.traces, {})
        self.app._poll()

    def select_for_unmount(self):
        self.refresh()
        self.app.volumes_tree.selection_set("0")
        self.app._select_volume()

    def assert_unmount_invalidated(self):
        self.assertIsNone(self.app._volume)
        self.assertIsNone(self.app._client)
        self.assertIsNone(self.app._inventory_alias)
        self.assertIsNone(self.app._inventory_unmount_alias)
        self.assertEqual(self.app._volumes, {})
        self.assertEqual(self.app._entries, {})
        self.assertEqual(self.app._path, [])
        self.assertIsNone(self.app._revision)
        self.assertIsNone(self.app._next_offset)
        self.assertEqual(self.app.preview_text.text, "")
        self.assertEqual(self.app.volumes_tree.selection(), ())
        self.assertEqual(self.app.unmount_btn.options["state"], "disabled")
        self.assertEqual(self.app.open_volume_btn.options["state"], "disabled")

    def test_unmount_never_automatic_and_requires_highlight_not_open_directory(self):
        self.refresh()
        self.app._unmount()
        self.confirm.assert_not_called()
        self.client.unmount.assert_not_called()
        self.assertIsNone(self.app._volume)
        self.assertEqual(self.app.unmount_btn.options["state"], "disabled")
        self.app.volumes_tree.selection_set("0")
        self.app._select_volume()
        self.client.list.assert_not_called()
        self.client.unmount.assert_not_called()
        self.assertEqual(self.app.unmount_btn.options["state"], "normal")
        self.confirm.return_value = True
        self.app._unmount()
        self.finish()
        self.client.unmount.assert_called_once_with("vol-1", "generation-1", cancel=self.app._cancel)
        self.assertIn("Test storage", self.confirm.call_args.kwargs["message"])
        self.assertEqual(self.confirm.call_args.kwargs["default"], ui.messagebox.NO)
        self.assertIn("cannot undo", self.confirm.call_args.kwargs["message"])
        self.assertIn("does not power off", self.app.status_var.get())
        self.assertIn("Unmounted selected volume", self.app.status_var.get())
        self.assert_unmount_invalidated()
        self.assertEqual(self.client.volumes.call_count, 1)

    def test_unmount_unconfigured_or_identical_alias_has_no_fallback(self):
        for read_alias, unmount_alias in (("storage-test", ""), ("", "unmount-test"),
                                           ("storage-test", "storage-test")):
            with self.subTest(read_alias=read_alias, unmount_alias=unmount_alias):
                self.app._configured_read_alias = read_alias
                self.app._unmount_alias = unmount_alias
                self.select_for_unmount()
                self.factory.assert_called_with("storage-test", unmount_alias=None)
                self.assertEqual(self.app.unmount_btn.options["state"], "disabled")
                self.app._unmount()
                self.confirm.assert_not_called()
                self.client.unmount.assert_not_called()
                self.assertIn("disabled", self.app.status_var.get())

    def test_constructor_reads_empty_unmount_default_without_fallback(self):
        with patch.dict(os.environ, {"USB_LOOM_STORAGE_SSH": "configured-read"}, clear=True):
            other = ui.StorageWindow(None)
            try:
                self.assertEqual(other._unmount_alias, "")
                self.assertIsNone(other._paired_unmount_alias("configured-read"))
                self.assertIn("USB_LOOM_STORAGE_UNMOUNT_SSH", other.unmount_hint_var.get())
            finally:
                other.close()
        self.factory.assert_not_called()

    def test_edited_read_alias_disables_unmount_and_never_pairs_unrelated_inventory(self):
        self.select_for_unmount()
        self.app.alias_var.set("another-read-endpoint")
        self.assertEqual(self.app.unmount_btn.options["state"], "disabled")
        self.assertIn("differs", self.app.unmount_hint_var.get())
        self.app._unmount()
        self.confirm.assert_not_called()
        self.select_for_unmount()
        self.factory.assert_called_with("another-read-endpoint", unmount_alias=None)
        self.app.alias_var.set("storage-test")
        self.assertEqual(self.app.unmount_btn.options["state"], "disabled")
        self.assertIn("Refresh", self.app.unmount_hint_var.get())
        self.app._unmount()
        self.confirm.assert_not_called()
        self.select_for_unmount()
        self.factory.assert_called_with("storage-test", unmount_alias="unmount-test")
        self.assertEqual(self.app.unmount_btn.options["state"], "normal")
        self.client.unmount.assert_not_called()

    def test_unmount_unavailable_unsupported_or_missing_generation_does_not_confirm(self):
        cases = [volume(False), volume(can_unmount=False), dict(volume(), can_unmount=1),
                 dict(volume(), generation=None), dict(volume(), generation="")]
        unsupported = volume()
        del unsupported["can_unmount"]
        cases.append(unsupported)
        for row in cases:
            with self.subTest(row=row):
                self.client.volumes.return_value = [row]
                self.select_for_unmount()
                self.assertEqual(self.app.unmount_btn.options["state"], "disabled")
                self.app._unmount()
                self.confirm.assert_not_called()
                self.client.unmount.assert_not_called()

    def test_unmount_confirmation_cancel_preserves_browser_and_warning(self):
        self.select_file()
        warning = self.app.warning_var.get()
        before = dict(self.app._volume)
        self.app._unmount()
        self.confirm.assert_called_once()
        self.client.unmount.assert_not_called()
        self.assertEqual(self.app._volume, before)
        self.assertEqual(self.app.warning_var.get(), warning)
        self.assertEqual(self.app.status_var.get(), "Unmount not requested. No request was sent.")
        self.assertFalse(self.app._busy)
        self.assertFalse(self.app._modal)

    def test_unmount_confirmation_guards_all_nested_actions(self):
        self.select_file()
        list_calls, volume_calls = self.client.list.call_count, self.client.volumes.call_count

        def confirm(**options):
            self.assertTrue(self.app._modal)
            self.assertIn("no request has been sent", self.app.status_var.get())
            self.assertEqual(self.app.unmount_btn.options["state"], "disabled")
            self.assertEqual(self.app.cancel_btn.options["state"], "disabled")
            self.app._unmount()
            self.app._save()
            self.app._refresh()
            self.app._preview()
            self.app._open_volume()
            self.app._list([])
            self.assertFalse(self.app._start("Nested", Mock(), Mock()))
            self.save_dialog.assert_not_called()
            self.client.unmount.assert_not_called()
            self.client.download.assert_not_called()
            self.client.preview.assert_not_called()
            self.assertEqual(self.client.list.call_count, list_calls)
            self.assertEqual(self.client.volumes.call_count, volume_calls)
            return False

        self.confirm.side_effect = confirm
        self.app._unmount()
        self.confirm.assert_called_once()
        self.assertFalse(self.app._modal)

    def test_unmount_rechecks_selection_generation_alias_availability_and_busy(self):
        for change in ("selection", "generation", "alias", "available", "can_unmount", "busy", "client"):
            with self.subTest(change=change):
                self.select_for_unmount()

                def confirm(**options):
                    if change == "selection":
                        self.app.volumes_tree.selection_set(())
                    elif change == "generation":
                        self.app._volumes["0"]["generation"] = "changed-generation"
                    elif change == "alias":
                        self.app.alias_var.set("other-endpoint")
                    elif change in ("available", "can_unmount"):
                        self.app._volumes["0"][change] = False
                    elif change == "client":
                        self.app._client = Mock()
                    else:
                        self.app._busy = True
                    return True

                self.confirm.side_effect = confirm
                self.app._unmount()
                self.client.unmount.assert_not_called()
                self.assertIn("unmount not requested", self.app.status_var.get())
                self.app._busy = False
                self.app.alias_var.set("storage-test")
                self.client.volumes.return_value = [volume()]

    def test_close_during_unmount_confirmation_never_sends_request(self):
        self.select_for_unmount()

        def confirm(**options):
            self.app.close()
            return True

        self.confirm.side_effect = confirm
        self.app._unmount()
        self.client.unmount.assert_not_called()
        self.assertEqual(self.app.window.callbacks, {})
        self.assertFalse(self.app._modal)
        self.app._poll()

    def test_unmount_confirmation_error_never_sends_request(self):
        self.select_for_unmount()
        self.confirm.side_effect = ui.tk.TclError("private-dialog-detail")
        self.app._unmount()
        self.client.unmount.assert_not_called()
        self.assertEqual(self.app.status_var.get(), "Could not confirm unmount. No request was sent.")
        self.assertFalse(self.app._modal)

    def test_every_unmount_outcome_invalidates_generation_and_requires_explicit_refresh(self):
        messages = {"unmounted": "Unmounted selected volume", "busy": "volume is busy", "stale": "stale",
                    "unavailable": "unavailable", "denied": "denied", "failed": "unknown"}
        for status, text in messages.items():
            with self.subTest(status=status):
                self.select_file()
                self.app._path = ["folder"]
                self.app._next_offset = 20
                self.app._clear_preview("earlier preview")
                warning = self.app.warning_var.get()
                volume_calls = self.client.volumes.call_count
                self.confirm.return_value = True
                self.client.unmount.return_value = unmount_result(status)
                self.app._unmount()
                self.assertEqual(self.app.warning_var.get(), warning)
                self.assert_unmount_invalidated()
                self.finish()
                self.assertIn(text, self.app.status_var.get())
                self.assertIn("Refresh volumes", self.app.status_var.get())
                self.assertEqual(self.app.warning_var.get(), warning)
                self.assertEqual(self.client.volumes.call_count, volume_calls)
                self.assert_unmount_invalidated()
                self.assertEqual(self.app.refresh_btn.options["state"], "normal")
                if status != "unmounted":
                    self.assertNotIn("Unmounted", self.app.status_var.get())

    def test_unmount_transport_or_response_error_is_unknown_not_cancelled_or_success(self):
        cases = [OSError("private-ssh-detail"), ValueError("secret-data"), None, {},
                 dict(unmount_result(), volume="other"), dict(unmount_result(), generation="stale"),
                 dict(unmount_result(), status="powered-off")]
        for result in cases:
            with self.subTest(result=result):
                self.select_for_unmount()
                self.confirm.return_value = True
                self.client.unmount.side_effect = result if isinstance(result, Exception) else None
                self.client.unmount.return_value = result
                self.app._unmount()
                self.finish()
                self.assertEqual(self.app.status_var.get(), ui.UNMOUNT_UNKNOWN)
                self.assert_unmount_invalidated()

    def test_unmount_cancel_is_disabled_until_worker_exits(self):
        self.select_for_unmount()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def unmount(volume_id, generation, cancel=None):
            entered.set()
            release.wait(2)
            return unmount_result()

        self.client.unmount.side_effect = unmount
        self.confirm.return_value = True
        self.app._unmount()
        self.assertTrue(entered.wait(1))
        try:
            self.assertEqual(self.app.cancel_btn.options["state"], "disabled")
            self.assertIn("cannot undo", self.app.status_var.get())
            self.app.cancel()
            self.assertFalse(self.app._cancel.is_set())
            self.assertTrue(self.app._busy)
            self.app._unmount()
            self.confirm.assert_called_once()
            self.assertFalse(self.app._start("Second", Mock(), Mock()))
        finally:
            release.set()
        self.finish()
        self.client.unmount.assert_called_once()
        self.assertIn("Unmounted", self.app.status_var.get())

    def test_close_during_unmount_never_claims_rollback_or_touches_destroyed_widgets(self):
        self.select_for_unmount()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def unmount(volume_id, generation, cancel=None):
            entered.set()
            release.wait(2)
            return unmount_result()

        self.client.unmount.side_effect = unmount
        self.confirm.return_value = True
        self.app._unmount()
        worker = self.app._thread
        self.assertTrue(entered.wait(1))
        try:
            with patch.object(worker, "join", side_effect=AssertionError("GUI cannot join")):
                self.app.close()
            self.assertTrue(self.app._cancel.is_set())
            self.assertEqual(self.app.window.callbacks, {})
            self.assertIn("cannot undo", self.app.status_var.get())
            self.app._poll()
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertNotIn("Cancelled", self.app.status_var.get())
        self.assertNotIn("Unmounted", self.app.status_var.get())

    def test_cancel_event_on_unmount_is_unknown_even_if_worker_returns_success(self):
        self.select_for_unmount()
        self.confirm.return_value = True
        self.app._unmount()
        self.app._cancel.set()  # e.g. transport cancelled after the endpoint acted
        self.finish()
        self.assertEqual(self.app.status_var.get(), ui.UNMOUNT_UNKNOWN)
        self.assert_unmount_invalidated()

    def test_active_local_download_forbids_unmount_confirmation_or_request(self):
        self.select_file()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def download(*args, **kwargs):
            entered.set()
            release.wait(2)
            return {"bytes": 10, "sha256": "a" * 64}

        self.client.download.side_effect = download
        self.save_dialog.return_value = "/explicit/choice.txt"
        self.app._save()
        self.assertTrue(entered.wait(1))
        try:
            self.assertEqual(self.app.unmount_btn.options["state"], "disabled")
            self.app._unmount()
            self.confirm.assert_not_called()
            self.client.unmount.assert_not_called()
        finally:
            release.set()
        self.finish()

    def test_unmount_worker_start_failure_clears_selection_without_unknown_dispatch_claim(self):
        self.select_for_unmount()
        self.confirm.return_value = True
        with patch.object(ui.threading.Thread, "start", side_effect=RuntimeError("private-error")):
            self.app._unmount()
        self.assert_unmount_invalidated()
        self.assertIn("No request was sent", self.app.status_var.get())
        self.assertIn("Refresh volumes", self.app.status_var.get())
        self.assertNotIn("private-error", self.app.status_var.get())
        self.assertFalse(self.app._busy)
        self.client.unmount.assert_not_called()

    def test_thread_start_failure_is_reported_without_wedging_ui(self):
        with patch.object(ui.threading.Thread, "start", side_effect=RuntimeError("secret")):
            self.app._refresh()
        self.assertFalse(self.app._busy)
        self.assertIsNone(self.app._thread)
        self.assertIn("Could not start", self.app.status_var.get())
        self.assertEqual(self.app.refresh_btn.options["state"], "normal")


class FilenameTests(unittest.TestCase):
    def test_ordinary_titles_and_extensions_are_preserved_exactly(self):
        for name in ("report.txt", "Quarterly report (final).pdf", "archive.tar.gz", "README", ".env",
                     "Café — report.txt", "Report .txt", "Hello 👋.txt", "under_score-123.csv"):
            with self.subTest(name=name):
                self.assertEqual(ui._safe_filename(name), name)

    def test_separators_drive_unc_ads_and_control_forms_cannot_be_paths(self):
        names = ["../../report.txt", r"..\..\report.txt", r"C:\private\report.txt", "C:report.txt",
                 r"\\server\share\report.txt", "report.txt:secret", '<bad>|name?*.txt',
                 "invisible\u202ereport\u2069.txt", "control\x00\r\n\t.txt", "bad\ud800.txt"]
        for name in names:
            with self.subTest(name=repr(name)):
                safe = ui._safe_filename(name)
                self.assertEqual(PureWindowsPath(safe).name, safe)
                self.assertEqual(PurePosixPath(safe).name, safe)
                self.assertEqual(PureWindowsPath(safe).drive, "")
                self.assertFalse(PureWindowsPath(safe).is_absolute())
                self.assertFalse(PurePosixPath(safe).is_absolute())
                self.assertTrue(safe.isprintable())
                self.assertFalse(any(c in '<>:"/\\\\|?*\u202e\u2069' for c in safe))
                self.assertLessEqual(len(safe.encode("utf-8")), ui.FILENAME_BYTES)

    def test_leading_tilde_cannot_be_expanded_by_native_dialog(self):
        for name in ("~", "~user", "~report.txt", "~/secret.txt"):
            with self.subTest(name=name):
                self.assertTrue(ui._safe_filename(name).startswith("_~"))
                self.assertNotIn("/", ui._safe_filename(name))

    def test_empty_or_only_unsafe_names_have_deterministic_fallback(self):
        for name in (None, "", ".", "..", " . . ", "\x00\u202e\u2069", ':/\\\\*?<>|"'):
            with self.subTest(name=repr(name)):
                self.assertEqual(ui._safe_filename(name), "download")

    def test_windows_device_names_and_trailing_dots_spaces_are_neutralized(self):
        for name in ("CON", "nul.txt", "PRN.csv", "Aux", "COM1.txt", "LPT9", "COM¹.txt",
                     "lpt².log", "CLOCK$", "CONIN$", "CONOUT$", "CON .txt"):
            with self.subTest(name=name):
                self.assertEqual(ui._safe_filename(name), "_" + name)
        self.assertEqual(ui._safe_filename("ordinary.txt...  "), "ordinary.txt")
        self.assertEqual(ui._safe_filename("NUL. "), "_NUL")
        self.assertEqual(ui._safe_filename("COM10.txt"), "COM10.txt")

    def test_truncation_cannot_expose_a_reserved_windows_device_name(self):
        for name, expected in (("NUL" + " " * 250 + "x", "_NUL"),
                               ("CON" + " " * 245 + "x.mkv", "_CON.mkv"),
                               ("LPT1" + " " * 238 + "x.txt", "_LPT1.txt")):
            with self.subTest(expected=expected):
                self.assertLessEqual(len(name.encode("utf-8")), 255)
                self.assertEqual(ui._safe_filename(name), expected)
                self.assertEqual(ui._safe_filename(expected), expected)
                self.assertLessEqual(len(expected.encode("utf-8")), ui.FILENAME_BYTES)

    def test_byte_bound_keeps_useful_extension_and_complete_utf8(self):
        for name in ("a" * 1000 + ".pdf", "界" * 1000 + ".txt", "🙂" * 1000 + ".jpeg",
                     "." + "é" * 500 + ".csv"):
            with self.subTest(name=name[:10]):
                safe = ui._safe_filename(name)
                self.assertLessEqual(len(safe.encode("utf-8")), 240)
                self.assertTrue(safe.endswith("." + name.rsplit(".", 1)[1]))
                self.assertNotIn("\ufffd", safe)
                self.assertEqual(ui._safe_filename(safe), safe)
        for name in ("é" * 500, "title." + "x" * 500, "x" * 240 + " ."):
            safe = ui._safe_filename(name)
            self.assertLessEqual(len(safe.encode("utf-8")), 240)
            self.assertFalse(safe.endswith((" ", ".")))


class DownloadsDirectoryTests(unittest.TestCase):
    def test_only_existing_downloads_directory_is_suggested_without_creation(self):
        for exists in (False, True):
            with self.subTest(exists=exists):
                with patch.object(ui.Path, "home", return_value=Path("/synthetic/home")) as home, \
                        patch.object(ui.Path, "is_dir", autospec=True, return_value=exists) as is_dir, \
                        patch.object(ui.Path, "mkdir") as mkdir:
                    result = ui._downloads_directory()
                self.assertEqual(result, str(Path("/synthetic/home/Downloads")) if exists else None)
                home.assert_called_once_with()
                self.assertEqual(is_dir.call_args.args[0], Path("/synthetic/home/Downloads"))
                mkdir.assert_not_called()

    def test_inaccessible_or_unknown_home_has_no_initialdir(self):
        with patch.object(ui.Path, "home", side_effect=RuntimeError("no home")):
            self.assertIsNone(ui._downloads_directory())
        with patch.object(ui.Path, "home", return_value=Path("/synthetic/home")), \
                patch.object(ui.Path, "is_dir", side_effect=OSError("unavailable")):
            self.assertIsNone(ui._downloads_directory())


class ClientFactoryTests(unittest.TestCase):
    def test_lazy_factory_uses_current_storage_client_api(self):
        with patch("client.common.storage.StorageClient", autospec=True) as client:
            self.assertIs(ui._client_for_alias("explicit-alias"), client.return_value)
        client.assert_called_once_with("explicit-alias", unmount_alias=None)

    def test_lazy_factory_passes_only_explicit_unmount_alias(self):
        with patch("client.common.storage.StorageClient", autospec=True) as client:
            self.assertIs(ui._client_for_alias("read-alias", unmount_alias="unmount-alias"), client.return_value)
        client.assert_called_once_with("read-alias", unmount_alias="unmount-alias")


if __name__ == "__main__":
    unittest.main()
