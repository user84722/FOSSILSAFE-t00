import unittest
from unittest import mock

from backend.tape_controller import TapeLibraryController
from backend.tape.runner import CommandResult


class TapeMoveTests(unittest.TestCase):
    def test_drive_to_slot_unload_argument_order(self):
        controller = TapeLibraryController(
            device="/dev/nst0",
            changer="/dev/sg1",
            config={},
            state={},
        )
        controller.drive_only_mode = False
        controller._refresh_inventory_cache = mock.Mock()
        controller._run_mtx_command = mock.Mock(
            return_value=CommandResult(
                command=[],
                stdout="",
                stderr="",
                returncode=0,
                duration=0.1,
                timed_out=False,
            )
        )

        controller.move_tape(
            source={"type": "drive", "value": 0},
            destination={"type": "slot", "value": 3},
            barcode=None,
        )

        controller._run_mtx_command.assert_called_once_with(["unload", "0", "3"])

    def test_drive_to_slot_unload_argument_order_slot_drive(self):
        controller = TapeLibraryController(
            device="/dev/nst0",
            changer="/dev/sg1",
            config={"tape": {"mtx_unload_order": "slot_drive"}},
            state={},
        )
        controller.drive_only_mode = False
        controller._refresh_inventory_cache = mock.Mock()
        controller._run_mtx_command = mock.Mock(
            return_value=CommandResult(
                command=[],
                stdout="",
                stderr="",
                returncode=0,
                duration=0.1,
                timed_out=False,
            )
        )

        controller.move_tape(
            source={"type": "drive", "value": 0},
            destination={"type": "slot", "value": 3},
            barcode=None,
        )

        controller._run_mtx_command.assert_called_once_with(["unload", "3", "0"])


if __name__ == "__main__":
    unittest.main()
