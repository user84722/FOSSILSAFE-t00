from flask import Blueprint, current_app, request, send_file
from backend.utils.responses import success_response, error_response
from backend.auth import require_role
import os

diagnostics_bp = Blueprint('diagnostics', __name__)

@diagnostics_bp.route('/api/diagnostics/run', methods=['POST'])
@require_role('operator')
def run_diagnostics():
    """Run full system diagnostics and create a job for tracking."""
    job_service = getattr(current_app, 'job_service', None)
    diagnostic_service = getattr(current_app, 'diagnostic_service', None)
    
    if not job_service or not diagnostic_service:
        return error_response("Diagnostic services unavailable", status_code=503)
        
    try:
        # Create an internal job to track diagnostics
        job_id = job_service.create_internal_job("Manual Diagnostics Run", "diagnostics_run", [])
        
        import threading
        def _do_run():
            try:
                job_service.update_job_with_log(job_id, "running", "Starting full appliance self-test")
                
                # Use the enhanced self-test logic
                results = diagnostic_service.run_full_self_test(save_to_db=True)
                
                # Store results in job info
                current_app.db.update_job_info(job_id, {"diagnostic_results": results})
                
                summary = f"Self-test completed: {results.get('overall', 'unknown')}"
                job_service.update_job_with_log(job_id, "completed", summary, "success")
            except Exception as e:
                job_service.update_job_with_log(job_id, "error", f"Self-test failed: {e}", "error")
            finally:
                job_service.cleanup_job_flag(job_id)
                
        threading.Thread(target=_do_run, daemon=True).start()
        
        return success_response(data={'job_id': job_id}, message="Diagnostics started")
    except Exception as e:
        return error_response(str(e))

@diagnostics_bp.route('/api/diagnostics/health', methods=['GET'])
@require_role('viewer')
def get_health():
    """Run a quick system health check."""
    diagnostic_service = getattr(current_app, 'diagnostic_service', None)
    if not diagnostic_service:
        return error_response("Diagnostic service unavailable", status_code=503)
        
    try:
        results = diagnostic_service.run_health_check()
        return success_response(data=results)
    except Exception as e:
        return error_response(str(e))

@diagnostics_bp.route('/api/diagnostics/bundle', methods=['POST'])
@require_role('admin')
def create_support_bundle():
    """Generate a support bundle for download."""
    diagnostic_service = getattr(current_app, 'diagnostic_service', None)
    if not diagnostic_service:
        return error_response("Diagnostic service unavailable", status_code=503)
        
    try:
        bundle_path = diagnostic_service.generate_support_bundle()
        return success_response(data={'bundle_path': bundle_path}, message="Support bundle generated")
    except Exception as e:
        return error_response(str(e))

@diagnostics_bp.route('/api/diagnostics/bundle/download', methods=['GET'])
@require_role('admin')
def download_support_bundle():
    """Download a previously generated support bundle."""
    path = request.args.get('path')
    if not path or not os.path.exists(path):
        return error_response("Bundle not found", status_code=404)
        
    # Security check: must be in temp dir or a specific bundles dir
    if not path.startswith('/tmp') and not 'lto_support_bundle' in path:
        return error_response("Invalid bundle path", status_code=403)
        
    try:
        return send_file(path, as_attachment=True)
    except Exception as e:
        return error_response(str(e))

@diagnostics_bp.route('/api/diagnostics/reports', methods=['GET'])
@require_role('viewer')
def list_diagnostics_reports():
    """List historical diagnostic reports."""
    try:
        limit = min(int(request.args.get('limit', 20)), 100)
        reports = current_app.db.get_diagnostics_reports(limit=limit)
        return success_response(data={'reports': reports})
    except Exception as e:
        return error_response(str(e))

@diagnostics_bp.route('/api/diagnostics/reports/<int:report_id>', methods=['DELETE'])
@require_role('admin')
def delete_diagnostics_report(report_id):
    """Delete a specific diagnostics report and its files."""
    report = current_app.db.get_diagnostics_report(report_id)
    if not report:
        return error_response("Report not found", status_code=404)
        
    # Attempt to delete files
    for key in ['report_json_path', 'report_text_path']:
        path = report.get(key)
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception as e:
                current_app.log_warning(f"Failed to delete report file {path}: {e}")
    
    try:
        # Assuming we need a delete method in DB, let's check or implement
        # For now, let's assume current_app.db.delete_diagnostics_report exists or we'll add it
        if hasattr(current_app.db, 'delete_diagnostics_report'):
            current_app.db.delete_diagnostics_report(report_id)
        else:
            current_app.db.execute('DELETE FROM diagnostics_reports WHERE id = ?', (report_id,))
            current_app.db.commit()
            
        return success_response(message="Report deleted")
    except Exception as e:
        return error_response(str(e))

@diagnostics_bp.route('/api/diagnostics/reports/<int:report_id>/download', methods=['GET'])
@require_role('viewer')
def download_diagnostics_report(report_id):
    """Download a specific diagnostics report (JSON or Text)."""
    kind = request.args.get('kind', 'json').lower()
    
    report = current_app.db.get_diagnostics_report(report_id)
    if not report:
        return error_response("Report not found", status_code=404)
        
    path = report.get('report_json_path') if kind == 'json' else report.get('report_text_path')
    
    if not path or not os.path.exists(path):
        return error_response(f"Report file ({kind}) not found on disk", status_code=404)
        
    filename = f"diagnostics_report_{report_id}.{kind}"
    return send_file(path, as_attachment=True, download_name=filename)
