
import os
import shutil
import uuid
import time
from pathlib import Path
from flask import Blueprint, request, current_app, jsonify
from werkzeug.utils import secure_filename

from backend.utils.responses import success_response, error_response
from backend.auth import require_role

uploads_bp = Blueprint('uploads', __name__)

# Temporary storage for upload sessions
# In a real production app, this might be backed by Redis or DB
# Here we use a simple in-memory dict + file system
_upload_sessions = {}

def _get_staging_path():
    """Get the persistent staging directory from config or default."""
    config = current_app.config.get('FOSSILSAFE_CONFIG', {})
    # Fallback to a folder in the data dir if not specified
    data_dir = current_app.config.get('DATA_DIR', '/var/lib/fossilsafe')
    return Path(config.get('staging_dir') or os.path.join(data_dir, 'staging'))

@uploads_bp.route('/api/uploads/session', methods=['POST'])
@require_role('operator')
def create_session():
    """Start a new upload session."""
    try:
        data = request.get_json() or {}
        filename = secure_filename(data.get('filename', 'dataset'))
        total_size = int(data.get('total_size', 0))
        
        group_id = data.get('group_id')
        if group_id:
            group_id = secure_filename(group_id)
        
        session_id = str(uuid.uuid4())
        staging_dir = _get_staging_path()
        
        # Check available disk space
        if not staging_dir.exists():
            staging_dir.mkdir(parents=True, exist_ok=True)
            
        total_usage = shutil.disk_usage(staging_dir)
        # Require actual size + 10% buffer
        required_space = total_size * 1.1
        
        if required_space > total_usage.free:
            return error_response(
                f"Insufficient staging space. Required: {required_space / (1024**3):.2f} GB, Available: {total_usage.free / (1024**3):.2f} GB",
                status_code=507 # Insufficient Storage
            )

        session_dir = staging_dir / "sessions" / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        
        _upload_sessions[session_id] = {
            'id': session_id,
            'filename': filename,
            'total_size': total_size,
            'uploaded_size': 0,
            'created_at': time.time(),
            'path': str(session_dir / filename),
            'chunks_received': 0,
            'group_id': group_id
        }
        
        return success_response(data={'session_id': session_id})
    except Exception as e:
        return error_response(str(e))

@uploads_bp.route('/api/uploads/chunk/<session_id>', methods=['POST'])
@require_role('operator')
def upload_chunk(session_id):
    """Append a chunk to the upload session."""
    session = _upload_sessions.get(session_id)
    if not session:
        return error_response("Invalid session", status_code=404)
        
    try:
        chunk = request.files.get('chunk')
        if not chunk:
            return error_response("No chunk data", status_code=400)
            
        offset = int(request.form.get('offset', -1))
        
        # Simple append-only for now. 
        # For robust resumable uploads, we'd handle random key offsets.
        with open(session['path'], 'ab') as f:
            if offset >= 0:
                f.seek(offset)
            # chunk.save(f) # This might not append correctly if using save() on an open file handle behavior?
            # Actually chunk.save() expects a path or file object. 
            # Better to read bytes.
            f.write(chunk.stream.read()) # Flask file storage
            
        # Re-opening in append binary mode to write bytes safely from stream
        # with open(session['path'], 'ab') as f:
        #      f.write(chunk.stream.read())

        session['uploaded_size'] = os.path.getsize(session['path'])
        session['chunks_received'] += 1
        
        return success_response(data={'uploaded_size': session['uploaded_size']})
    except Exception as e:
        return error_response(str(e))

@uploads_bp.route('/api/uploads/finalize/<session_id>', methods=['POST'])
@require_role('operator')
def finalize_session(session_id):
    """Verify upload and move to final staging area."""
    session = _upload_sessions.get(session_id)
    if not session:
        return error_response("Invalid session", status_code=404)
        
    try:
        # Move from session temp dir to main staging area
        staging_dir = _get_staging_path() / "ready"
        
        # If group_id present, use subdirectory
        if session.get('group_id'):
            staging_dir = staging_dir / session['group_id']
            
        staging_dir.mkdir(parents=True, exist_ok=True)
        
        src_path = Path(session['path'])
        dest_path = staging_dir / f"{session['filename']}"
        
        if src_path.exists():
            shutil.move(src_path, dest_path)
            
        # Cleanup session dir
        shutil.rmtree(src_path.parent, ignore_errors=True)
        del _upload_sessions[session_id]
        
        return success_response(data={
            'staging_path': str(dest_path),
            'staging_root': str(staging_dir),
            'filename': session['filename'],
            'size': os.path.getsize(dest_path)
        })
    except Exception as e:
        return error_response(str(e))

@uploads_bp.route('/api/uploads/session/<session_id>', methods=['DELETE'])
@require_role('operator')
def cancel_session(session_id):
    """Cancel and cleanup session."""
    session = _upload_sessions.get(session_id)
    if session:
        try:
            path = Path(session['path'])
            shutil.rmtree(path.parent, ignore_errors=True)
            del _upload_sessions[session_id]
        except Exception:
            pass
    return success_response(message="Session cancelled")
