"""Pinned condenser selection and pre-forward validation; never record live audio."""
from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hub"))
import audio


class EndLoop(Exception):
    pass


class MicrophonePinTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"USB_LOOM_MIC_USB_ID": "31b2:0011", "USB_LOOM_CAMERA_ENABLED": "1"})
        self.env.start(); self.addCleanup(self.env.stop)
        self.condenser = audio.AlsaCapture(2, 0, "USB Condenser Microphone")
        self.webcam = audio.AlsaCapture(0, 0, "USB 2.0 Camera")
        self.intel = audio.AlsaCapture(1, 0, "HDA Intel")
        self.identity = audio.CaptureIdentity(("31B2", "0011"), "Condenser", ("/synthetic/usb/1-5.4", "7"))
        self.ids = {0: ("0C45", "6366"), 1: ("", ""), 2: ("31B2", "0011")}

    def select(self, rows):
        with patch.object(audio, "_usb_ids", side_effect=lambda card: self.ids[card]), patch.object(
                audio, "capture_identity", return_value=self.identity):
            return audio.select_mic(rows)

    def test_camera_first_does_not_steal_condensers_selection(self):
        selected, status = self.select([self.webcam, self.intel, self.condenser])
        self.assertEqual(selected.card, 2)
        self.assertEqual(selected.alsa, "hw:CARD=Condenser,DEV=0")
        self.assertEqual(status, "pinned USB microphone")

    def test_missing_pin_target_is_silence_not_camera_or_intel(self):
        selected, status = self.select([self.webcam, self.intel])
        self.assertIsNone(selected)
        self.assertIn("unavailable", status)
        self.assertIn("no fallback", status)

    def test_multiple_matching_capture_pcms_fail_closed(self):
        selected, status = self.select([self.condenser, replace(self.condenser, device=1), self.webcam])
        self.assertIsNone(selected)
        self.assertIn("ambiguous", status)

    def test_malformed_or_blank_pin_never_falls_back(self):
        for value in ("", "31b2", "31b2:11", "hw:2,0", "31b2:0011:extra", "do-not-print-this-value"):
            with self.subTest(value=value), patch.dict(os.environ, {"USB_LOOM_MIC_USB_ID": value}), patch.object(
                    audio, "list_captures") as listing:
                selected, status = audio.select_mic()
                self.assertIsNone(selected)
                self.assertIn("invalid USB_LOOM_MIC_USB_ID", status)
                self.assertNotIn("do-not-print-this-value", status)
                listing.assert_not_called()

    def test_webcam_enabled_requires_explicit_pin(self):
        del os.environ["USB_LOOM_MIC_USB_ID"]
        selected, status = audio.select_mic([self.webcam, self.intel])
        self.assertIsNone(selected)
        self.assertIn("required", status)

    def test_legacy_selection_retained_only_without_camera_or_pin(self):
        del os.environ["USB_LOOM_MIC_USB_ID"]
        os.environ["USB_LOOM_CAMERA_ENABLED"] = "0"
        selected, _ = audio.select_mic([self.intel, self.condenser])
        self.assertEqual(selected.alsa, "hw:2,0")
        self.assertEqual(audio.pick_mic([self.intel]).alsa, "plughw:1,0")

    def test_lost_identity_metadata_does_not_open_by_card_number(self):
        for identity in (None, replace(self.identity, usb_id=("0C45", "6366"))):
            with patch.object(audio, "_usb_ids", return_value=("31B2", "0011")), patch.object(
                    audio, "capture_identity", return_value=identity):
                self.assertIsNone(audio.pick_mic([self.condenser]))

    def test_card_number_changes_but_native_alsa_target_does_not(self):
        self.ids[5] = self.ids[2]
        selected, _ = self.select([self.webcam, replace(self.condenser, card=5)])
        self.assertEqual(selected.alsa, "hw:CARD=Condenser,DEV=0")

    def test_inventory_uses_one_scan_and_exposes_only_selected_adapter(self):
        with patch.object(audio, "list_captures", return_value=[self.webcam, self.intel, self.condenser]) as listing, patch.object(
                audio, "_usb_ids", side_effect=lambda card: self.ids[card]), patch.object(
                audio, "capture_identity", return_value=self.identity):
            rows = audio.inventory()
        listing.assert_called_once()
        self.assertEqual([r["name"] for r in rows if "mic" in r["adapters"]], [self.condenser.name])
        self.assertEqual(rows[-1]["path"], "hw:CARD=Condenser,DEV=0")
        self.assertNotIn("generation", str(rows))

    def test_missing_source_does_not_launch_arecord_and_warning_is_not_spammed(self):
        sock = MagicMock()
        with patch.object(audio.socket, "socket", return_value=sock), patch.object(
                audio, "select_mic", return_value=(None, "pinned microphone unavailable")), patch.object(
                audio, "capture_pcm") as capture, patch.object(audio.time, "sleep", side_effect=[None, None, EndLoop]), patch(
                "builtins.print") as log:
            with self.assertRaises(EndLoop):audio.stream_mic(lambda _: {"dest_host": "192.0.2.20", "dest_port": 27183})
        capture.assert_not_called(); sock.sendto.assert_not_called()
        self.assertEqual(log.call_count, 1)

    def test_generation_change_before_open_never_starts_capture(self):
        chosen = replace(self.condenser, identity=self.identity)
        with patch.object(audio.socket, "socket"), patch.object(audio, "select_mic", return_value=(chosen, "pinned")), patch.object(
                audio, "capture_identity", return_value=None), patch.object(audio, "capture_pcm") as capture, patch.object(
                audio.time, "sleep", side_effect=EndLoop):
            with self.assertRaises(EndLoop):audio.stream_mic(lambda _: {"dest_host": "192.0.2.20", "dest_port": 27183})
        capture.assert_not_called()

    def test_generation_change_during_open_discards_pcm_before_forwarding(self):
        chosen = replace(self.condenser, identity=self.identity)
        proc = MagicMock(); proc.stdout.read.return_value = b"\x01\x00" * (audio.FRAME_BYTES // 2)
        sock = MagicMock()
        with patch.object(audio.socket, "socket", return_value=sock), patch.object(
                audio, "select_mic", return_value=(chosen, "pinned")), patch.object(
                audio, "capture_identity", side_effect=[self.identity, replace(self.identity, generation=("/synthetic/usb/1-5.4", "8"))]), patch.object(
                audio, "capture_pcm", return_value=proc) as capture, patch.object(audio.time, "sleep", side_effect=EndLoop):
            with self.assertRaises(EndLoop):audio.stream_mic(lambda _: {"dest_host": "192.0.2.20", "dest_port": 27183})
        capture.assert_called_once_with("hw:CARD=Condenser,DEV=0")
        sock.sendto.assert_not_called(); proc.kill.assert_called_once(); proc.stdout.close.assert_called_once()

    def test_pinned_forwarding_preserves_pcm_contract_and_route(self):
        chosen = replace(self.condenser, identity=self.identity)
        proc = MagicMock(); proc.stdout.read.side_effect = [b"\x01\x00" * (audio.FRAME_BYTES // 2), b""]
        sock = MagicMock()
        with patch.object(audio.socket, "socket", return_value=sock), patch.object(
                audio, "select_mic", return_value=(chosen, "pinned")), patch.object(
                audio, "capture_identity", return_value=self.identity), patch.object(
                audio, "capture_pcm", return_value=proc), patch.object(audio.time, "sleep", side_effect=EndLoop):
            with self.assertRaises(EndLoop):audio.stream_mic(lambda _: {"dest_host": "192.0.2.20", "dest_port": 27183})
        packet, dest = sock.sendto.call_args.args
        sequence, rate, channels, pcm = audio.decode_au10(packet)
        self.assertEqual((sequence, rate, channels, dest), (1, 44100, 1, ("192.0.2.20", 27183)))
        self.assertEqual(pcm, b"\x06\x00" * (audio.FRAME_BYTES // 2))

    def test_native_capture_arguments_unchanged(self):
        with patch.object(audio.subprocess, "Popen") as popen:
            audio.capture_pcm("hw:CARD=Condenser,DEV=0")
        command = popen.call_args.args[0]
        self.assertEqual(command, ["arecord", "-D", "hw:CARD=Condenser,DEV=0", "-f", "S16_LE",
                                   "-r", "44100", "-c", "1", "-t", "raw", "-q"])


class IdentityMetadataTests(unittest.TestCase):
    def test_usb_ancestor_and_stable_alsa_id_and_replug_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            usb = root / "usb/1-5.4"
            interface = usb / "1-5.4:1.0"
            interface.mkdir(parents=True)
            for name, value in {"idVendor": "31b2", "idProduct": "0011", "devnum": "7"}.items():
                (usb / name).write_text(value)
            sound = root / "sound"; (sound / "card2").mkdir(parents=True)
            (sound / "card2/device").symlink_to(interface)
            proc = root / "asound"; (proc / "card2").mkdir(parents=True)
            (proc / "card2/id").write_text("Condenser")
            with patch.object(audio, "SOUND_ROOT", sound), patch.object(audio, "ASOUND_ROOT", proc):
                before = audio.capture_identity(2)
                self.assertEqual(before.usb_id, ("31B2", "0011"))
                self.assertEqual(before.alsa_id, "Condenser")
                (usb / "devnum").write_text("8")
                self.assertNotEqual(before, audio.capture_identity(2))
                (proc / "card2/id").write_text("other,DEV=5")
                self.assertIsNone(audio.capture_identity(2))
                (proc / "card2/id").write_text("Condenser")
                (usb / "idProduct").unlink()
                self.assertIsNone(audio.capture_identity(2))

    def test_enumeration_timeout_fails_closed(self):
        with patch.object(audio.subprocess, "check_output", side_effect=audio.subprocess.TimeoutExpired("arecord", 5)):
            self.assertEqual(audio.list_captures(), [])
