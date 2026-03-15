from typing import Dict, List, Optional
from backend.utils.responses import success_response, error_response
from backend.utils.datetime import now_utc_iso

class FileService:
    def __init__(self, db):
        self.db = db

    def search_files(self, query: str = '', job_id: int = None, tape_barcode: str = None, extension: str = None, limit: int = 100, offset: int = 0) -> Dict:
        """Search archived files in the database."""
        results = self.db.search_archived_files(
            query=query,
            job_id=job_id,
            tape_barcode=tape_barcode,
            extension=extension
        )
        total = len(results)
        paged_results = results[offset:offset + limit]
        return {
            'files': paged_results,
            'total': total,
            'limit': limit,
            'offset': offset
        }

    def get_files_by_job(self, job_id: int) -> List[Dict]:
        """Get files associated with a specific job."""
        return self.db.get_files_by_job(job_id)

    def get_files_by_tape(self, barcode: str) -> List[Dict]:
        """Get files stored on a specific tape."""
        return self.db.get_files_by_tape(barcode)
