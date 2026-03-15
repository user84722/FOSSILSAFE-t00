import os
import tempfile
import unittest

from backend.smb_client import SMBClient


class SMBScanTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.base_path = self.tmpdir.name
        nested_dir = os.path.join(self.base_path, "nested")
        os.makedirs(nested_dir, exist_ok=True)
        with open(os.path.join(self.base_path, "file1.txt"), "wb") as handle:
            handle.write(b"abc")
        with open(os.path.join(nested_dir, "file2.bin"), "wb") as handle:
            handle.write(b"12345")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_scan_directory_counts_files_and_bytes(self):
        client = SMBClient()
        result = client.scan_directory(self.base_path, "", "", "")
        self.assertEqual(result["file_count"], 2)
        self.assertEqual(result["total_size"], 8)
        self.assertEqual(result["dir_count"], 1)
        self.assertFalse(result["partial"])
        self.assertGreaterEqual(result["duration_ms"], 0)

    def test_scan_directory_reports_partial_when_limited(self):
        client = SMBClient()
        result = client.scan_directory(self.base_path, "", "", "", scan_mode="quick", max_files=1)
        self.assertTrue(result["partial"])
        self.assertEqual(result["file_count"], 1)

    def test_scan_directory_warns_on_unreadable_paths(self):
        if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("Permission warnings are not reliable when running as root.")
        restricted_dir = os.path.join(self.base_path, "restricted")
        os.makedirs(restricted_dir, exist_ok=True)
        os.chmod(restricted_dir, 0)
        try:
            client = SMBClient()
            result = client.scan_directory(self.base_path, "", "", "", scan_mode="full")
            self.assertIsInstance(result.get("warnings"), list)
        finally:
            os.chmod(restricted_dir, 0o700)


if __name__ == "__main__":
    unittest.main()
