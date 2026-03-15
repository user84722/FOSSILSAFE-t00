import unittest
from unittest import mock

from backend.tape.devices import DeviceInfo, get_devices, parse_lsscsi_output


class TapeDeviceParseTests(unittest.TestCase):
    def test_parse_lsscsi_output_prefers_nst_only_if_exists(self):
        lsscsi_output = (
            "[0:0:0:0] tape HP Ultrium 5 /dev/st0 /dev/sg3\n"
        )

        def _fake_exists(path):
            if path == "/dev/nst0":
                return False
            if path == "/dev/st0":
                return True
            return False

        with mock.patch("backend.tape.devices.os.path.exists", side_effect=_fake_exists):
            drives, changers = parse_lsscsi_output(lsscsi_output)

        self.assertEqual(len(drives), 1)
        self.assertEqual(drives[0].path, "/dev/st0")
        self.assertEqual(drives[0].sg_path, "/dev/sg3")
        self.assertEqual(changers, [])

    def test_get_devices_prefers_medium_changer_over_drive_sg(self):
        drives = [DeviceInfo(path="/dev/nst0", sg_path="/dev/sg1", vendor="IBM", model="ULTRIUM-HH6")]
        changers = [DeviceInfo(path="/dev/sg2", vendor="FUJITSU", model="ETERNUS")]

        def _fake_exists(path):
            return path in {"/dev/nst0", "/dev/sg1", "/dev/sg2"}

        def _fake_scsi_type(path):
            return "8" if path == "/dev/sg2" else "1"

        with mock.patch("backend.tape.devices.discover_devices", return_value=(drives, changers)):
            with mock.patch("backend.tape.devices.os.path.exists", side_effect=_fake_exists):
                with mock.patch("backend.tape.devices._is_char_device", return_value=True):
                    with mock.patch("backend.tape.devices._scsi_generic_type", side_effect=_fake_scsi_type):
                        devices, health = get_devices(
                            config={"tape": {"drive_device": "/dev/nst0", "changer_device": "/dev/sg1"}},
                            state={},
                        )

        self.assertEqual(devices["drive_nst"], "/dev/nst0")
        self.assertEqual(devices["drive_sg"], "/dev/sg1")
        self.assertEqual(devices["changer_sg"], "/dev/sg2")
        self.assertEqual(health["status"], "ok")


if __name__ == "__main__":
    unittest.main()
