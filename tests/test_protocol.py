"""Byte-for-byte regression against an independently emulated Windows encoder."""

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from sc830 import protocol as p
from sc830.device import Mouse
from sc830.profiles import compile_profile, default_profile

FIXTURES = json.loads((Path(__file__).parent / "original-vectors.json").read_text())


class OriginalProgramTests(unittest.TestCase):
    def test_dpi_table_matches_original(self):
        self.assertEqual(list(p.DPI_VALUES), FIXTURES["dpi_values"])

    def test_original_packets(self):
        for vector in FIXTURES["vectors"]:
            kind, inputs = vector["kind"], vector["input"]
            with self.subTest(kind=kind, case=FIXTURES["vectors"].index(vector)):
                if kind == "dpi":
                    commands = [p.dpi_command(**inputs)]
                elif kind == "dpi_colors":
                    commands = [p.dpi_colors_command(**inputs)]
                elif kind == "lighting":
                    commands = [p.lighting_command(**inputs)]
                elif kind == "polling":
                    commands = [p.polling_command(**inputs)]
                elif kind == "buttons":
                    # Windows sends in hardware order, our API accepts visual order.
                    commands = [p.button_command(i, bytes.fromhex(inputs["actions"][i]))
                                for i in (0, 1, 2, 4, 3, 5)]
                elif kind == "macro":
                    commands = p.macro_commands(**inputs)
                else:
                    self.fail(f"Unknown fixture kind {kind}")
                self.assertEqual([c.packet(i).hex() for i, c in enumerate(commands)], vector["packets"])

    def test_original_action_tables(self):
        self.assertEqual(p.shortcut("ctrl+c").hex(), "700106")
        self.assertEqual(p.action_bytes("media:player").hex(), "808301")
        self.assertEqual(p.action_bytes("media:volume-up").hex(), "80e900")
        self.assertEqual(p.action_bytes("fire:70:3").hex(), "304603")

    def test_macro_loop_encoding(self):
        self.assertEqual(p.macro_action(3, "release"), bytes.fromhex("900300"))
        self.assertEqual(p.macro_action(3, "press"), bytes.fromhex("901300"))
        self.assertEqual(p.macro_action(3, "count", 4), bytes.fromhex("902304"))


class ValidationTests(unittest.TestCase):
    def test_disabled_active_dpi_rejected(self):
        with self.assertRaises(ValueError):
            p.dpi_command([800, 1200], 1, [True, False])

    def test_invalid_dpi_is_not_rounded(self):
        for value in (0, 5000, 9700, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                p.dpi_command([value])

    def test_left_button_preserved(self):
        profile = default_profile()
        profile["buttons"][0] = "disabled"
        with self.assertRaisesRegex(ValueError, "左键"):
            compile_profile(profile)

    def test_macro_uploaded_before_button_binding(self):
        profile = default_profile()
        profile["macros"]["2"] = {"events": [{"type": "key", "key": "a"}], "loop": "count", "repeat": 2}
        profile["buttons"][3] = {"macro": 2}
        commands = compile_profile(profile, {"buttons"})
        self.assertEqual(commands[0].code, 0x0E)
        self.assertEqual(commands[4].payload, bytes.fromhex("04902202"))

    def test_reject_bad_macro(self):
        for events in ([], [{"type": "delay", "ms": 10}], [{"type": "key", "key": "a", "delay_ms": 5000}],
                       [{"type": "move_x", "value": 128}], [{"type": "key", "key": "a"}] * 126):
            with self.subTest(events=events[:1]), self.assertRaises(ValueError):
                p.macro_events(events)

    def test_protocol_sequence_wrap_is_validated(self):
        with self.assertRaises(ValueError):
            p.polling_command(500).packet(256)

    def test_partial_apply_does_not_include_unselected_settings(self):
        commands = compile_profile(default_profile(), {"lighting"})
        self.assertEqual([c.code for c in commands], [8])


class StatusTests(unittest.TestCase):
    RESPONSE = bytes.fromhex("0c012000020110004d3630312501001225ff5e00ff010000866ed6cbfff46f2d39")

    def test_real_device_response(self):
        result = p.parse_status(self.RESPONSE, 2)
        self.assertEqual(result["sensor"], "PAW3212")
        self.assertEqual(result["battery_percent"], 94)
        self.assertTrue(result["online"])

    def test_empty_or_stale_status_rejected(self):
        for data, seq in ((self.RESPONSE, 1), (bytes.fromhex("0c012000000000") + bytes(26), 0)):
            with self.assertRaises(ValueError):
                p.parse_status(data, seq)

    def test_initial_empty_response_retried(self):
        mouse = object.__new__(Mouse)
        mouse.path = "/dev/hidraw-test"
        mouse.fd, mouse.sequence = 1, 0
        replies = [bytes.fromhex("0c012000000000") + bytes(26), self.RESPONSE]
        def ioctl(fd, command, data, *args):
            if command & 255 == 7:
                data[:] = replies.pop(0)
                return 33
            return 33
        with patch("sc830.device.fcntl.ioctl", side_effect=ioctl), patch("sc830.device.time.sleep"):
            # Second request has sequence 1; adjust reply to simulate the matching firmware response.
            response = bytearray(self.RESPONSE); response[4] = 1
            replies[1] = bytes(response)
            self.assertEqual(mouse.status()["battery_percent"], 94)


if __name__ == "__main__":
    unittest.main()
