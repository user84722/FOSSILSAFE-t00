
from flask import Blueprint, request, jsonify, current_app
from backend.services.verification_service import VerificationService
from backend.database import Database
from backend.auth import require_role

verification_bp = Blueprint('verification', __name__)

def get_db():
    return current_app.config['db']

def get_verification_service() -> VerificationService:
    return current_app.config['verification_service']

@verification_bp.route('/start', methods=['POST'])
@require_role('operator')
def start_verification():
    """Start a manual verification job."""
    data = request.json
    tapes = data.get('tapes', [])
    
    if not tapes:
        return jsonify({'error': 'No tapes specified'}), 400
        
    db = get_db()
    service = get_verification_service()
    
    job_id = db.create_job(
        name=f"Manual Verification: {len(tapes)} tapes",
        job_type='verification',
        tapes=tapes,
        verify=True
    )
    
    service.start_verification_job(tapes, job_id)
    return jsonify({'job_id': job_id, 'status': 'started'})

@verification_bp.route('/reports', methods=['GET'])
@require_role('viewer')
def get_reports():
    """Get recent verification reports."""
    limit = int(request.args.get('limit', 50))
    db = get_db()
    reports = db.get_verification_reports(limit)
    return jsonify(reports)

@verification_bp.route('/reports/<int:report_id>', methods=['GET'])
@require_role('viewer')
def get_report(report_id):
    """Get a specific verification report."""
    db = get_db()
    report = db.get_verification_report(report_id)
    if not report:
        return jsonify({'error': 'Report not found'}), 404
    return jsonify(report)
