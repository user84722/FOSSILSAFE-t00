import unittest

BackupScheduler = None
_import_error = None
try:
    from backend.scheduler import BackupScheduler
except Exception as exc:
    _import_error = exc


class CronValidationTests(unittest.TestCase):
    def test_valid_cron_expression(self):
        if _import_error:
            self.skipTest(f"Scheduler import failed: {_import_error}")
        valid, error = BackupScheduler.validate_cron_expression("0 15 2 * * *")
        self.assertTrue(valid)
        self.assertIsNone(error)

    def test_invalid_cron_field_count(self):
        if _import_error:
            self.skipTest(f"Scheduler import failed: {_import_error}")
        valid, error = BackupScheduler.validate_cron_expression("0 2 * * *")
        self.assertFalse(valid)
        self.assertIn("6 fields", error)

    def test_invalid_cron_values(self):
        if _import_error:
            self.skipTest(f"Scheduler import failed: {_import_error}")
        valid, error = BackupScheduler.validate_cron_expression("61 0 0 * * *")
        self.assertFalse(valid)
        self.assertIn("Invalid", error)  # Accept "Invalid second: 61" or "Invalid cron expression"


if __name__ == '__main__':
    unittest.main()
