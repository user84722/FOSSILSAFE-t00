"""
Recovery API endpoints for catalog rebuild.
"""
from flask import Blueprint, request, current_app
from backend.utils.responses import success_response, error_response
from backend.auth import require_role
from backend.catalog_rebuild import CatalogRebuildEngine
import logging

logger = logging.getLogger(__name__)

recovery_bp = Blueprint('recovery', __name__)


@recovery_bp.route('/api/recovery/scan-tape', methods=['POST'])
@require_role('admin')
def scan_tape():
    """Scan a single tape for catalog."""
    try:
        data = request.get_json() or {}
        tape_barcode = data.get('tape_barcode')
        
        if not tape_barcode:
            return error_response("tape_barcode is required", status_code=400)
        
        db = getattr(current_app, 'db', None)
        tape_controller = getattr(current_app, 'tape_controller', None)
        
        if not db or not tape_controller:
            return error_response("Services unavailable", status_code=503)
        
        rebuild_engine = CatalogRebuildEngine(db, tape_controller)
        catalog = rebuild_engine.scan_tape_for_catalog(tape_barcode)
        
        if not catalog:
            return error_response(f"No catalog found on tape {tape_barcode}", status_code=404)
        
        trust_result = rebuild_engine.trust_results.get(tape_barcode, {})
        
        return success_response(data={
            'catalog': catalog,
            'trust_level': trust_result.get('trust_level'),
            'verification_message': trust_result.get('message')
        })
        
    except Exception as e:
        logger.exception("Tape scan failed")
        return error_response(str(e))


@recovery_bp.route('/api/recovery/rebuild', methods=['POST'])
@require_role('admin')
def start_rebuild():
    """Start full catalog rebuild from multiple tapes."""
    try:
        data = request.get_json() or {}
        tape_barcodes = data.get('tape_barcodes', [])
        scan_all = data.get('scan_all', False)
        
        db = getattr(current_app, 'db', None)
        tape_controller = getattr(current_app, 'tape_controller', None)
        
        if not db or not tape_controller:
            return error_response("Services unavailable", status_code=503)
        
        # If scan_all, get all tapes from library
        if scan_all:
            inventory = tape_controller.inventory()
            tape_barcodes = [t.get('barcode') for t in inventory if t.get('barcode')]
        
        if not tape_barcodes:
            return error_response("No tapes specified or found in library", status_code=400)
        
        rebuild_engine = CatalogRebuildEngine(db, tape_controller)
        summary = rebuild_engine.rebuild_from_tapes(tape_barcodes)
        
        return success_response(data=summary, message="Catalog rebuild complete")
        
    except Exception as e:
        logger.exception("Catalog rebuild failed")
        return error_response(str(e))


@recovery_bp.route('/api/recovery/import', methods=['POST'])
@require_role('admin')
def import_catalog():
    """Import discovered catalogs to database."""
    try:
        db = getattr(current_app, 'db', None)
        tape_controller = getattr(current_app, 'tape_controller', None)
        
        if not db or not tape_controller:
            return error_response("Services unavailable", status_code=503)
        
        # Assuming rebuild_engine is stored in app context
        rebuild_engine = getattr(current_app, 'rebuild_engine', None)
        if not rebuild_engine or not rebuild_engine.discovered_catalogs:
            return error_response("No catalogs to import. Run rebuild first.", status_code=400)
        
        files_imported = rebuild_engine.import_to_database()
        
        return success_response(data={'files_imported': files_imported}, message=f"Imported {files_imported} files")
        
    except Exception as e:
        logger.exception("Catalog import failed")
        return error_response(str(e))

@recovery_bp.route('/api/recovery/emergency-dump', methods=['POST'])
@require_role('admin')
def emergency_dump():
    """Start an emergency tape dump (tar extraction)."""
    job_service = getattr(current_app, 'job_service', None)
    tape_controller = getattr(current_app, 'tape_controller', None)
    
    if not job_service or not tape_controller:
        return error_response("Services unavailable", status_code=503)

    try:
        data = request.get_json() or {}
        barcode = data.get('barcode')
        destination = data.get('destination')
        drive = int(data.get('drive', 0))
        
        if not barcode or not destination:
            return error_response("Barcode and destination path required", status_code=400)

        job_id = job_service.create_internal_job(
            f"Emergency Dump {barcode}", 
            "tape_restore", 
            [barcode], 
            drive=drive
        )
        
        def _do_dump():
            import threading
            try:
                job_service.update_job_with_log(job_id, "running", f"Dumping {barcode} to {destination}")
                tape_controller.dump_tape(barcode, destination_path=destination, drive=drive)
                job_service.update_job_with_log(job_id, "completed", "Dump complete", "success")
            except Exception as e:
                job_service.update_job_with_log(job_id, "error", f"Dump failed: {e}", "error")
            finally:
                job_service.cleanup_job_flag(job_id)

        import threading
        threading.Thread(target=_do_dump, daemon=True).start()
        
        return success_response(data={'job_id': job_id})
        
    except Exception as e:
        return error_response(str(e))


@recovery_bp.route('/api/recovery/status', methods=['GET'])
@require_role('viewer')
def get_status():
    """Get current rebuild status."""
    try:
        rebuild_engine = getattr(current_app, 'rebuild_engine', None)
        
        if not rebuild_engine:
            return success_response(data={'status': 'idle'})
        
        return success_response(data={
            'status': 'ready',
            'catalogs_discovered': len(rebuild_engine.discovered_catalogs),
            'trust_results': rebuild_engine.trust_results
        })
        
    except Exception as e:
        logger.exception("Status check failed")
        return error_response(str(e))
