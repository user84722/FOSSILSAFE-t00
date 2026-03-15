import unittest
import os
import tempfile
import json
import sqlite3
from backend.database import Database
from backend.external_catalog_backup import ExternalCatalogBackup

from backend.tape.simulators import MockTapeController

class TestSchemaAlignment(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        os.close(self.db_fd)
        self.db = Database(self.db_path)
        self.tape_controller = MockTapeController()
        self.service = ExternalCatalogBackup(self.db, self.tape_controller)

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    def test_schema_columns_exist(self):
        """Verify columns used by ExternalCatalogBackup exist in database."""
        # Check archived_files columns
        cursor = self.db.execute("PRAGMA table_info(archived_files)")
        columns = {row['name'] for row in cursor.fetchall()}
        self.assertIn('file_mtime', columns)
        self.assertIn('backup_set_id', columns)
        
        # Check backup_sets columns
        cursor = self.db.execute("PRAGMA table_info(backup_sets)")
        columns = {row['name'] for row in cursor.fetchall()}
        self.assertIn('id', columns)
        self.assertIn('sources', columns)
        self.assertIn('created_at', columns)

    def test_backup_set_operations(self):
        """Verify we can insert and retrieve backup sets using the corrected queries."""
        # Insert using the logic similar to import_external_catalog
        backup_set_id = "SET_001"
        sources = ["smb://source"]
        created_at = "2023-01-01T00:00:00"
        
        # Test INSERT
        self.db.execute("""
            INSERT OR IGNORE INTO backup_sets (id, sources, created_at)
            VALUES (?, ?, ?)
        """, (backup_set_id, json.dumps(sources), created_at))
        self.db.commit()
        
        # Test SELECT (logic from create_full_catalog_export)
        rows = self.db.execute("""
            SELECT id as backup_set_id, sources, created_at
            FROM backup_sets
            ORDER BY created_at DESC
        """).fetchall()
        
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['backup_set_id'], backup_set_id)

    def test_archived_files_extended_columns(self):
        """Verify we can insert into archived_files with new columns."""
        self.db.execute("""
            INSERT INTO archived_files 
            (file_path, file_size, file_mtime, checksum, tape_barcode, backup_set_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("/path/file", 100, "2023-01-01T12:00:00", "sha1", "TAPE1", "SET_001"))
        self.db.commit()
        
        row = self.db.execute("SELECT * FROM archived_files").fetchone()
        self.assertEqual(row['file_mtime'], "2023-01-01T12:00:00")
        self.assertEqual(row['backup_set_id'], "SET_001")

if __name__ == '__main__':
    unittest.main()
