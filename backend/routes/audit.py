"""
Audit log routes for enterprise compliance.
Provides access to immutable audit trail with verification.
"""
from flask import Blueprint, request, g, current_app
from backend.utils.responses import success_response, error_response
from backend.auth import require_role

audit_bp = Blueprint('audit', __name__)


@audit_bp.route('/api/audit', methods=['GET'])
@require_role('admin')
def get_audit_log():
    """
    Get audit log entries with pagination.
    Admin only - sensitive operation history.
    """
    db = current_app.config.get('db')
    if not db:
        return error_response("Database not available", status_code=503)
    
    try:
        limit = min(int(request.args.get('limit', 100)), 1000)
        offset = int(request.args.get('offset', 0))
    except (ValueError, TypeError):
        limit = 100
        offset = 0
    
    try:
        entries = db.get_audit_log(limit=limit, offset=offset)
        return success_response(data={
            'entries': entries,
            'limit': limit,
            'offset': offset
        })
    except Exception as e:
        return error_response(f"Failed to retrieve audit log: {str(e)}")


@audit_bp.route('/api/audit/verify', methods=['GET'])
@require_role('admin')
def verify_audit_chain():
    """
    Verify the integrity of the audit log hash chain.
    Detects if any entries have been tampered with.
    """
    db = current_app.config.get('db')
    if not db:
        return error_response("Database not available", status_code=503)
    
    try:
        result = db.verify_audit_chain()
        # Persist verification result
        db.save_audit_verification_result(result)
        
        # Trigger Compliance Alarm if tampering is detected
        if not result.get('valid', False):
            webhook_service = current_app.config.get('webhook_service')
            if webhook_service:
                webhook_service.trigger_event("COMPLIANCE_ALARM", {
                    "reason": "AUDIT_LOG_TAMPERING",
                    "details": result.get('error_message', 'Chain verification failed'),
                    "first_invalid_id": result.get('first_invalid_id')
                })
        
        return success_response(data=result)
    except Exception as e:
        return error_response(f"Failed to verify audit chain: {str(e)}")


@audit_bp.route('/api/audit/verification-history', methods=['GET'])
@require_role('admin')
def get_audit_verification_history():
    """Get historical audit verification results."""
    db = current_app.config.get('db')
    if not db:
        return error_response("Database not available", status_code=503)
        
    try:
        limit = min(int(request.args.get('limit', 50)), 100)
        history = db.get_audit_verification_history(limit=limit)
        return success_response(data={'history': history})
    except Exception as e:
        return error_response(f"Failed to retrieve verification history: {str(e)}")


@audit_bp.route('/api/audit/export', methods=['GET'])
@require_role('admin')
def export_audit_log():
    """
    Export audit log with verification signature.
    For compliance and external auditing.
    """
    db = current_app.config.get('db')
    if not db:
        return error_response("Database not available", status_code=503)
    
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    try:
        export_data = db.export_audit_log(start_date=start_date, end_date=end_date)
        return success_response(data=export_data)
    except Exception as e:
        return error_response(f"Failed to export audit log: {str(e)}")


@audit_bp.route('/api/audit/stats', methods=['GET'])
@require_role('admin')
def get_audit_stats():
    """Get audit log statistics."""
    db = current_app.config.get('db')
    if not db:
        return error_response("Database not available", status_code=503)
    
    try:
        # Get total count and action breakdown
        entries = db.get_audit_log(limit=10000)
        
        action_counts = {}
        user_counts = {}
        
        for entry in entries:
            action = entry.get('action', 'unknown')
            user = entry.get('user', 'anonymous')
            
            action_counts[action] = action_counts.get(action, 0) + 1
            user_counts[user] = user_counts.get(user, 0) + 1
        
        return success_response(data={
            'total_entries': len(entries),
            'by_action': action_counts,
            'by_user': user_counts
        })
    except Exception as e:
        return error_response(f"Failed to get audit stats: {str(e)}")


@audit_bp.route('/api/audit/compliance-stats', methods=['GET'])
@require_role('admin')
def get_compliance_stats():
    """Get summarized compliance and security stats."""
    db = current_app.config.get('db')
    if not db:
        return error_response("Database not available", status_code=503)
    
    try:
        stats = db.get_compliance_stats()
        return success_response(data=stats)
    except Exception as e:
        return error_response(f"Failed to get compliance stats: {str(e)}")


@audit_bp.route('/api/audit/compliance-report', methods=['GET'])
@require_role('admin')
def get_compliance_report():
    """Generate and return a signed compliance report."""
    db = current_app.config.get('db')
    if not db:
        return error_response("Database not available", status_code=503)
    
    try:
        report = db.generate_compliance_report()
        return success_response(data=report)
    except Exception as e:
        return error_response(f"Failed to generate compliance report: {str(e)}")
