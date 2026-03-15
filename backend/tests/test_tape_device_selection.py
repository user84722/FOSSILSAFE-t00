import unittest
from unittest import mock

from backend.tape_device_selection import choose_devices_from_lsscsi_output


class TapeDeviceSelectionTests(unittest.TestCase):
    def test_chooses_mediumx_changer_over_tape_sg(self):
        lsscsi_output = (
            "[0:0:0:0] tape IBM      ULTRIUM-HH6     G9A2  /dev/st0  /dev/sg1\n"
            "[0:0:1:0] mediumx FUJITSU  ETERNUS LT S2 1.00  /dev/sg2\n"
        )

        with mock.patch("backend.tape.devices.os.path.exists", return_value=True):
            drive_path, changer_path, detected = choose_devices_from_lsscsi_output(lsscsi_output)

        self.assertEqual(drive_path, "/dev/nst0")
        self.assertEqual(changer_path, "/dev/sg2")
        self.assertEqual(detected["drives"][0]["path"], "/dev/nst0")
        self.assertEqual(detected["changers"][0]["path"], "/dev/sg2")

    def test_swapped_sg_devices_prefers_mediumx(self):
        lsscsi_output = (
            "[0:0:0:0] tape IBM      ULTRIUM-HH6     G9A2  /dev/st0  /dev/sg2\n"
            "[0:0:1:0] mediumx FUJITSU  ETERNUS LT S2 1.00  /dev/sg1\n"
        )

        with mock.patch("backend.tape.devices.os.path.exists", return_value=True):
            drive_path, changer_path, detected = choose_devices_from_lsscsi_output(lsscsi_output)

        self.assertEqual(drive_path, "/dev/nst0")
        self.assertEqual(changer_path, "/dev/sg1")
        self.assertEqual(detected["drives"][0]["sg_path"], "/dev/sg2")
        self.assertEqual(detected["changers"][0]["path"], "/dev/sg1")


if __name__ == '__main__':
    unittest.main()
