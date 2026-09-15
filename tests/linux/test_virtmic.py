"""Virtual microphone ownership tests; never connect to the real audio server."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy.linux import virtmic


class FakePulse:
    def __init__(self):
        self.calls = []
        self.modules = {}
        self.existing_sink = False

    def __call__(self, *args):
        self.calls.append(args)
        if args == ("--format=json", "list", "sinks"):
            return json.dumps([{"name": virtmic.SINK}] if self.existing_sink or self.modules else [])
        if args[:2] == ("load-module", "module-null-sink"):
            self.modules[42] = " ".join(args[2:])
            return "42"
        if args == ("list", "short", "modules"):
            return "\n".join(f"{idx}\tmodule-null-sink\t{arguments}\t0" for idx, arguments in self.modules.items())
        if args[0] == "unload-module":
            del self.modules[int(args[1])]
            return ""
        raise AssertionError(f"Unexpected audio operation: {args[0]}")


class VirtualMicTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "runtime/mic.json"
        self.pulse = FakePulse()
        p = patch.object(virtmic, "pactl", side_effect=self.pulse)
        p.start(); self.addCleanup(p.stop)

    def test_created_sink_is_owned_and_removed_without_setting_defaults(self):
        virtmic.start(self.path)
        state = json.loads(self.path.read_text())
        self.assertEqual(state["module"], 42)
        self.assertEqual(len(state["owner"]), 32)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        virtmic.stop(self.path)
        self.assertEqual(self.pulse.modules, {})
        self.assertFalse(self.path.exists())
        self.assertFalse(any("set-default" in arg for args in self.pulse.calls for arg in args))

    def test_existing_sink_is_borrowed_not_owned(self):
        self.pulse.existing_sink = True
        virtmic.start(self.path)
        virtmic.stop(self.path)
        self.assertFalse(self.path.exists())
        self.assertFalse(any(args[0] in ("load-module", "unload-module") for args in self.pulse.calls))

    def test_reused_module_id_with_different_owner_is_never_unloaded(self):
        virtmic.start(self.path)
        self.pulse.modules[42] = 'sink_name=portclaim_mic sink_properties="portclaim.owner=someone-else"'
        virtmic.stop(self.path)
        self.assertIn(42, self.pulse.modules)
        self.assertFalse(any(args[0] == "unload-module" for args in self.pulse.calls))

    def test_failed_ownership_write_cleans_up_only_created_module(self):
        with patch.object(virtmic, "save_state", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                virtmic.start(self.path)
        self.assertEqual(self.pulse.modules, {})
        self.assertFalse(self.path.exists())

    def test_malformed_record_fails_without_unloading(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"module": "invalid", "owner": "bad"}')
        with self.assertRaises(ValueError):
            virtmic.stop(self.path)
        self.assertTrue(self.path.exists())
        self.assertEqual(self.pulse.calls, [])

    def test_partial_sink_name_or_owner_token_does_not_match(self):
        state = {"module": 42, "owner": "a" * 32}
        for arguments in (f'sink_name=portclaim_mic_other sink_properties="portclaim.owner={state["owner"]}"',
                          f'sink_name=portclaim_mic sink_properties="portclaim.owner={state["owner"]}extra"'):
            self.pulse.modules[42] = arguments
            self.assertFalse(virtmic.owns_module(state))


if __name__ == "__main__":
    unittest.main()
