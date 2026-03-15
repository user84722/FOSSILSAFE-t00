from flask import Blueprint, current_app, request
from backend.utils.responses import success_response, error_response
from backend.auth import require_role

files_bp = Blueprint('files', __name__)

@files_bp.route('/api/files/search', methods=['POST'])
@require_role('viewer')
def search_files():
    """Search for files in the database."""
    file_service = getattr(current_app, 'file_service', None)
    if not file_service:
        return error_response("File service unavailable", status_code=503)
        
    try:
        data = request.get_json() or {}
        query = data.get('query', '').strip()
        job_id = data.get('job_id')
        tape_barcode = data.get('tape_barcode')
        extension = data.get('extension')
        limit = min(int(data.get('limit', 100)), 500)
        offset = int(data.get('offset', 0))
        
        result = file_service.search_files(
            query=query,
            job_id=job_id,
            tape_barcode=tape_barcode,
            extension=extension,
            limit=limit,
            offset=offset
        )
        
        # Normalize results for frontend
        files = result.get('files', [])
        for f in files:
            if 'file_name' in f and 'filename' not in f:
                f['filename'] = f['file_name']
            if 'file_size' in f and 'size' not in f:
                f['size'] = f['file_size']
            if 'archived_at' in f and 'created_at' not in f:
                f['created_at'] = f['archived_at']
                
        return success_response(data=result)
    except Exception as e:
        return error_response(str(e))

@files_bp.route('/api/files/by-job/<int:job_id>')
@require_role('viewer')
def get_job_files(job_id):
    """Get files for a specific job."""
    file_service = getattr(current_app, 'file_service', None)
    if not file_service:
        return error_response("File service unavailable", status_code=503)
        
    try:
        results = file_service.get_files_by_job(job_id)
        return success_response(data={'files': results, 'count': len(results)})
    except Exception as e:
        return error_response(str(e))

@files_bp.route('/api/files/by-tape/<barcode>')
@require_role('viewer')
def get_tape_files(barcode):
    """Get files for a specific tape."""
    file_service = getattr(current_app, 'file_service', None)
    if not file_service:
        return error_response("File service unavailable", status_code=503)
        
    try:
        results = file_service.get_files_by_tape(barcode)
        return success_response(data={'files': results, 'count': len(results)})
    except Exception as e:
        return error_response(str(e))
@files_bp.route('/api/files/estimate-size', methods=['POST'])
@require_role('viewer')
def estimate_path_size():
    """Estimate the size of a directory or file on disk."""
    import os
    try:
        data = request.get_json() or {}
        path = data.get('path')
        if not path:
            return error_response("Path is required", status_code=400)
            
        # Normalize browser paths
        if path.startswith('//browser/'):
            path = path.replace('//browser/', '/', 1)
            
        if not os.path.exists(path):
            return error_response("Path does not exist", status_code=404)
            
        total_size = 0
        file_count = 0
        
        if os.path.isfile(path):
            total_size = os.path.getsize(path)
            file_count = 1
        else:
            # For directories, we'll do a quick walk
            # Note: For very large shares, this might be slow, but it's "real-time" enough for small/medium
            # In a production app, we'd use a cached catalog or a faster recursive stat
            for dirpath, dirnames, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    # skip if it is a symbolic link
                    if not os.path.islink(fp):
                        total_size += os.path.getsize(fp)
                        file_count += 1
                # Optimization: limit walk depth or time if needed
                    
        return success_response(data={
            'path': path,
            'total_size': total_size,
            'file_count': file_count
        })
    except Exception as e:
        return error_response(str(e))
