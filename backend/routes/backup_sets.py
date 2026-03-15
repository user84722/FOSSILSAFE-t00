from flask import Blueprint, jsonify, current_app, request
from backend.auth import require_auth, require_role, require_admin
from backend.utils.responses import success_response, error_response
import json

backup_sets_bp = Blueprint('backup_sets', __name__, url_prefix='/api/backup-sets')

@backup_sets_bp.route('', methods=['GET'])
@require_auth
@require_role('viewer')
def list_backup_sets():
    """List all logical backup sets."""
    try:
        db = current_app.db
        backup_sets = db.get_backup_sets()
        return success_response(data={'backup_sets': backup_sets})
    except Exception as e:
        return error_response(str(e), code="db_error", status_code=500)

@backup_sets_bp.route('/<backup_set_id>', methods=['GET'])
@require_auth
@require_role('viewer')
@require_admin
def get_backup_set_details(backup_set_id):
    """Get details for a specific backup set."""
    try:
        db = current_app.db
        backup_set = db.get_backup_set(backup_set_id)
        if not backup_set:
            return error_response("Backup set not found", code="not_found", status_code=404)
        
        snapshots = db.get_backup_snapshots(backup_set_id)
        return success_response(data={
            'backup_set': backup_set,
            'snapshots': snapshots
        })
    except Exception as e:
        return error_response(str(e), code="db_error", status_code=500)

@backup_sets_bp.route('/<backup_set_id>/graph', methods=['GET'])
@require_auth
@require_role('viewer')
@require_admin
def get_backup_set_graph(backup_set_id):
    """Generate topology graph data for a backup set."""
    try:
        db = current_app.db
        backup_set = db.get_backup_set(backup_set_id)
        if not backup_set:
            return error_response("Backup set not found", code="not_found", status_code=404)
        
        snapshots = db.get_backup_snapshots(backup_set_id)
        
        nodes = []
        edges = []
        
        # Root node
        root_id = f"set_{backup_set_id}"
        nodes.append({
            "id": root_id,
            "type": "set",
            "label": f"Set: {backup_set_id}",
            "data": backup_set
        })
        
        tapes_added = set()
        
        for snap in snapshots:
            snap_id = f"snap_{snap['id']}"
            nodes.append({
                "id": snap_id,
                "type": "snapshot",
                "label": snap['created_at'][:10], # Just the date
                "data": snap
            })
            
            # Link snapshot to set
            edges.append({
                "id": f"e_{root_id}_{snap_id}",
                "source": root_id,
                "target": snap_id,
                "label": "snapshot"
            })
            
            # Tapes for this snapshot
            tape_map = snap.get('tape_map', {})
            for barcode in tape_map.keys():
                tape_id = f"tape_{barcode}"
                if barcode not in tapes_added:
                    # Fetch tape details for health/trust
                    tape = db.get_tape(barcode)
                    trust = "unknown"
                    if tape:
                        if tape.get('ltfs_verified_at'):
                            trust = "verified"
                        if tape.get('status') == 'available':
                            trust = "online"
                    
                    nodes.append({
                        "id": tape_id,
                        "type": "tape",
                        "label": barcode,
                        "trust": trust,
                        "data": tape
                    })
                    tapes_added.add(barcode)
                
                # Link tape to snapshot
                edges.append({
                    "id": f"e_{snap_id}_{tape_id}",
                    "source": snap_id,
                    "target": tape_id,
                    "label": "stored on"
                })
        
        return success_response(data={'graph': {
                'nodes': nodes,
                'edges': edges
            }})
    except Exception as e:
        current_app.logger.exception("Graph generation failed")
        return error_response(str(e), code="graph_error", status_code=500)
