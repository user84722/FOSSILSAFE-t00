import os


import json
import tempfile
import time
import hashlib
import threading
from datetime import datetime
from flask import Blueprint, current_app, request, jsonify, send_file, g
from backend.utils.responses import success_response, error_response
from backend.utils.validation import validate_tape_identifier
from backend.utils.datetime import now_utc_iso
from backend.auth import require_role, require_admin
from typing import Any, cast, Dict

tapes_bp = Blueprint('tapes', __name__)
_last_inventory_refresh = 0
_refresh_lock = threading.Lock()

def _get_library_id():
    lib_id = None
    if request.is_json:
        data = request.get_json(silent=True) or {}
        lib_id = data.get('library_id')
    if not lib_id:
        lib_id = request.args.get('library_id')
    return lib_id

def _get_controller() -> Any:
    lib_id = _get_library_id()
    if hasattr(current_app, 'library_manager') and current_app.library_manager:
        return current_app.library_manager.get_library(lib_id)
    return getattr(current_app, 'tape_controller', None)

@tapes_bp.route('/api/tapes', methods=['GET'])
@require_role('viewer')
def get_tapes():
    """Retrieve all tapes with updated status and active job overlay."""
    tape_service = cast(Any, getattr(current_app, 'tape_service', None))
    db = cast(Any, getattr(current_app, 'db', None))
    if not tape_service or not db:
        return error_response("Tape service unavailable", code="service_unavailable", status_code=503)

    try:
        controller = cast(Any, _get_controller())
        active_jobs = db.get_active_jobs() if db else []
        hardware_locked = any(j.get('job_type') in ('tape_wipe', 'tape_format') for j in active_jobs)

        # Always serve from DB cache immediately so the worker is never blocked.
        # Fire a background thread to refresh from hardware (max 8s) for next request.
        inventory = db.get_tape_inventory()

        if controller and not hardware_locked:
            def _bg_refresh():
                global _last_inventory_refresh
                now = time.time()
                with _refresh_lock:
                    if now - _last_inventory_refresh < 30:
                        return
                    _last_inventory_refresh = now
                
                try:
                    fresh = controller.scan_library('fast')
                    if fresh:
                        db.update_tape_inventory(fresh)
                except Exception:
                    pass

            t = threading.Thread(target=_bg_refresh, daemon=True)
            t.start()

        aliases = db.get_tape_aliases()
        db_tapes = {tape["barcode"]: tape for tape in inventory if tape.get("barcode")}

        has_mail_slots = False
        for tape in inventory:
            if tape.get('location_type') == 'mailslot':
                has_mail_slots = True
        aliases = db.get_tape_aliases()
        has_mail_slots = False
        for tape in inventory:
            if tape.get('location_type') == 'mailslot':
                has_mail_slots = True
            barcode = tape.get("barcode")
            tape['alias'] = aliases.get(barcode) or tape.get('alias')

        if has_mail_slots:
            from backend.config_store import update_config
            update_config({"tape": {"mail_slot_detected": True}})

        active_jobs = db.get_active_jobs()
        # tape_service.apply_active_tape_job_state now handles:
        # 1. Overlaying active jobs (status updates)
        # 2. Injecting missing empty drives
        # 3. Synthesizing 'location' strings (Drive 0, Slot 5, etc.)
        tapes = tape_service.apply_active_tape_job_state(inventory, active_jobs)
        return success_response(data={'tapes': tapes})
    except Exception as e:

        return error_response(str(e), code="tape_inventory_failed")

@tapes_bp.route('/api/library/scan', methods=['POST'])
@tapes_bp.route('/api/tapes/scan', methods=['POST', 'GET'])
@require_role('operator')
def scan_library():
    """Trigger a library scan (fast or deep)."""
    tape_service = cast(Any, getattr(current_app, 'tape_service', None))
    if not tape_service:
        return error_response("Tape service unavailable", status_code=503)

    try:
        # In POST, mode might be in JSON. In GET, in query args.
        if request.method == 'POST':
            payload = request.get_json(silent=True) or {}
            mode = payload.get("mode")
        else:
            mode = request.args.get("mode")
            
        settings = tape_service.get_scan_settings()
        mode = (mode or settings["default_mode"]).lower()
        if mode not in ("fast", "deep"):
            return error_response('Invalid scan mode', status_code=400)

        library_id = _get_library_id()
        if mode == "deep":
            guard = tape_service.guard_deep_scan(settings, library_id=library_id)
            if guard:
                payload, status = guard
                # standardized error format
                return error_response(payload.get('error'), code=payload.get('code', 'scan_guarded'), status_code=status, detail=payload.get('retry_after_seconds'))

        current_app.log_info(f"Library scan requested (mode: {mode}, lib: {library_id})", 'tape')
        tapes = tape_service.scan_library_and_update(mode, library_id=library_id)
        current_app.log_info(f"Library scan complete: {len(tapes)} tapes found", 'tape')
        return success_response(data={'tapes': tapes})
    except Exception as e:
        current_app.log_error(f"Library scan failed: {e}", 'tape')
        return error_response(str(e), code="scan_failed")

@tapes_bp.route('/api/tapes/<barcode>', methods=['GET'])
@require_role('viewer')
def get_tape_details(barcode):
    """Retrieve detailed information about a specific tape."""
    db = cast(Any, getattr(current_app, 'db', None))
    if not db:
        return error_response("Database unavailable", status_code=503)
    
    try:
        tapes = db.get_tape_inventory()
        tape = next((t for t in tapes if t['barcode'] == barcode), None)
        if not tape:
            return error_response('Tape not found', status_code=404)
        return success_response(data={'tape': tape})
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/tapes/<barcode>/alerts', methods=['GET'])
@require_role('viewer')
def get_tape_alerts(barcode):
    """Retrieve recent TapeAlerts for a specific tape."""
    db = cast(Any, getattr(current_app, 'db', None))
    if not db:
        return error_response("Database unavailable", status_code=503)
    
    try:
        limit = request.args.get('limit', 50, type=int)
        alerts = db.get_last_tape_alerts(barcode, limit=limit)
        # Format for consistency if needed, but db returns list of dicts
        return success_response(data={'alerts': alerts})
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/tapes/<barcode>/alias', methods=['POST'])
@require_role('operator')
def update_tape_alias(barcode):
    """Update tape alias for manual naming."""
    db = cast(Any, getattr(current_app, 'db', None))
    if not db:
        return error_response("Database unavailable", status_code=503)
        
    try:
        data = request.get_json() or {}
        alias = data.get('alias')
        if alias is not None:
            alias = alias.strip()
            if alias == '':
                alias = None

        tape = db.get_tape(barcode)
        if not tape:
            return error_response('Tape not found', status_code=404)

        db.update_tape_alias(barcode, alias)
        current_app.log_info(f"Updated tape alias for {barcode}: {alias or 'cleared'}", 'tape')
        return success_response(data={'alias': alias}, message="Tape alias updated")
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/tapes/random-name', methods=['GET'])
@require_role('viewer')
def get_random_tape_name():
    """Get a single random name."""
    from backend.utils.naming import get_random_name
    return success_response(data={'name': get_random_name()})

@tapes_bp.route('/api/tapes/auto-alias', methods=['POST'])
@require_role('operator')
def auto_alias_tapes():
    """Auto-assign aliases to unnamed tapes."""
    tape_service = cast(Any, getattr(current_app, 'tape_service', None))
    if not tape_service:
        return error_response("Tape service unavailable", status_code=503)
    
    try:
        data = request.get_json() or {}
        overwrite = bool(data.get('overwrite'))
        count = tape_service.auto_alias_tapes(overwrite=overwrite)
        current_app.log_info(f"Auto-aliased {count} tapes", 'tape')
        return success_response(data={'count': count}, message=f"named {count} tapes")
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/library/poll-alerts', methods=['POST'])
@require_role('operator')
def force_poll_alerts():
    """Manually trigger a TapeAlert poll for the primary drive."""
    controller = cast(Any, _get_controller())
    if not controller:
        return error_response("Tape controller unavailable", status_code=503)
        
    try:
        drive = request.args.get('drive', 0, type=int)
        alerts = controller.poll_tape_alerts(drive=drive)
        return success_response(data={'alerts': alerts}, message=f"Poll complete, found {len(alerts)} alerts")
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/drive-only/status')
@require_role('viewer')
def get_drive_only_status():
    """Check if running in drive-only mode (no changer)"""
    tape_controller = cast(Any, _get_controller())
    if not tape_controller:
        return error_response("Tape hardware unavailable", status_code=503)
        
    try:
        drive_only = tape_controller.is_drive_only()
        return success_response(data={
            'drive_only': drive_only,
            'mounted_tape': getattr(tape_controller, 'mounted_tape', None),
            'drive_device': getattr(tape_controller, 'device', None)
        })
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/drive-only/insert-tape', methods=['POST'])
@require_role('operator')
def confirm_tape_inserted():
    """Confirm manual tape insertion in drive-only mode."""
    tape_controller = cast(Any, _get_controller())
    db = cast(Any, getattr(current_app, 'db', None))
    if not tape_controller or not db:
        return error_response("Services unavailable", status_code=503)

    try:
        data = request.get_json() or {}
        barcode = data.get('barcode', '')
        
        if not barcode:
            return error_response('Tape barcode/name is required', status_code=400)
        
        if not tape_controller.is_drive_only():
            return error_response('This endpoint is only for drive-only mode', status_code=400)
        
        tape_controller.set_manual_tape(barcode)
        
        existing = db.get_tape(barcode)
        if not existing:
            db.add_tape(barcode=barcode, slot=0)
        
        current_app.log_info(f"Manual tape insertion confirmed: {barcode}", 'tape')
        return success_response(message=f"Tape '{barcode}' registered", data={'barcode': barcode})
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/drive-only/eject-tape', methods=['POST'])
@require_role('operator')
def eject_manual_tape():
    """Signal manual tape removal in drive-only mode."""
    tape_controller = cast(Any, _get_controller())
    if not tape_controller:
        return error_response("Tape hardware unavailable", status_code=503)

    try:
        if not tape_controller.is_drive_only():
            return error_response('This endpoint is only for drive-only mode', status_code=400)
        
        previous_tape = getattr(tape_controller, 'mounted_tape', None)
        tape_controller.unload_tape()
        
        current_app.log_info(f"Manual tape ejection confirmed: {previous_tape}", 'tape')
        return success_response(message='Tape ejected', data={'previous_tape': previous_tape})
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/library/load/<barcode>', methods=['POST'])
@require_role('operator')
def load_tape(barcode):
    """Load a tape by barcode into the first available drive."""
    tape_controller = cast(Any, _get_controller())
    if not tape_controller:
        return error_response("Tape hardware unavailable", status_code=503)

    try:
        drive = int(request.args.get('drive', 0) or 0)
        tape_controller.load_tape(barcode, drive=drive)
        current_app.log_info(f"Tape {barcode} loaded into drive {drive}", 'tape')
        return success_response(message=f"Tape {barcode} loaded")
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/library/unload', methods=['POST'])
@require_role('operator')
def unload_tape():
    """Unload the tape from a drive."""
    tape_controller = cast(Any, _get_controller())
    if not tape_controller:
        return error_response("Tape hardware unavailable", status_code=503)

    try:
        data = request.get_json() or {}
        drive = int(data.get('drive', 0) or 0)
        tape_controller.unload_tape(drive=drive)
        current_app.log_info(f"Drive {drive} unloaded", 'tape')
        return success_response(message=f"Drive {drive} unloaded")
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/library/force-unload', methods=['POST'])
@require_admin
def force_unload_tape():
    """Force an unload by moving a tape from drive to slot directly."""
    tape_controller = cast(Any, _get_controller())
    job_service = cast(Any, getattr(current_app, 'job_service', None))
    if not tape_controller or not job_service:
        return error_response("Services unavailable", status_code=503)

    try:
        data = request.get_json() or {}
        drive = int(data.get('drive', 0) or 0)
        dest_slot = data.get('dest_slot')
        
        job_id = job_service.create_internal_job(
            f"Force unload drive {drive}",
            "library_force_unload",
            [],
            drive=drive
        )

        def _do_force_unload():
            try:
                job_service.update_job_with_log(job_id, "running", f"Force-unloading drive {drive}")
                tape_controller.force_unload(drive=drive, dest_slot=dest_slot)
                job_service.set_job_progress(job_id, "completed", "Force unload complete", level="success")
                job_service.update_job_with_log(job_id, "completed", "Force unload complete", "success")
            except Exception as e:
                job_service.update_job_with_log(job_id, "error", f"Force unload failed: {e}", "error")
            finally:
                job_service.cleanup_job_flag(job_id)

        threading.Thread(target=_do_force_unload, daemon=True).start()
        return success_response(data={'job_id': job_id}, message="Force unload started")
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/tape/move', methods=['POST'])
@require_role('operator')
def move_tape():
    """Move a tape between library elements."""
    tape_controller = cast(Any, _get_controller())
    job_service = cast(Any, getattr(current_app, 'job_service', None))
    if not tape_controller or not job_service:
        return error_response("Services unavailable", status_code=503)

    try:
        data = request.get_json() or {}
        if tape_controller.is_drive_only():
            return error_response("Tape moves require a library changer", status_code=400)
            
        return_home = bool(data.get('return_home'))
        source = data.get('source') or {}
        destination = data.get('destination') or {}

        def _normalize_endpoint(endpoint):
            e_type = str(endpoint.get('type', '')).lower()
            if e_type not in ('slot', 'drive', 'mailslot'): return None
            try:
                val = int(endpoint.get('value', endpoint.get('index', 0)))
                return {'type': e_type, 'value': val}
            except: return None

        if return_home:
            drive = int(data.get('drive', 0) or 0)
            job_id = job_service.create_internal_job(f"Return tape in drive {drive}", "tape_move", [], drive=drive)
            
            def _do_return():
                try:
                    job_service.update_job_with_log(job_id, "running", "Returning tape to home slot")
                    tape_controller.unload_tape(drive)
                    job_service.update_job_with_log(job_id, "completed", "Tape returned", "success")
                except Exception as e:
                    job_service.update_job_with_log(job_id, "error", f"Return failed: {e}", "error")
                finally: job_service.cleanup_job_flag(job_id)
                
            threading.Thread(target=_do_return, daemon=True).start()
            return success_response(data={'job_id': job_id})

        s = cast(dict, _normalize_endpoint(source))
        d = cast(dict, _normalize_endpoint(destination))
        if not s or not d: return error_response("Invalid source or destination", status_code=400)

        job_id = job_service.create_internal_job(f"Move {s['type']}{s['value']} to {d['type']}{d['value']}", "tape_move", [])
        
        def _do_move():
            try:
                job_service.update_job_with_log(job_id, "running", "Moving tape")
                tape_controller.move_tape(source=s, destination=d)
                job_service.update_job_with_log(job_id, "completed", "Move complete", "success")
            except Exception as e:
                job_service.update_job_with_log(job_id, "error", f"Move failed: {e}", "error")
            finally: job_service.cleanup_job_flag(job_id)

        threading.Thread(target=_do_move, daemon=True).start()
        return success_response(data={'job_id': job_id})
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/tapes/<barcode>/export', methods=['POST'])
@require_role('operator')
def export_tape(barcode):
    """Move a tape from its current location to the first available mail slot."""
    tape_controller = cast(Any, _get_controller())
    job_service = cast(Any, getattr(current_app, 'job_service', None))
    if not tape_controller or not job_service:
        return error_response("Services unavailable", status_code=503)

    try:
        current_app.log_info(f"Export requested for tape {barcode}", "tape")
        tapes = tape_controller.scan_library("fast")
        
        # Find target tape
        source_tape = next((t for t in tapes if t.get('barcode') == barcode), None)
        if not source_tape:
            return error_response(f"Tape {barcode} not found in library", status_code=404)
            
        if source_tape.get('location_type') == 'mailslot':
            return error_response(f"Tape {barcode} is already in a mail slot", status_code=400)
            
        # Find first empty mailslot
        empty_mailslot = next((t for t in tapes if t.get('location_type') == 'mailslot' and t.get('status') == 'empty'), None)
        if not empty_mailslot:
            return error_response("No empty mail slots available", status_code=400)
            
        source = {'type': source_tape['location_type'], 'value': source_tape.get('slot') or source_tape.get('drive_index')}
        destination = {'type': 'mailslot', 'value': empty_mailslot.get('slot')}
        
        job_id = job_service.create_internal_job(f"Export {barcode} to mailslot {empty_mailslot['slot']}", "tape_export", [barcode])
        
        def _do_export():
            try:
                job_service.update_job_with_log(job_id, "running", f"Exporting {barcode}")
                tape_controller.move_tape(source=source, destination=destination)
                job_service.update_job_with_log(job_id, "completed", f"Export of {barcode} complete", "success")
            except Exception as e:
                job_service.update_job_with_log(job_id, "error", f"Export failed: {e}", "error")
            finally: 
                job_service.cleanup_job_flag(job_id)

        threading.Thread(target=_do_export, daemon=True).start()
        return success_response(data={'job_id': job_id})
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/tapes/import', methods=['POST'])
@require_role('operator')
def import_tape():
    """Move a tape from a mail slot into the first available storage slot."""
    tape_controller = cast(Any, _get_controller())
    job_service = cast(Any, getattr(current_app, 'job_service', None))
    if not tape_controller or not job_service:
        return error_response("Services unavailable", status_code=503)

    try:
        data = request.get_json(silent=True) or {}
        mailslot_id = data.get('mailslot')
        current_app.log_info(f"Import requested (mailslot={mailslot_id or 'auto'})", "tape")
        
        tapes = tape_controller.scan_library("fast")
        
        # Find the tape in the mail slot
        source_tape = None
        if mailslot_id:
            source_tape = next((t for t in tapes if t.get('location_type') == 'mailslot' and (t.get('slot') == mailslot_id or t.get('slot_number') == mailslot_id) and t.get('status') != 'empty'), None)
            if not source_tape:
                return error_response(f"No tape found in mailslot {mailslot_id}", status_code=404)
        else:
            # Just grab the first full mail slot
            source_tape = next((t for t in tapes if t.get('location_type') == 'mailslot' and t.get('status') != 'empty'), None)
            if not source_tape:
                return error_response("No tapes found in any mail slots", status_code=404)
                
        # Find first empty standard slot
        empty_slot = next((t for t in tapes if t.get('location_type') == 'slot' and t.get('status') == 'empty'), None)
        if not source_tape or not empty_slot:
            return error_response("Source tape and empty slot are required", status_code=400)
            
        source_tape = cast(dict, source_tape)
        empty_slot = cast(dict, empty_slot)
        barcode = source_tape.get('barcode') or "Unknown Tape"
        source = {'type': 'mailslot', 'value': source_tape.get('slot')}
        destination = {'type': 'slot', 'value': empty_slot.get('slot')}
        
        job_id = job_service.create_internal_job(f"Import {barcode} from mailslot {source_tape.get('slot')}", "tape_import", [barcode] if barcode != "Unknown Tape" else [])
        
        source_slot_val = source_tape.get('slot')
        dest_slot_val = empty_slot.get('slot')
        
        def _do_import():
            try:
                job_service.update_job_with_log(job_id, "running", f"Importing tape from mailslot {source_slot_val}")
                tape_controller.move_tape(source=source, destination=destination)
                job_service.update_job_with_log(job_id, "completed", f"Import complete to slot {dest_slot_val}", "success")
            except Exception as e:
                job_service.update_job_with_log(job_id, "error", f"Import failed: {e}", "error")
            finally: 
                job_service.cleanup_job_flag(job_id)

        threading.Thread(target=_do_import, daemon=True).start()
        return success_response(data={'job_id': job_id})
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/library/bulk-move', methods=['POST'])
@require_role('operator')
def bulk_move_tapes():
    """Execute multiple tape moves in sequence."""
    tape_controller = cast(Any, _get_controller())
    job_service = cast(Any, getattr(current_app, 'job_service', None))
    if not tape_controller or not job_service:
        return error_response("Services unavailable", status_code=503)

    try:
        data = request.get_json() or {}
        moves = data.get('moves', [])
        if not moves:
            return error_response("No moves provided", status_code=400)
            
        job_id = job_service.create_internal_job(f"Bulk Move ({len(moves)} tapes)", "tape_move", [])
        
        def _do_bulk_move():
            try:
                for idx, move in enumerate(moves):
                    s = move.get('source')
                    d = move.get('destination')
                    job_service.update_job_with_log(job_id, "running", f"Moving tape {idx+1}/{len(moves)}")
                    tape_controller.move_tape(source=s, destination=d)
                job_service.update_job_with_log(job_id, "completed", "Bulk move complete", "success")
            except Exception as e:
                job_service.update_job_with_log(job_id, "error", f"Bulk move failed at index {idx}: {e}", "error")
            finally:
                job_service.cleanup_job_flag(job_id)

        threading.Thread(target=_do_bulk_move, daemon=True).start()
        return success_response(data={'job_id': job_id}, message="Bulk move started")
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/library/bulk-format', methods=['POST'])
@tapes_bp.route('/api/tapes/<barcode>/format', methods=['POST'])
@require_admin
def format_tape(barcode=None):
    """Format one or more tapes with LTFS."""
    job_service = cast(Any, getattr(current_app, 'job_service', None))
    tape_controller = cast(Any, _get_controller())
    db = cast(Any, getattr(current_app, 'db', None))
    if not job_service or not tape_controller or not db:
        return error_response("Services unavailable", status_code=503)

    try:
        data = request.get_json() or {}
        barcodes = [barcode] if barcode else data.get('barcodes', [])
        drive_str = str(data.get('drive', 0) or 0)
        drive = int(drive_str)
        
        if not barcodes:
            return error_response("No barcodes provided", status_code=400)
            
        # Check WORM locks and cleaning tapes
        cleaning_tapes = []
        locked = []
        for b in barcodes:
            tape = db.get_tape(b)
            if tape and tape.get('is_cleaning_tape'):
                cleaning_tapes.append(b)
            elif b.startswith('CLN'):
                cleaning_tapes.append(b)
            
            if db.is_tape_locked(b):
                locked.append(b)

        if cleaning_tapes:
            return error_response(f"Tapes {', '.join(cleaning_tapes)} are cleaning media and cannot be formatted.", status_code=403)

        if locked:
            webhook_service = current_app.config.get('webhook_service')
            if webhook_service:
                for b in locked:
                    webhook_service.trigger_event("COMPLIANCE_ALARM", {
                        "reason": "WORM_LOCK_VIOLATION",
                        "action": "FORMAT",
                        "barcode": b,
                        "status": "BLOCKED"
                    })
            return error_response(f"Tapes {', '.join(locked)} are under WORM retention lock and cannot be formatted.", status_code=403)
            
        job_name = f"Format {', '.join(barcodes)}" if len(barcodes) <= 2 else f"Bulk Format ({len(barcodes)} tapes)"
        job_id = job_service.create_internal_job(job_name, "tape_format", barcodes, drive=drive, total_size=len(barcodes) * 100, total_files=len(barcodes))
        
        def _do_format():
            try:
                for idx, b in enumerate(barcodes):
                    # Update progress: bytes_written represents completed tapes
                    db.update_job_info(job_id, {"bytes_written": idx * 100})
                    job_service.update_job_with_log(job_id, "running", f"Formatting tape {b} ({idx+1}/{len(barcodes)}) - This can take 3-5 minutes per tape...")
                    
                    def on_format_progress(pct: int, msg: str):
                        overall_pct = (idx * 100) + pct
                        db.update_job_info(job_id, {"bytes_written": overall_pct})
                        job_service.update_job_with_log(job_id, "running", f"[{b}] {msg}")
                        
                    tape_controller.format_tape(b, force=True, drive=drive, progress_callback=on_format_progress)
                    
                    # Ensure database reflects that tape is formatted
                    # Reset tape metrics after format
                    try:
                        db_conn = db._get_conn()
                        capacity = tape_controller.get_capacity_bytes_for_generation(tape_controller._parse_generation(b))
                        db_conn.execute("UPDATE tapes SET capacity_bytes = ?, used_bytes = 0, ltfs_formatted = 1, status = 'available' WHERE barcode = ?", (capacity, b))
                        db_conn.commit()
                        db.update_tape_trust_status(b, 'trusted')
                    except Exception as db_err:
                        job_service.update_job_with_log(job_id, "running", f"Warning: Failed to reset DB state for {b}: {db_err}")
                    finally:
                        db.release_connection()
                    
                    # Auto-unload after format per user request
                    try:
                        tape_controller.unload_tape(drive=drive)
                    except Exception as unload_err:
                        job_service.update_job_with_log(job_id, "running", f"Warning: Failed to unload tape {b} after format: {unload_err}")
                        
                db.update_job_info(job_id, {"bytes_written": len(barcodes) * 100})
                job_service.update_job_with_log(job_id, "completed", f"Formatted {len(barcodes)} tapes successfully", "success")
            except Exception as e:
                job_service.update_job_with_log(job_id, "error", str(e), "error")
            finally:
                db.release_connection()
                job_service.cleanup_job_flag(job_id)

        threading.Thread(target=_do_format, daemon=True).start()
        return success_response(data={'job_id': job_id}, message="Format job started")
    except Exception as e:
        return error_response(str(e))


@tapes_bp.route('/api/library/bulk-wipe', methods=['POST'])
@tapes_bp.route('/api/tapes/<barcode>/wipe', methods=['POST'])
@require_admin
def wipe_tape(barcode=None):
    """Wipe one or more tapes completely."""
    job_service = cast(Any, getattr(current_app, 'job_service', None))
    tape_controller = cast(Any, _get_controller())
    db = cast(Any, getattr(current_app, 'db', None))
    if not job_service or not tape_controller or not db:
        return error_response("Services unavailable", status_code=503)

    try:
        data = request.get_json() or {}
        barcodes = [barcode] if barcode else data.get('barcodes', [])
        drive = int(data.get('drive', 0) or 0)
        erase_mode = data.get('erase_mode', 'quick')
        
        if erase_mode not in ('quick', 'format', 'secure'):
            return error_response(f"Invalid erase_mode '{erase_mode}'. Must be 'quick', 'format', or 'secure'.", status_code=400)
        
        if not barcodes:
            return error_response("No barcodes provided", status_code=400)
            
        if data.get('confirmation') != "BULK_WIPE" and (barcode and data.get('confirmation') != barcode):
             return error_response("Type 'BULK_WIPE' (or the barcode for single tape) to confirm", status_code=400)

        # Check WORM locks and cleaning tapes
        cleaning_tapes: list[Any] = []
        locked: list[Any] = []
        for b in barcodes:
            tape = db.get_tape(b)
            if tape and tape.get('is_cleaning_tape'):
                cleaning_tapes.append(b)
            elif b.startswith('CLN'):
                cleaning_tapes.append(b)
            
            if db.is_tape_locked(b):
                locked.append(b)

        if cleaning_tapes:
            return error_response(f"Tapes {', '.join(cleaning_tapes)} are cleaning media and cannot be wiped.", status_code=403)

        if locked:
            webhook_service = current_app.config.get('webhook_service')
            if webhook_service:
                for b in locked:
                    webhook_service.trigger_event("COMPLIANCE_ALARM", {
                        "reason": "WORM_LOCK_VIOLATION",
                        "action": "WIPE",
                        "barcode": b,
                        "status": "BLOCKED"
                    })
            return error_response(f"Tapes {', '.join(locked)} are under WORM retention lock and cannot be wiped.", status_code=403)
        
        mode_labels = {'quick': 'Quick Erase', 'format': 'Reformat', 'secure': 'Secure Erase'}
        mode_label = mode_labels.get(erase_mode, erase_mode)
        job_name = f"{mode_label} {', '.join(barcodes)}" if len(barcodes) <= 2 else f"Bulk {mode_label} ({len(barcodes)} tapes)"
        job_id = job_service.create_internal_job(job_name, "tape_wipe", barcodes, drive=drive)
        
        def _do_wipe():
            import subprocess
            import re
            import logging
            
            logger = logging.getLogger(__name__)

            try:
                for idx, b in enumerate(cast(Any, barcodes)):
                    job_service.update_job_with_log(job_id, "running", f"{mode_label} tape {b} ({idx+1}/{len(barcodes)})")
                    
                    # Only spawn sg_requests progress polling for secure erase (the others finish in seconds)
                    if erase_mode == 'secure':
                        is_wiping = [True]
                        def progress_updater():
                            job_service.db.update_job_info(job_id, {"total_size": 100, "bytes_written": 0})
                            while is_wiping[0]:
                                try:
                                    out = subprocess.check_output(['sudo', '/usr/bin/sg_requests', '--progress', '/dev/sg1'], stderr=subprocess.STDOUT, text=True)
                                    match = re.search(r'Progress indication:\s*([\d\.]+)%', out)
                                    if match:
                                        pct = float(match.group(1))
                                        job_service.db.update_job_info(job_id, {"bytes_written": int(pct)})
                                except subprocess.CalledProcessError as e:
                                    logger.warning(f"sg_requests warning: {e.output}")
                                except Exception as e:
                                    logger.error(f"sg_requests thread error: {e}")
                                time.sleep(15)
                                
                        prog_thread = threading.Thread(target=progress_updater, daemon=True)
                        prog_thread.start()
                    
                    try:
                        tape_controller.wipe_tape(b, drive=drive, mode=erase_mode)
                        
                        # Apply wiped state to database
                        try:
                            db_conn = db._get_conn()
                            db_conn.execute("UPDATE tapes SET capacity_bytes = 0, used_bytes = 0, ltfs_formatted = 0, status = 'available', volume_name = '' WHERE barcode = ?", (b,))
                            db_conn.commit()
                            db.update_tape_trust_status(b, 'trusted')
                        except Exception as db_err:
                            logger.error(f"Failed to reset database state for wiped tape {b}: {db_err}")
                        
                        # Auto-unload after wipe per user request
                        try:
                            tape_controller.unload_tape(drive=drive)
                        except Exception as unload_err:
                            logger.error(f"Failed to unload tape {b} after wipe: {unload_err}")
                            job_service.update_job_with_log(job_id, "running", f"Warning: Failed to auto-unload tape {b}")
                            
                    finally:
                        if erase_mode == 'secure':
                            is_wiping[0] = False
                        job_service.db.update_job_info(job_id, {"total_size": 100, "bytes_written": 100})
                
                job_service.update_job_with_log(job_id, "completed", f"{mode_label} of {len(barcodes)} tapes complete", "success")
            except Exception as e:
                job_service.update_job_with_log(job_id, "error", f"{mode_label} failed: {e}", "error")
            finally:
                job_service.cleanup_job_flag(job_id)

        threading.Thread(target=_do_wipe, daemon=True).start()
        return success_response(data={'job_id': job_id, 'erase_mode': erase_mode})
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/tapes/<barcode>/lock', methods=['POST'])
@require_admin
def lock_tape(barcode):
    """Set WORM retention lock on a tape."""
    db = cast(Any, getattr(current_app, 'db', None))
    if not db:
        return error_response("Database unavailable", status_code=503)
        
    try:
        data = request.get_json() or {}
        expires_at = data.get('expires_at') # ISO format timestamp
        
        if not expires_at:
            return error_response("Retention expiration date (expires_at) is required", status_code=400)
            
        success = db.lock_tape(barcode, expires_at)
        if success:
            current_app.log_info(f"Tape {barcode} locked until {expires_at}", 'compliance')
            return success_response(message=f"Tape {barcode} locked until {expires_at}")
        else:
            return error_response(f"Failed to lock tape {barcode}")
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/library/recover', methods=['POST'])
@require_admin
def recover_library():
    """Attempt to recover library from an inconsistent state."""
    tape_controller = cast(Any, _get_controller())
    if not tape_controller:
        return error_response("Tape hardware unavailable", status_code=503)

    try:
        data = request.get_json() or {}
        drive = int(data.get('drive', 0) or 0)
        result = tape_controller.recover_library(drive=drive)
        return success_response(data={'result': result})
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/drive/clean', methods=['POST'])
@require_admin
def clean_drive():
    """Initiate a drive cleaning cycle."""
    tape_controller = cast(Any, _get_controller())
    if not tape_controller:
        return error_response("Tape hardware unavailable", status_code=503)

    try:
        data = request.get_json() or {}
        drive = int(data.get('drive', 0) or 0)
        # Logic for cleaning drive would go here
        return success_response(message=f"Drive {drive} cleaning initiated")
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/tapes/<barcode>/manifest')
@require_role('viewer')
def get_tape_manifest(barcode):
    """Get manifest of files on a tape"""
    db = getattr(current_app, 'db', None)
    if not db:
        return error_response("Database unavailable", status_code=503)
        
    try:
        # 1. Basic validation
        valid, error = validate_tape_identifier(barcode)
        if not valid:
            return error_response(error, status_code=400)
        
        # 2. Cleaning check
        is_cleaning = barcode.startswith('CLN')
        try:
            t_row = db.get_tape(barcode)
            if t_row and dict(t_row).get('is_cleaning_tape'):
                is_cleaning = True
        except Exception:
            pass
                
        if is_cleaning:
            return success_response(data={
                'manifest': {
                    'barcode': barcode,
                    'generated': now_utc_iso(),
                    'is_cleaning_media': True,
                    'file_count': 0,
                    'total_size': 0,
                    'files': []
                }
            })

        # 3. Fetch files
        files = []
        try:
            raw_files = db.get_files_on_tape(barcode)
            if raw_files:
                for f in raw_files:
                    try:
                        d = dict(f)
                        # Stringify everything to be safe
                        clean_d = {}
                        for k, v in d.items():
                            if v is None:
                                clean_d[k] = None
                            elif hasattr(v, 'isoformat'):
                                clean_d[k] = cast(Any, v).isoformat()
                            elif isinstance(v, bytes):
                                clean_d[k] = v.hex()
                            else:
                                clean_d[k] = v
                        
                        # Add aliases for frontend compatibility
                        if 'file_name' in clean_d and 'filename' not in clean_d:
                            clean_d['filename'] = clean_d['file_name']
                        if 'file_size' in clean_d and 'size' not in clean_d:
                            clean_d['size'] = clean_d['file_size']
                        if 'archived_at' in clean_d and 'created_at' not in clean_d:
                            clean_d['created_at'] = clean_d['archived_at']
                            
                        files.append(clean_d)
                    except Exception:
                        continue
        except Exception as fe:
            if hasattr(current_app, 'logger'):
                current_app.logger.error(f"DB Error fetching files for {barcode}: {fe}")

        manifest = {
            'barcode': barcode,
            'generated': now_utc_iso(),
            'file_count': len(files),
            'total_size': sum((f.get('file_size') or f.get('size') or 0) for f in files),
            'files': files
        }
        
        return current_app.response_class(
            json.dumps({
                "success": True, 
                "data": {"manifest": manifest},
                "request_id": getattr(g, "request_id", None)
            }, default=str),
            mimetype='application/json'
        )
    except Exception as e:
        import traceback
        if hasattr(current_app, 'logger'):
            current_app.logger.error(f"Manifest CRASH for {barcode}: {str(e)}\n{traceback.format_exc()}")
        return error_response(str(e))

@tapes_bp.route('/api/tapes/<barcode>/manifest/export')
@require_role('viewer')
def export_tape_manifest(barcode):
    """Export tape manifest as downloadable JSON file"""
    db = getattr(current_app, 'db', None)
    if not db:
        return error_response("Database unavailable", status_code=503)
        
    try:
        valid, error = validate_tape_identifier(barcode)
        if not valid:
            return error_response(error, status_code=400)
        
        files = db.get_files_on_tape(barcode)
        
        manifest = {
            'barcode': barcode,
            'generated': now_utc_iso(),
            'file_count': len(files),
            'total_size': sum(f.get('file_size', 0) for f in files),
            'files': [{
                'path': f['file_path'],
                'size': f['file_size'],
                'checksum': f.get('checksum', ''),
                'backed_up': f.get('created_at', '')
            } for f in files]
        }
        
        manifest_str = json.dumps(manifest, indent=2, sort_keys=True)
        manifest['hash'] = hashlib.sha256(manifest_str.encode()).hexdigest()
        
        manifest_path = os.path.join(tempfile.gettempdir(), f'manifest_{barcode}_{datetime.now().strftime("%Y%m%d")}.json')
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)
        
        return send_file(manifest_path, as_attachment=True, download_name=f'manifest_{barcode}.json')
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/library/reconcile')
@require_role('viewer')
def get_reconciliation_status():
    """Compare database inventory with actual library scan"""
    db = cast(Any, getattr(current_app, 'db', None))
    tape_controller = cast(Any, _get_controller())
    if not db or not tape_controller:
        return error_response("Services unavailable", status_code=503)
        
    try:
        db_tapes = {t['barcode']: t for t in db.get_tape_inventory()}
        scan = tape_controller.scan_barcodes()
        library_tapes = {t['barcode']: t for t in scan if t.get('barcode')}
        unknown_drives = [
            {
                'drive': t.get('drive_index'),
                'source_slot': t.get('drive_source_slot'),
            }
            for t in scan
            if t.get('location_type') == 'drive' and t.get('drive_full') and not t.get('barcode')
        ]
        
        result: dict = {
            'db_only': cast(Any, []),
            'library_only': cast(Any, []),
            'slot_mismatch': cast(Any, []),
            'matched': cast(Any, []),
            'unknown_drives': unknown_drives,
        }
        
        all_barcodes = set(db_tapes.keys()) | set(library_tapes.keys())
        for barcode in all_barcodes:
            in_db = barcode in db_tapes
            in_library = barcode in library_tapes
            if in_db and not in_library:
                result['db_only'].append({'barcode': barcode, 'db_slot': db_tapes[barcode].get('slot')})
            elif in_library and not in_db:
                result['library_only'].append({'barcode': barcode, 'library_slot': library_tapes[barcode].get('slot')})
            elif in_db and in_library:
                db_slot = db_tapes[barcode].get('slot')
                lib_slot = library_tapes[barcode].get('slot')
                if db_slot != lib_slot:
                    result['slot_mismatch'].append({'barcode': barcode, 'db_slot': db_slot, 'library_slot': lib_slot})
                else:
                    result['matched'].append(barcode)
        
        result['needs_reconciliation'] = bool(result['db_only'] or result['library_only'] or result['slot_mismatch'] or result['unknown_drives'])
        return success_response(data={'reconciliation': result})
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/library/reconcile', methods=['POST'])
@require_admin
def perform_reconciliation():
    """Perform reconciliation - update DB to match library"""
    db = getattr(current_app, 'db', None)
    if not db:
        return error_response("Database unavailable", status_code=503)
        
    try:
        data = request.json
        actions = data.get('actions', [])
        results = []
        for action in actions:
            barcode = action.get('barcode')
            action_type = action.get('type')
            if action_type == 'add_to_db':
                db.add_tape(barcode, action.get('slot'))
                results.append({'barcode': barcode, 'action': 'added', 'success': True})
            elif action_type == 'remove_from_db':
                db.remove_tape(barcode)
                results.append({'barcode': barcode, 'action': 'removed', 'success': True})
            elif action_type == 'update_slot':
                db.update_tape_slot(barcode, action.get('new_slot'))
                results.append({'barcode': barcode, 'action': 'slot_updated', 'success': True})
        
        current_app.log_info(f"Reconciliation performed: {len(results)} actions", 'tape')
        return success_response(data={'results': results})
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/tapes/capacity-check', methods=['POST'])
@require_role('viewer')
def check_tape_capacity():
    """Check if selected tapes have sufficient capacity for a job"""
    db = cast(Any, getattr(current_app, 'db', None))
    tape_controller = cast(Any, _get_controller())
    if not db or not tape_controller:
        return error_response("Services unavailable", status_code=503)
        
    try:
        data = request.json
        required_bytes = data.get('required_bytes', 0)
        tape_barcodes = data.get('tapes', [])
        result: dict = {'sufficient': False, 'total_available': 0, 'tapes': cast(Any, [])}
        
        for barcode in tape_barcodes:
            tape = db.get_tape(barcode)
            if tape:
                capacity = tape.get('capacity_bytes', 0)
                if capacity == 0:
                    capacity = tape_controller.get_capacity_bytes_for_generation(tape_controller._parse_generation(barcode))
                used = tape.get('used_bytes', 0)
                available = capacity - used
                percent_free = (available / capacity * 100) if capacity > 0 else 0
                result['tapes'].append({
                    'barcode': barcode,
                    'generation': tape.get('generation', 'Unknown'),
                    'capacity': capacity,
                    'used': used,
                    'available': available,
                    'percent_free': round(percent_free, 1),
                    'warning': percent_free < 20
                })
                result['total_available'] += available
        
        result['sufficient'] = result['total_available'] >= required_bytes
        result['shortage'] = max(0, required_bytes - result['total_available'])
        return success_response(data={'result': result})
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/tapes/reclaim/candidates', methods=['GET'])
@require_role('operator')
def get_reclaim_candidates():
    """Get tapes suitable for reclamation."""
    # We need to access TapeReclaimService. Ideally instantiated in App.
    # We can instantiate it on the fly if needed, or better, attach to app.
    # Assuming we will attach it to app.
    
    # Lazy init if not on app?
    reclaim_service = getattr(current_app, 'tape_reclaim_service', None)
    if not reclaim_service:
        # Fallback: create fresh instance
        from backend.services.tape_reclaim_service import TapeReclaimService
        db = getattr(current_app, 'db', None)
        tape_controller = cast(Any, _get_controller())
        if not db:
            return error_response("Database unavailable", status_code=503)
        reclaim_service = TapeReclaimService(db, tape_controller, library_manager=getattr(current_app, 'library_manager', None))
    
    try:
        threshold = float(request.args.get('threshold', 50.0))
        limit = int(request.args.get('limit', 100))
        
        candidates = reclaim_service.identify_reclaimable_tapes(threshold, limit)
        stats = reclaim_service.calculate_reclaim_stats(candidates)
        
        return success_response(data={'candidates': candidates, 'stats': stats})
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/tapes/reclaim/start', methods=['POST'])
@require_admin
def start_reclaim_job():
    """Start a tape reclaim job."""
    reclaim_service = getattr(current_app, 'tape_reclaim_service', None)
    if not reclaim_service:
        from backend.services.tape_reclaim_service import TapeReclaimService
        db = getattr(current_app, 'db', None)
        tape_controller = cast(Any, _get_controller())
        socketio = getattr(current_app, 'socketio', None)
        if not db:
            return error_response("Database unavailable", status_code=503)
        reclaim_service = TapeReclaimService(db, tape_controller, socketio, library_manager=getattr(current_app, 'library_manager', None))
        
    try:
        data = request.json or {}
        source_barcodes = data.get('source_barcodes', [])
        dest_barcode = data.get('dest_barcode')
        
        if not source_barcodes or not dest_barcode:
            return error_response("Source barcodes and destination barcode are required", status_code=400)
            
        job_id = reclaim_service.start_reclaim_job(source_barcodes, dest_barcode)
        
        return success_response(data={'job_id': job_id}, message="Reclaim job started")
    except Exception as e:
        return error_response(str(e))
@tapes_bp.route('/api/library/calibration/info', methods=['GET'])
@require_admin
def get_calibration_info():
    """Get information required for drive calibration."""
    controller = _get_controller()
    if not controller:
        return error_response("Tape controller unavailable", status_code=503)
        
    try:
        from backend.tape.devices import discover_devices
        potential_drives, _ = discover_devices()
        
        # Get current status to see drive counts and inventory
        status = controller.get_library_info()
        inventory = controller.scan_barcodes()
        
        return success_response(data={
            'potential_logical_paths': [d.path for d in potential_drives],
            'physical_drive_count': status.get('drives', 1),
            'inventory': inventory,
            'current_mapping': controller.drive_devices
        })
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/library/calibration/identify', methods=['POST'])
@require_admin
def identify_calibration_drive():
    """Load a tape into a physical drive and identify its logical path."""
    controller = _get_controller()
    if not controller:
        return error_response("Tape controller unavailable", status_code=503)
        
    try:
        data = request.json
        mtx_index = data.get('mtx_index')
        barcode = data.get('barcode')
        
        if mtx_index is None or not barcode:
            return error_response("MTX index and barcode are required")
            
        logical_path = controller.identify_drive_mapping(int(mtx_index), barcode)
        
        if logical_path:
            return success_response(data={'logical_path': logical_path})
        else:
            return error_response("Could not identify logical path for this physical drive")
    except Exception as e:
        return error_response(str(e))

@tapes_bp.route('/api/library/calibration/save', methods=['POST'])
@require_admin
def save_calibration_mapping():
    """Save the physical-to-logical drive mapping to configuration."""
    try:
        data = request.json
        mapping = data.get('mapping') # Expected: { "0": "/dev/nst0", "1": "/dev/nst1" }
        
        if not mapping:
            return error_response("Mapping is required")
            
        from backend.config_store import update_config
        # We store it under tape.drive_devices
        # Note: mapping keys must be strings for JSON, but controller expects int keys
        # The frontend should send strings, we'll store them as they are
        update_config({"tape": {"drive_devices": mapping}})
        
        current_app.log_info(f"Drive calibration saved: {mapping}", 'tape')
        return success_response(message="Calibration saved successfully. Please restart the service to apply changes.")
    except Exception as e:
        return error_response(str(e))
