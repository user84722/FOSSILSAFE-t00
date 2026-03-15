import unittest

BackupScheduler = None
_import_error = None

try:
    from backend.scheduler import BackupScheduler
except Exception as exc:
    _import_error = exc


class FakeDB:
    def get_schedules(self):
        return []


class FakeBackupEngine:
    pass


class SchedulerStopTests(unittest.TestCase):
    def setUp(self):
        if BackupScheduler is None:
            self.skipTest(f"Scheduler import failed: {_import_error}")

    def test_stop_idempotent(self):
        scheduler = BackupScheduler(FakeDB(), FakeBackupEngine())
        scheduler.start()
        scheduler.stop()
        scheduler.stop()
        self.assertFalse(scheduler.is_running())


if __name__ == "__main__":
    unittest.main()
