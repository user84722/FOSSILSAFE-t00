import unittest
from unittest import mock
from backend.tape_controller import TapeLibraryController, TapeLoadError
from backend.tape.runner import CommandResult

class TapeControllerDriveOnlyTests(unittest.TestCase):
    def setUp(self):
        self.controller = TapeLibraryController(device="/dev/nst0", changer=None, config={}, state={})
        self.controller.drive_only_mode = True
        # Mock logging to avoid clutter
        self.controller._log_event = mock.Mock()

    def test_load_tape_verifies_barcode_match(self):
        # Setup: tapeinfo returns matching barcode
        with mock.patch.object(self.controller, "_get_drive_barcode", return_value="TAPE123"):
            with mock.patch.object(self.controller, "get_drive_status", return_value={}):
                self.controller.load_tape("TAPE123")
        
        self.assertEqual(self.controller.manual_tape_barcode, "TAPE123")
        self.controller._log_event.assert_any_call("info", "Barcode verified: TAPE123")

    def test_load_tape_raises_on_barcode_mismatch(self):
        # Setup: tapeinfo returns DIFFERENT barcode
        with mock.patch.object(self.controller, "_get_drive_barcode", return_value="WRONG123"):
            with mock.patch.object(self.controller, "get_drive_status", return_value={}):
                with self.assertRaises(TapeLoadError) as ctx:
                    self.controller.load_tape("RIGHT123")
        
        self.assertIn("Barcode mismatch", str(ctx.exception))
        self.assertIn("WRONG123", str(ctx.exception))
        self.assertIn("RIGHT123", str(ctx.exception))

    def test_load_tape_trusts_user_if_hardware_read_fails(self):
        # Setup: tapeinfo returns None (cannot read MAM)
        with mock.patch.object(self.controller, "_get_drive_barcode", return_value=None):
            with mock.patch.object(self.controller, "get_drive_status", return_value={}):
                self.controller.load_tape("TAPE123")
        
        self.assertEqual(self.controller.manual_tape_barcode, "TAPE123")
        self.controller._log_event.assert_any_call("warning", "Could not verify barcode from hardware (MAM empty or drive incorrect). trusting user input.")

    def test_get_drive_barcode_parses_tapeinfo(self):
        # Test the parsing logic specifically
        stdout = "Product Type: LTO-6\nSerial Number: 123456\nBarcode: TAPE123   \n"
        result = CommandResult(
            command=["tapeinfo"],
            stdout=stdout,
            stderr="",
            returncode=0,
            duration=0.1,
            timed_out=False
        )
        
        with mock.patch.object(self.controller.command_runner, "run", return_value=result):
            barcode = self.controller._get_drive_barcode("/dev/nst0")
        
        self.assertEqual(barcode, "TAPE123")
    
    def test_get_drive_barcode_returns_none_on_error(self):
        result = CommandResult(
            command=["tapeinfo"],
            stdout="",
            stderr="Error",
            returncode=1,
            duration=0.1,
            timed_out=False
        )
        
        with mock.patch.object(self.controller.command_runner, "run", return_value=result):
            barcode = self.controller._get_drive_barcode("/dev/nst0")
        
        self.assertIsNone(barcode)

if __name__ == "__main__":
    unittest.main()
