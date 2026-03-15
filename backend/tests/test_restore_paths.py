import os
import tempfile
import unittest

from backend.backup_engine import BackupEngine


class RestorePathSafetyTests(unittest.TestCase):
    def setUp(self):
        self.engine = BackupEngine(None, None, None, None)

    def test_restore_rejects_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                self.engine._resolve_restore_destination(tmpdir, "/etc/passwd")

    def test_restore_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                self.engine._resolve_restore_destination(tmpdir, "../secret.txt")

    def test_restore_within_destination(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = self.engine._resolve_restore_destination(tmpdir, "folder/file.txt")
            self.assertTrue(dest.startswith(os.path.realpath(tmpdir)))


if __name__ == '__main__':
    unittest.main()
