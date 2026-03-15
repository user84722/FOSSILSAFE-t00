import unittest
import os
import tempfile
import sqlite3
from backend.database import Database

class TestExternalCatalogDB(unittest.TestCase):
    def setUp(self):
        # Create a temporary database file
        self.db_fd, self.db_path = tempfile.mkstemp()
        os.close(self.db_fd)
        self.db = Database(self.db_path)

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    def test_table_exists(self):
        """Verify the external_catalog_backups table is created."""
        conn = self.db._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='external_catalog_backups'")
        self.assertIsNotNone(cursor.fetchone())

    def test_insert_and_retrieve(self):
        """Verify we can insert and retrieve backup records."""
        # Insert
        self.db.execute("""
            INSERT INTO external_catalog_backups 
            (tape_barcode, created_at, backup_sets_count, files_count, bytes_total)
            VALUES (?, ?, ?, ?, ?)
        """, ('TEST001L8', '2023-01-01T12:00:00', 5, 1000, 1024000))
        self.db.commit()

        # Retrieve
        row = self.db.execute("SELECT * FROM external_catalog_backups WHERE tape_barcode = ?", ('TEST001L8',)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['tape_barcode'], 'TEST001L8')
        self.assertEqual(row['backup_sets_count'], 5)
        self.assertEqual(row['files_count'], 1000)
        self.assertEqual(row['bytes_total'], 1024000)

if __name__ == '__main__':
    unittest.main()
