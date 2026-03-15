import unittest
from unittest import mock

from backend.tape_controller import LIBRARY_DEGRADED, TapeLibraryController
from backend.tape.runner import CommandResult


class TapeControllerParsingTests(unittest.TestCase):
    def setUp(self):
        self.controller = TapeLibraryController(device="/dev/nst0", changer="/dev/sg1", config={}, state={})
        self.controller.drive_only_mode = False

    def test_derive_tape_serial_from_barcode(self):
        self.assertEqual(self.controller._derive_tape_serial("XA0005L6"), "XA0005")
        self.assertEqual(self.controller._derive_tape_serial("000001"), "000001")

    def test_derive_tape_serial_invalid_barcode(self):
        with self.assertRaises(ValueError) as context:
            self.controller._derive_tape_serial("BAD")
        self.assertIn("Tape barcode must be 6 characters", str(context.exception))

    def test_parse_mtx_status_accepts_volume_tag_with_spaces(self):
        output = (
            "  Storage Element 1:Full :VolumeTag = ABC123\n"
            "  Data Transfer Element 0:Full :VolumeTag = XYZ789\n"
        )
        tapes = self.controller._parse_mtx_status(output)
        barcodes = {tape["barcode"] for tape in tapes}
        self.assertIn("ABC123", barcodes)
        self.assertIn("XYZ789", barcodes)
        drive_entry = next((t for t in tapes if t.get("location_type") == "drive"), None)
        self.assertIsNotNone(drive_entry)
        self.assertTrue(drive_entry["has_barcode"])

    def test_get_drive_status_accepts_volume_tag_with_spaces(self):
        output = "  Data Transfer Element 0:Full :VolumeTag = XYZ789\n"
        result = CommandResult(
            command=["mtx", "status"],
            stdout=output,
            stderr="",
            returncode=0,
            duration=0.1,
            timed_out=False,
        )
        with mock.patch.object(self.controller, "_run_mtx_command", return_value=result):
            status = self.controller.get_drive_status()
        self.assertEqual(status["loaded_tape"], "XYZ789")
        self.assertTrue(status["in_use"])
        self.assertIsNone(status["source_slot"])

    def test_parse_mtx_status_drive_loads_from_storage_element(self):
        output = "Data Transfer Element 0:Full (Storage Element 12 Loaded):VolumeTag = XA0005L6\n"
        tapes = self.controller._parse_mtx_status(output)
        drive = next((t for t in tapes if t.get("location_type") == "drive"), None)
        self.assertIsNotNone(drive)
        self.assertEqual(drive["barcode"], "XA0005L6")
        self.assertEqual(drive["drive_source_slot"], 12)
        self.assertEqual(drive["drive_index"], 0)
        self.assertTrue(drive["drive_full"])

    def test_parse_mtx_status_drive_loads_lowercase_loaded(self):
        output = "Data Transfer Element 0:Full (Storage Element 3 loaded):VolumeTag = XA0005L6\n"
        tapes = self.controller._parse_mtx_status(output)
        drive = next((t for t in tapes if t.get("location_type") == "drive"), None)
        self.assertIsNotNone(drive)
        self.assertEqual(drive["barcode"], "XA0005L6")
        self.assertEqual(drive["drive_source_slot"], 3)
        self.assertEqual(drive["drive_index"], 0)
        self.assertTrue(drive["drive_full"])

    def test_parse_mtx_status_drive_without_barcode(self):
        output = "Data Transfer Element 0:Full (Storage Element 5 Loaded)\n"
        tapes = self.controller._parse_mtx_status(output)
        drive = next((t for t in tapes if t.get("location_type") == "drive"), None)
        self.assertIsNotNone(drive)
        self.assertIsNone(drive["barcode"])
        self.assertFalse(drive["has_barcode"])
        self.assertEqual(drive["drive_source_slot"], 5)

    def test_parse_mtx_status_accepts_representative_vendor_formats(self):
        output = (
            "Storage Element 2:Full :VolumeTag=ABC123\n"
            "Storage Element 3:Full :VolumeTag = DEF456\n"
            "Data Transfer Element 0:Full (Storage Element 2 Loaded):VolumeTag=ABC123\n"
            "Data Transfer Element 1:Full (Storage Element 3 Loaded) :VolumeTag = DEF456\n"
        )
        tapes = self.controller._parse_mtx_status(output)
        barcodes = {tape["barcode"] for tape in tapes if tape.get("barcode")}
        self.assertTrue({"ABC123", "DEF456"}.issubset(barcodes))

    def test_get_drive_status_without_barcode(self):
        output = "  Data Transfer Element 0:Full (Storage Element 3 Loaded)\n"
        result = CommandResult(
            command=["mtx", "status"],
            stdout=output,
            stderr="",
            returncode=0,
            duration=0.1,
            timed_out=False,
        )
        with mock.patch.object(self.controller, "_run_mtx_command", return_value=result):
            status = self.controller.get_drive_status()
        self.assertIsNone(status["loaded_tape"])
        self.assertTrue(status["in_use"])
        self.assertEqual(status["source_slot"], 3)

    def test_scan_library_deep_runs_inventory_and_status(self):
        inventory_result = CommandResult(
            command=["mtx", "-f", "/dev/sg1", "inventory"],
            stdout="",
            stderr="",
            returncode=0,
            duration=0.2,
            timed_out=False,
        )
        status_result = CommandResult(
            command=["mtx", "-f", "/dev/sg1", "status"],
            stdout="Storage Element 1:Full :VolumeTag = ABC123\n",
            stderr="",
            returncode=0,
            duration=0.1,
            timed_out=False,
        )
        with mock.patch.object(
            self.controller,
            "_run_mtx_command",
            side_effect=[inventory_result, status_result],
        ) as runner:
            tapes = self.controller.scan_library(mode="deep")
        self.assertEqual(len(tapes), 1)
        runner.assert_has_calls([mock.call(["inventory"]), mock.call(["status"])])

    def test_scan_library_fast_runs_status_only(self):
        status_result = CommandResult(
            command=["mtx", "-f", "/dev/sg1", "status"],
            stdout="Storage Element 1:Full :VolumeTag = ABC123\n",
            stderr="",
            returncode=0,
            duration=0.1,
            timed_out=False,
        )
        with mock.patch.object(self.controller, "_run_mtx_command", return_value=status_result) as runner:
            tapes = self.controller.scan_library(mode="fast")
        self.assertEqual(len(tapes), 1)
        runner.assert_called_once_with(["status"])

    def test_load_tape_decrements_busy_on_exception(self):
        self.controller.scan_barcodes = mock.Mock(return_value=[{"barcode": "ABC123", "slot": 1}])
        with mock.patch.object(self.controller, "_run_mtx_command", side_effect=Exception("boom")):
            with self.assertRaises(Exception):
                self.controller.load_tape("ABC123", drive=0)
        self.assertEqual(self.controller._busy_operations, 0)
        self.assertEqual(self.controller._base_library_state, LIBRARY_DEGRADED)

    def test_load_tape_unloads_full_drive(self):
        self.controller.get_drive_status = mock.Mock(return_value={
            "in_use": True,
            "loaded_tape": "OLD123",
            "source_slot": 5,
            "drive_number": 0,
        })
        self.controller.unload_tape = mock.Mock(return_value=True)
        self.controller.scan_barcodes = mock.Mock(return_value=[{"barcode": "NEW123", "slot": 1}])
        self.controller._run_mtx_command = mock.Mock(
            return_value=CommandResult(
                command=[],
                stdout="",
                stderr="",
                returncode=0,
                duration=0.1,
                timed_out=False,
            )
        )

        self.controller.load_tape("NEW123", drive=0)

        self.controller.unload_tape.assert_called_once_with(0)

    def test_unload_tape_decrements_busy_on_exception(self):
        self.controller._set_mounted_tape(0, "ABC123")
        with mock.patch.object(self.controller, "_run_mtx_command", side_effect=Exception("boom")):
            with self.assertRaises(Exception):
                self.controller.unload_tape(drive=0)
        self.assertEqual(self.controller._busy_operations, 0)
        self.assertEqual(self.controller._base_library_state, LIBRARY_DEGRADED)


if __name__ == "__main__":
    unittest.main()
