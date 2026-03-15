from flask import Blueprint, request, current_app
from backend.utils.responses import success_response, error_response
from backend.auth import require_role

restore_bp = Blueprint('restore', __name__)

@restore_bp.route('/api/restore', methods=['POST'])
@restore_bp.route('/api/restore/start', methods=['POST'])  # Alias for RestoreWizard
@require_role('operator')
def initiate_restore():
    """Initiate a restore operation."""
    restore_service = getattr(current_app, 'restore_service', None)
    if not restore_service:
        return error_response("Restore service unavailable", status_code=503)
        
    try:
        data = request.get_json() or {}
        success, result = restore_service.initiate_restore(data)
        if not success:
            return error_response(result.get("message"), code=result.get("code"), status_code=400)
        return success_response(data=result)
    except Exception as e:
        return error_response(str(e))

@restore_bp.route('/api/restore/jobs')
@require_role('viewer')
def get_restore_jobs():
    """Get all restore jobs."""
    restore_service = getattr(current_app, 'restore_service', None)
    if not restore_service:
        return error_response("Restore service unavailable", status_code=503)
        
    try:
        limit = request.args.get('limit', 50, type=int)
        results = restore_service.get_restore_jobs(limit=limit)
        return success_response(data={'jobs': results})
    except Exception as e:
        return error_response(str(e))

@restore_bp.route('/api/restore/jobs/<int:restore_id>')
@require_role('viewer')
def get_restore_job(restore_id):
    """Get details for a specific restore job."""
    restore_service = getattr(current_app, 'restore_service', None)
    if not restore_service:
        return error_response("Restore service unavailable", status_code=503)
        
    try:
        job = restore_service.get_restore_job(restore_id)
        if not job:
            return error_response('Restore job not found', code="not_found", status_code=404)
        return success_response(data={'job': job})
    except Exception as e:
        return error_response(str(e))

@restore_bp.route('/api/restore/<int:restore_id>/confirm-tape', methods=['POST'])
@require_role('operator')
def confirm_tape(restore_id):
    """Confirm a tape for a restore job."""
    restore_service = getattr(current_app, 'restore_service', None)
    if not restore_service:
        return error_response("Restore service unavailable", status_code=503)
        
    try:
        data = request.get_json() or {}
        barcode = data.get('tape_barcode') or data.get('barcode')
        if not barcode:
            return error_response("Barcode is required", status_code=400)
            
        success, message = restore_service.confirm_tape(restore_id, barcode)
        if success:
            return success_response(message=message)
        else:
            return error_response(message, status_code=400)
    except Exception as e:
        return error_response(str(e))

@restore_bp.route('/api/restore/verify-tape', methods=['POST'])
@require_role('operator')
def verify_tape():
    """Verify integrity of a physical tape."""
    restore_service = getattr(current_app, 'restore_service', None)
    if not restore_service:
        return error_response("Restore service unavailable", status_code=503)
        
    try:
        data = request.get_json() or {}
        barcode = data.get('barcode')
        password = data.get('encryption_password')
        
        if not barcode:
            return error_response("Barcode is required", status_code=400)
            
        results = restore_service.verify_backup_tape(barcode, encryption_password=password)
        return success_response(data=results)
    except Exception as e:
        return error_response(str(e))
