import os
import tempfile
import unittest
from datetime import datetime

from backend.database import Database


class TimestampTimezoneTests(unittest.TestCase):
    def test_log_timestamp_has_timezone(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = Database(db_path)
            db.add_log(level="info", message="timezone check")
            logs = db.get_logs(limit=1)
            self.assertTrue(logs)
            timestamp = logs[0]["timestamp"]
            parsed = datetime.fromisoformat(timestamp)
            self.assertIsNotNone(parsed.tzinfo)


if __name__ == "__main__":
    unittest.main()
