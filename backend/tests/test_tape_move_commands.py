import unittest
from unittest import mock

from backend.tape_controller import TapeLibraryController
from backend.tape.runner import CommandResult


class TapeMoveCommandTests(unittest.TestCase):
    def test_move_tape_uses_transfer_command(self):
        controller = TapeLibraryController(device="/dev/nst0", changer="/dev/sg1", config={}, state={})
        controller.drive_only_mode = False
        result = CommandResult(command=[], stdout="", stderr="", returncode=0, duration=0.1, timed_out=False)

        with mock.patch.object(controller, "_resolve_mtx_path", return_value="mtx"):
            controller.command_runner.run = mock.Mock(return_value=result)
            controller.move_tape({"type": "slot", "value": 1}, {"type": "slot", "value": 2})

        commands = [call_args[0][0] for call_args in controller.command_runner.run.call_args_list]
        self.assertTrue(any("transfer" in cmd for cmd in commands))

    def test_move_tape_decrements_busy_on_exception(self):
        controller = TapeLibraryController(device="/dev/nst0", changer="/dev/sg1", config={}, state={})
        controller.drive_only_mode = False
        with mock.patch.object(controller, "_run_mtx_command", side_effect=Exception("boom")):
            with self.assertRaises(Exception):
                controller.move_tape({"type": "slot", "value": 1}, {"type": "slot", "value": 2})
        self.assertEqual(controller._busy_operations, 0)


if __name__ == "__main__":
    unittest.main()
