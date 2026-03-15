import tempfile
import unittest

from backend.database import Database


class JobLifecycleTests(unittest.TestCase):
    def test_job_status_transitions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test.db"
            db = Database(db_path=db_path)

            job_id = db.create_job(
                name="Test Job",
                source_id=None,
                tapes=["TAPE001"],
                verify=False,
                duplicate=False,
                scheduled_time=None,
                drive=0,
                job_type="library_load",
            )

            db.update_job_status(job_id, "queued", "Queued")
            job = db.get_job(job_id)
            self.assertEqual(job["status"], "queued")

            db.update_job_status(job_id, "running", "Running")
            job = db.get_job(job_id)
            self.assertEqual(job["status"], "running")
            self.assertIsNotNone(job["started_at"])

            db.update_job_status(job_id, "completed", "Completed")
            job = db.get_job(job_id)
            self.assertEqual(job["status"], "completed")
            self.assertIsNotNone(job["completed_at"])


if __name__ == "__main__":
    unittest.main()
