import unittest
from unittest import mock

from backend.tape_controller import TapeLibraryController
from backend.tape.runner import CommandResult


class TapeUnloadHomeSlotTests(unittest.TestCase):
    def test_unload_uses_home_slot_when_known(self):
        controller = TapeLibraryController(
            device="/dev/nst0",
            changer="/dev/sg1",
            config={},
            state={"tape": {"home_slots": {"0": {"slot": 5, "barcode": "TAPE1"}}}},
        )
        controller.drive_only_mode = False
        controller._set_mounted_tape(0, "TAPE1")
        controller._is_ltfs_mounted = mock.Mock(return_value=False)
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

        controller.unload_tape(drive=0)

        controller._run_mtx_command.assert_any_call(["unload", "0", "5"])
        self.assertNotIn(0, controller._home_slots)

    def test_unload_uses_slot_drive_order_when_configured(self):
        controller = TapeLibraryController(
            device="/dev/nst0",
            changer="/dev/sg1",
            config={"tape": {"mtx_unload_order": "slot_drive"}},
            state={"tape": {"home_slots": {"0": {"slot": 5, "barcode": "TAPE1"}}}},
        )
        controller.drive_only_mode = False
        controller._set_mounted_tape(0, "TAPE1")
        controller._is_ltfs_mounted = mock.Mock(return_value=False)
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

        controller.unload_tape(drive=0)

        controller._run_mtx_command.assert_any_call(["unload", "5", "0"])


if __name__ == "__main__":
    unittest.main()
