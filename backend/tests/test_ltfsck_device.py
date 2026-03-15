import unittest
from unittest import mock

from backend.tape_controller import TapeLibraryController
from backend.tape.runner import CommandResult


class LtfsckDeviceTests(unittest.TestCase):
    def test_verify_ltfs_uses_sg_device_for_ltfsck(self):
        command_runner = mock.Mock()
        command_runner.run.return_value = CommandResult(
            command=[],
            stdout="",
            stderr="",
            returncode=0,
            duration=0.1,
            timed_out=False,
        )
        controller = TapeLibraryController(
            device="/dev/nst0",
            changer="/dev/sg9",
            config={},
            state={},
            command_runner=command_runner,
        )
        controller.drive_sg = "/dev/fossilsafe-drive-sg"
        controller._set_mounted_tape(0, "TAPE1")
        controller._get_device = mock.Mock(return_value="/dev/fossilsafe-drive-nst")
        controller._is_ltfs_mounted = mock.Mock(return_value=True)
        controller.collect_ltfs_metadata = mock.Mock(
            return_value={"ltfs_formatted": True, "capacity_bytes": 0, "used_bytes": 0}
        )

        def _which(binary):
            return "/usr/bin/ltfsck" if binary == "ltfsck" else None

        with mock.patch("backend.tape_controller.shutil.which", side_effect=_which):
            with mock.patch("backend.tape_controller.os.path.exists", return_value=True):
                result = controller.verify_ltfs()

        self.assertTrue(result.get("ok"))
        command_runner.run.assert_called_with(
            ["/usr/bin/ltfsck", "/dev/fossilsafe-drive-sg"],
            name="ltfsck",
        )


if __name__ == "__main__":
    unittest.main()
