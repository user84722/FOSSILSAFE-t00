import unittest
import os
import tempfile
import sqlite3
from backend.database import Database
from backend.services.metrics_service import MetricsService

class TestMetricsServiceFix(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        os.close(self.db_fd)
        self.db = Database(self.db_path)
        
        # Ensure archived_files table exists (it should be created by Database init)
        # Insert sample data
        self.db.execute("""
            INSERT INTO archived_files 
            (tape_barcode, file_path, file_size)
            VALUES 
            ('TAPE1', '/path/to/file1', 100),
            ('TAPE1', '/path/to/file2', 200),
            (NULL, '/path/to/pending', 50)
        """)
        self.db.commit()
        
        self.metrics_service = MetricsService(self.db)

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    def test_get_data_written_bytes(self):
        """Verify _get_data_written_bytes sums file_size from archived_files correctly."""
        # Expected: 100 + 200 = 300. The file with NULL tape_barcode should be ignored based on WHERE clause.
        total_bytes = self.metrics_service._get_data_written_bytes()
        self.assertEqual(total_bytes, 300)

if __name__ == '__main__':
    unittest.main()
