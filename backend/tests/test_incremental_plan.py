import unittest

from backend.backup_engine import (
    compute_incremental_plan,
    PLAN_REASON_CHANGED,
    PLAN_REASON_MISSING,
    PLAN_REASON_SKIPPED_PRESENT,
    PLAN_REASON_SKIPPED_UNCHANGED,
)


class IncrementalPlanTests(unittest.TestCase):
    def test_unchanged_file_skipped(self):
        files = [{"path": "docs/report.txt", "size": 100, "checksum": "hash1"}]
        last_snapshot = {"docs/report.txt": "hash1"}
        catalog_index = {"hash1": ["TAPE001"]}
        plan = compute_incremental_plan(files, last_snapshot, catalog_index, ["TAPE001"])
        self.assertEqual(len(plan["to_backup"]), 0)
        self.assertEqual(plan["skipped"][0]["reason"], PLAN_REASON_SKIPPED_UNCHANGED)

    def test_changed_hash_included(self):
        files = [{"path": "docs/report.txt", "size": 100, "checksum": "hash2"}]
        last_snapshot = {"docs/report.txt": "hash1"}
        catalog_index = {"hash1": ["TAPE001"]}
        plan = compute_incremental_plan(files, last_snapshot, catalog_index, ["TAPE001"])
        self.assertEqual(len(plan["to_backup"]), 1)
        self.assertEqual(plan["to_backup"][0]["reason"], PLAN_REASON_CHANGED)

    def test_renamed_file_same_hash_skipped(self):
        files = [{"path": "docs/renamed.txt", "size": 100, "checksum": "hash1"}]
        last_snapshot = {"docs/report.txt": "hash1"}
        catalog_index = {"hash1": ["TAPE001"]}
        plan = compute_incremental_plan(files, last_snapshot, catalog_index, ["TAPE001"])
        self.assertEqual(len(plan["to_backup"]), 0)
        self.assertEqual(plan["skipped"][0]["reason"], PLAN_REASON_SKIPPED_PRESENT)

    def test_missing_on_tape_forces_backup(self):
        files = [{"path": "docs/report.txt", "size": 100, "checksum": "hash1"}]
        last_snapshot = {"docs/report.txt": "hash1"}
        catalog_index = {"hash1": ["TAPE001"]}
        plan = compute_incremental_plan(files, last_snapshot, catalog_index, [])
        self.assertEqual(len(plan["to_backup"]), 1)
        self.assertEqual(plan["to_backup"][0]["reason"], PLAN_REASON_MISSING)


if __name__ == "__main__":
    unittest.main()
