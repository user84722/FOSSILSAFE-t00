from flask import Blueprint, current_app, request, jsonify
from backend.utils.responses import success_response, error_response
from backend.auth import require_role
from backend.database import Database
from backend.smb_client import SMBClient
from backend.sources.rclone_source import RcloneSource
import json
import logging
import os

sources_bp = Blueprint('sources', __name__)
logger = logging.getLogger(__name__)

def get_db():
    return current_app.db

@sources_bp.route('/api/sources', methods=['GET'])
@require_role('viewer')
def get_sources():
    """List all configured sources."""
    try:
        source_manager = getattr(current_app, 'source_manager', None)
        if not source_manager:
            return error_response("Source manager unavailable", status_code=503)
        
        sources = source_manager.list_sources()
        # Ensure consistency with frontend expectation
        for s in sources:
            s['name'] = s.get('display_name', s.get('id', ''))
            s['type'] = s.get('source_type', '')
            # Reconstruct config object for frontend
            s['config'] = {
                'path': s.get('source_path', ''),
                'username': s.get('username', ''),
                'domain': s.get('domain', ''),
                'nfs_server': s.get('nfs_server', ''),
                'nfs_export': s.get('nfs_export', ''),
                'bucket': s.get('s3_bucket', ''),
                'region': s.get('s3_region', '')
            }
        return success_response(data={'sources': sources})
    except Exception as e:
        logger.error(f"Failed to list sources: {e}")
        return error_response("Failed to retrieve sources list")

@sources_bp.route('/api/sources', methods=['POST'])
@require_role('admin')
def create_source():
    """Create a new source configuration."""
    data = request.get_json() or {}
    name = data.get('name')
    source_type = data.get('type')
    config = data.get('config', {})

    if not name or not source_type:
        return error_response("Name and type are required", status_code=400)

    # Validate connection before saving
    if os.environ.get("FOSSILSAFE_SKIP_SOURCE_TEST") == "1":
        pass
    elif source_type == 'smb':
        smb = SMBClient()
        success = smb.connect(
            config.get('path'),
            config.get('username'),
            config.get('password'),
            config.get('domain')
        )
        if not success:
            return error_response("Failed to connect to SMB share", status_code=400)

    elif source_type in ['s3', 'b2']:
        success, message = RcloneSource.test_connection_with_credentials(source_type, config)
        if not success:
            return error_response(f"Failed to validate {source_type.upper()} connection: {message}", status_code=400)

    elif source_type == 'nfs':
        from backend.sources.nfs_source import NFSSource
        res = NFSSource.test_connection(config.get('nfs_server'), config.get('nfs_export'))
        if not res['ok']:
             return error_response(f"Failed to validate NFS connection: {res['detail']}", status_code=400)

    elif source_type == 'ssh':
        from backend.sources.ssh_source import SSHSource
        success, message = SSHSource.test_connection(
            config.get('host'),
            config.get('username'),
            int(config.get('port', 22))
        )
        if not success:
            return error_response(message, status_code=400)

    elif source_type == 'rsync':
        from backend.sources.rsync_source import RsyncSource
        success, message = RsyncSource.test_connection(
            config.get('host'),
            config.get('username'),
            int(config.get('port', 22))
        )
        if not success:
            return error_response(message, status_code=400)

    try:
        source_manager = getattr(current_app, 'source_manager', None)
        if not source_manager:
            return error_response("Source manager unavailable", status_code=503)

        # Map to Database schema
        source_id = data.get('source_id') or name.lower().replace(' ', '_')
        payload = {
            'id': source_id,
            'source_type': source_type,
            'source_path': config.get('path', ''),
            'display_name': name,
            'username': config.get('username', ''),
            'password': config.get('password', ''),
            'domain': config.get('domain', ''),
            'nfs_server': config.get('nfs_server', ''),
            'nfs_export': config.get('nfs_export', ''),
            's3_bucket': config.get('bucket', ''),
            's3_region': config.get('region', ''),
            'host': config.get('host', ''),
            'port': config.get('port', 22)
        }
        
        source_manager.store_source(payload)
        return success_response(message="Source created successfully", data={'source_id': source_id})
    except Exception as e:
        logger.exception("Failed to create source")
        # Do not return str(e) as it may contain credentials from the payload
        return error_response("Failed to create source configuration. Check logs for details.")

@sources_bp.route('/api/sources/<source_id>', methods=['DELETE'])
@require_role('admin')
def delete_source(source_id: str):
    """Delete a source configuration."""
    try:
        db = get_db()
        db.delete_source(source_id)
        return success_response(message="Source deleted")
    except Exception as e:
        logger.error(f"Failed to delete source {source_id}: {e}")
        return error_response("Failed to delete source")

@sources_bp.route('/api/sources/test', methods=['POST'])
@require_role('admin')
def test_source_connection():
    """Test connection for a source configuration (unsaved)."""
    data = request.get_json() or {}
    source_type = data.get('type')
    config = data.get('config', {})

    if source_type == 'smb':
        smb = SMBClient()
        res = smb.test_connection_detailed(
             config.get('path'),
            config.get('username'),
            config.get('password'),
            config.get('domain')
        )
        if res['ok']:
            return success_response(message="Connection successful")
        else:
            return error_response(res['message'], detail=res.get('detail'))
            
    elif source_type in ['s3', 'b2']:
        success, message = RcloneSource.test_connection_with_credentials(source_type, config)
        if success:
            return success_response(message="Connection successful")
        else:
            return error_response(f"Connection failed: {message}")

    elif source_type == 'nfs':
        from backend.sources.nfs_source import NFSSource
        res = NFSSource.test_connection(config.get('nfs_server'), config.get('nfs_export'))
        if res['ok']:
            return success_response(message="Connection successful")
        else:
            return error_response(res['detail'])

    elif source_type == 'ssh':
        from backend.sources.ssh_source import SSHSource
        success, message = SSHSource.test_connection(
            config.get('host'),
            config.get('username'),
            int(config.get('port', 22))
        )
        if success:
            return success_response(message="Connection successful")
        else:
            return error_response(message)

    elif source_type == 'rsync':
        from backend.sources.rsync_source import RsyncSource
        success, message = RsyncSource.test_connection(
            config.get('host'),
            config.get('username'),
            int(config.get('port', 22))
        )
        if success:
            return success_response(message="Connection successful")
        else:
            return error_response(message)

    return error_response("Unsupported source type")
