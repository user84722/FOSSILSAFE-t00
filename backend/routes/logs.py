from backend.utils.responses import success_response, error_response
from backend.auth import require_role
from flask import Blueprint, current_app, request

logs_bp = Blueprint('logs', __name__)

@logs_bp.route('/api/logs', methods=['GET'])
@require_role('viewer')
def get_logs():
    """Retrieve system logs with filtering and pagination"""
    log_manager = getattr(current_app, 'log_manager', None)
    if log_manager is None:
        return error_response(
            message="Logs subsystem not initialized",
            code="logs_unavailable",
            status_code=503
        )
    
    level = request.args.get('level', 'all')
    category = request.args.get('category')
    try:
        limit = min(int(request.args.get('limit', 100)), 1000)
        offset = int(request.args.get('offset', 0))
    except (ValueError, TypeError):
        limit = 100
        offset = 0
        
    since_id = request.args.get('since_id')
    since_seq = request.args.get('since_seq')
    
    result = log_manager.get(
        level=level,
        category=category,
        limit=limit,
        offset=offset,
        since_id=since_id,
        since_seq=since_seq,
    )
    # Return result directly as data
    return success_response(data=result)

@logs_bp.route('/api/frontend/log', methods=['POST'])
@require_role('viewer')
def log_frontend_error():
    """Capture React error boundary reports and other frontend logs"""
    data = request.get_json() or {}
    level = data.get('level', 'error').lower()
    message = data.get('message', 'Unknown frontend error')
    category = data.get('category', 'frontend')
    details = data.get('details') # Stack trace etc.

    log_manager = getattr(current_app, 'log_manager', None)
    if not log_manager:
        return error_response("Logs subsystem not initialized", status_code=503)

    # Use log_manager.add directly
    if hasattr(log_manager, 'add'):
        log_manager.add(level, message, category, details)
        return success_response(message="Frontend error logged")
    
    return error_response("Log manager add function missing", status_code=500)

@logs_bp.route('/api/logs/cleanup', methods=['POST'])
@require_role('admin')
def cleanup_logs():
    """Clean up logs older than the specified retention period."""
    log_manager = getattr(current_app, 'log_manager', None)
    if not log_manager:
        return error_response("Logs subsystem not initialized", status_code=503)
    
    data = request.get_json() or {}
    retention_days = data.get('retention_days', 30)
    
    try:
        retention_days = max(0, min(int(retention_days), 365))  # Clamp 0-365 days
    except (ValueError, TypeError):
        retention_days = 30
    
    if hasattr(log_manager, 'cleanup_old_logs'):
        deleted_count = log_manager.cleanup_old_logs(retention_days)
        return success_response(
            message=f"Cleaned up {deleted_count} logs older than {retention_days} days",
            data={'deleted_count': deleted_count, 'retention_days': retention_days}
        )
    
    return error_response("Log cleanup not available", status_code=500)

@logs_bp.route('/api/logs/stats', methods=['GET'])
@require_role('viewer')
def get_log_stats():
    """Get log statistics for monitoring."""
    log_manager = getattr(current_app, 'log_manager', None)
    if not log_manager:
        return error_response("Logs subsystem not initialized", status_code=503)
    
    if hasattr(log_manager, 'get_log_stats'):
        return success_response(data=log_manager.get_log_stats())
    
    return success_response(data={'total_in_memory': 0, 'by_level': {}})

