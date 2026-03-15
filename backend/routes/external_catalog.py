"""
External catalog backup API endpoints.
"""
from flask import Blueprint, request, current_app
from backend.utils.responses import success_response, error_response
from backend.auth import require_role
from backend.external_catalog_backup import ExternalCatalogBackup
import logging

logger = logging.getLogger(__name__)

external_catalog_bp = Blueprint('external_catalog', __name__)


@external_catalog_bp.route('/api/catalog/export', methods=['POST'])
@require_role('admin')
def export_catalog():
    """Export full catalog to external tape."""
    try:
        data = request.get_json() or {}
        tape_barcode = data.get('tape_barcode')
        
        if not tape_barcode:
            return error_response("tape_barcode is required", status_code=400)
        
        db = getattr(current_app, 'db', None)
        tape_controller = getattr(current_app, 'tape_controller', None)
        
        if not db or not tape_controller:
            return error_response("Services unavailable", status_code=503)
        
        backup_service = ExternalCatalogBackup(db, tape_controller)
        success, message = backup_service.write_catalog_to_tape(tape_barcode)
        
        if success:
            return success_response(message=message)
        else:
            return error_response(message, status_code=500)
        
    except Exception as e:
        logger.exception("Catalog export failed")
        return error_response(str(e))


@external_catalog_bp.route('/api/catalog/import', methods=['POST'])
@require_role('admin')
def import_external_catalog():
    """Import catalog from external tape."""
    try:
        data = request.get_json() or {}
        tape_barcode = data.get('tape_barcode')
        
        if not tape_barcode:
            return error_response("tape_barcode is required", status_code=400)
        
        db = getattr(current_app, 'db', None)
        tape_controller = getattr(current_app, 'tape_controller', None)
        
        if not db or not tape_controller:
            return error_response("Services unavailable", status_code=503)
        
        backup_service = ExternalCatalogBackup(db, tape_controller)
        
        # Read catalog from tape
        success, message, catalog_data = backup_service.restore_from_external_catalog(tape_barcode)
        if not success:
            return error_response(message, status_code=500)
        
        # Import to database
        success, import_message = backup_service.import_external_catalog(catalog_data)
        if success:
            return success_response(message=import_message, data={'catalog_info': {
                'backup_sets': len(catalog_data.get('backup_sets', [])),
                'total_files': catalog_data.get('total_files', 0),
                'export_date': catalog_data.get('export_date')
            }})
        else:
            return error_response(import_message, status_code=500)
        
    except Exception as e:
        logger.exception("Catalog import failed")
        return error_response(str(e))


@external_catalog_bp.route('/api/catalog/backups', methods=['GET'])
@require_role('viewer')
def list_external_backups():
    """List all external catalog backups."""
    try:
        db = getattr(current_app, 'db', None)
        if not db:
            return error_response("Database unavailable", status_code=503)
        
        backups = db.execute("""
            SELECT * FROM external_catalog_backups
            ORDER BY created_at DESC
            LIMIT 50
        """).fetchall()
        
        return success_response(data={'backups': [dict(b) for b in backups]})
        
    except Exception as e:
        logger.exception("Failed to list external backups")
        return error_response(str(e))
@external_catalog_bp.route('/api/catalog/sync', methods=['POST'])
@require_role('admin')
def sync_catalog():
    """Trigger manual cloud catalog synchronization."""
    try:
        db = getattr(current_app, 'db', None)
        tape_controller = getattr(current_app, 'tape_controller', None)
        
        if not db or not tape_controller:
            return error_response("Services unavailable", status_code=503)
            
        backup_service = ExternalCatalogBackup(db, tape_controller)
        success, message, results = backup_service.sync_without_tape()
        
        if success:
            return success_response(message=message, data={'results': results})
        else:
            return error_response(message, status_code=500)
            
    except Exception as e:
        logger.exception("Cloud sync failed")
        return error_response(str(e))
