from flask import Blueprint, current_app, request, jsonify
from backend.utils.responses import success_response, error_response
from backend.auth import require_role

jobs_bp = Blueprint('jobs', __name__)

@jobs_bp.route('/api/jobs', methods=['GET'])
@require_role('viewer')
def get_jobs():
    """List all jobs with pagination."""
    job_service = getattr(current_app, 'job_service', None)
    if not job_service:
        return error_response("Job service unavailable", status_code=503)
        
    try:
        limit = min(int(request.args.get('limit', 100)), 500)
        offset = int(request.args.get('offset', 0))
        jobs = job_service.get_jobs(limit=limit, offset=offset)
        return success_response(data={'jobs': jobs, 'total': len(jobs)})
    except Exception as e:
        return error_response(str(e))

@jobs_bp.route('/api/jobs/<int:job_id>', methods=['GET'])
@require_role('viewer')
def get_job(job_id: int):
    """Get details and logs for a specific job."""
    job_service = getattr(current_app, 'job_service', None)
    if not job_service:
        return error_response("Job service unavailable", status_code=503)
        
    try:
        job = job_service.get_job(job_id)
        if not job:
            return error_response('Job not found', code="not_found", status_code=404)
        logs = job_service.get_job_logs(job_id)
        return success_response(data={'job': job, 'logs': logs})
    except Exception as e:
        return error_response(str(e))

@jobs_bp.route('/api/jobs', methods=['POST'])
@require_role('operator')
def create_job():
    """Create and start a new job."""
    job_service = getattr(current_app, 'job_service', None)
    if not job_service:
        return error_response("Job service unavailable", status_code=503)
        
    try:
        data = request.get_json() or {}
        success, result = job_service.create_job(data)
        if not success:
            return error_response(result, status_code=400)
        return success_response(data={'job_id': result}, message="Job created successfully")
    except Exception as e:
        current_app.log_error(f"Failed to create job: {e}", 'job')
        return error_response(str(e))

@jobs_bp.route('/api/jobs/<int:job_id>/cancel', methods=['POST'])
@require_role('operator')
def cancel_job(job_id: int):
    """Cancel a running job."""
    job_service = getattr(current_app, 'job_service', None)
    if not job_service:
        return error_response("Job service unavailable", status_code=503)
        
    try:
        success = job_service.cancel_job(job_id)
        if not success:
            return error_response("Job not found or cannot be cancelled", status_code=404)
        return success_response(message="Cancellation requested")
    except Exception as e:
        return error_response(str(e))

@jobs_bp.route('/api/jobs/preflight', methods=['POST'])
@require_role('operator')
def run_preflight():
    """Run preflight checks for a potential job."""
    job_service = getattr(current_app, 'job_service', None)
    if not job_service:
        return error_response("Job service unavailable", status_code=503)
        
    try:
        data = request.get_json() or {}
        results = job_service.run_preflight(data)
        return success_response(data={'result': results})
    except Exception as e:
        return error_response(str(e))
@jobs_bp.route('/api/jobs/dryrun', methods=['POST'])
@require_role('operator')
def dry_run():
    """Perform a dry-run to estimate job requirements."""
    job_service = getattr(current_app, 'job_service', None)
    if not job_service:
        return error_response("Job service unavailable", status_code=503)
        
    try:
        data = request.get_json() or {}
        success, result = job_service.dry_run(data)
        if not success:
            return error_response(result.get("message"), code=result.get("code"), detail=result.get("detail"), status_code=400)
        return success_response(data={'result': result})
    except Exception as e:
        return error_response(str(e))
@jobs_bp.route('/api/jobs/hooks', methods=['GET'])
@require_role('operator')
def list_hooks():
    """List available pre- and post-job hooks."""
    from backend.services.hook_service import hook_service
    pre_hooks = hook_service.list_hooks("pre")
    post_hooks = hook_service.list_hooks("post")
    return success_response(data={
        'pre': pre_hooks,
        'post': post_hooks
    })
