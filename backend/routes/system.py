from flask import Blueprint, jsonify
from backend.auth import require_role
import os
import time

# Track application start time for uptime calculation
_start_time = time.time()

system_bp = Blueprint('system', __name__)

def _get_uptime():
    """Return application uptime in seconds."""
    return time.time() - _start_time

@system_bp.route('/api/system/info')
@require_role('viewer')
def get_system_info():
    """Get general system info"""
    return jsonify({
        'version': '1.0.0', # TODO: Get from package
        'hostname': os.uname().nodename,
        'uptime': _get_uptime(),
        'load_avg': os.getloadavg(),
        'hardware_available': True, 
        'hardware_reason': None,
    })

@system_bp.route('/api/system/libraries')
@require_role('viewer')
def list_libraries():
    """List all configured tape libraries."""
    from flask import current_app
    library_manager = getattr(current_app, 'library_manager', None)
    
    if not library_manager:
        # Fallback for single library mode
        tape_controller = getattr(current_app, 'tape_controller', None)
        if tape_controller:
             return jsonify({
                 'success': True, 
                 'data': [{'id': 'default', 'display_name': 'Default Library', 'is_default': True}]
             })
        return jsonify({'success': True, 'data': []})
        
    libraries = []
    for lib_id, controller in library_manager.controllers.items():
        libraries.append({
            'id': lib_id,
            'display_name': f"Library {lib_id}", # Or check config for custom name
            'is_default': lib_id == library_manager.default_library_id
        })
        
    return jsonify({'success': True, 'data': sorted(libraries, key=lambda x: x['id'])})

@system_bp.route('/api/system/ssh-key')
@require_role('viewer')
def get_ssh_public_key():
    """Get appliance public SSH key"""
    from backend.sources.ssh_source import SSHSource
    key = SSHSource.get_public_key()
    return jsonify({'success': True, 'public_key': key})

@system_bp.route('/api/system/stats')
@require_role('viewer')
def get_system_stats():
    """Get real-time system resource usage."""
    from flask import current_app
    current_app.log_info("get_system_stats called", "system")
    
    db = getattr(current_app, 'db', None)
    tape_controller = getattr(current_app, 'tape_controller', None)
    
    # Initialize defaults
    cpu_percent = 0.0
    ram_percent = 0.0
    cache_disk_percent = 0.0
    total_capacity = 0
    used_capacity = 0
    tapes_online = 0
    total_slots = 0
    mailslot_enabled = False

    # Force fallback for stability debugging
    # try:
    #     import psutil
    #     current_app.log_info("Using psutil", "system")
    #     cpu_percent = psutil.cpu_percent(interval=None)
    #     mem = psutil.virtual_memory()
    #     ram_percent = mem.percent
    #     
    #     data_dir = '/var/lib/fossilsafe'
    #     if not os.path.exists(data_dir):
    #         data_dir = '/tmp'
    #         
    #     disk = psutil.disk_usage(data_dir)
    #     cache_disk_percent = disk.percent
    # except ImportError:
    
    current_app.log_info("Using fallback stats", "system")
    # Fallback if psutil not installed
    try:
        load = os.getloadavg()
        # approximations
        cpu_percent = (load[0] / os.cpu_count()) * 100 if os.cpu_count() else 0
    except:
        pass

    # RAM fallback via /proc/meminfo
    try:
        with open('/proc/meminfo', 'r') as f:
            meminfo = {}
            for line in f:
                parts = line.split(':')
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().split()[0]  # value in kB
                    meminfo[key] = int(val)
            mem_total = meminfo.get('MemTotal', 0)
            mem_available = meminfo.get('MemAvailable', 0)
            if mem_total > 0:
                ram_percent = ((mem_total - mem_available) / mem_total) * 100
    except:
        pass
        
    # Disk fallback
    try:
        import shutil
        total, used, free = shutil.disk_usage('/')
        cache_disk_percent = (used / total) * 100
    except:
        pass

    # Get Tape Stats
    if tape_controller:
        try:
            current_app.log_info("Getting tape stats", "system")
            # We can get slots from controller or DB
            # Use DB inventory if available as it's faster
            if db:
                tapes = db.get_tape_inventory()
                tapes_online = len([t for t in tapes if t.get('status') != 'offline'])
                total_slots = len(tapes) # Approximation if library info not available
                
                # Calculate capacity from tapes
                total_capacity = sum(t.get('capacity_bytes', 0) for t in tapes)
                used_capacity = sum(t.get('used_bytes', 0) for t in tapes)
                
                # Check for active hardware-locking jobs (wipe/format)
                active_jobs = db.get_active_jobs()
                hardware_locked = any(j.get('job_type') in ('tape_wipe', 'tape_format', 'tape_move', 'tape_import', 'tape_export') for j in active_jobs)
            
            # Get Library Info for precise slot count
            # Use getattr to avoid crash if method missing
            if hasattr(tape_controller, 'get_library_info'):
                 # Skip hardware polling if a bus-locking operation is running
                 if db and hardware_locked:
                     current_app.log_info("Skipping get_library_info - bus is locked by active erase/format operation", "system")
                 else:
                     lib_info = tape_controller.get_library_info()
                     if lib_info:
                         total_slots = lib_info.get('slots', total_slots)
                         mailslot_enabled = lib_info.get('mailslot_enabled', False)
                         mailslot_count = lib_info.get('mailslot_count', 0)
                
        except Exception as e:
            current_app.log_error(f"Error getting tape stats: {e}", "system")

    current_app.log_info("Returning system stats", "system")
    return jsonify({
        'success': True,
        'data': {
            'cpu_percent': round(cpu_percent, 1),
            'ram_percent': round(ram_percent, 1),
            'cache_disk_percent': round(cache_disk_percent, 1),
            'total_capacity_bytes': total_capacity,
            'used_capacity_bytes': used_capacity,
            'tapes_online': tapes_online,
            'total_slots': total_slots,
            'mailslot_enabled': mailslot_enabled,
            'mailslot_count': mailslot_count
        }
    })


@system_bp.route('/api/system/mailslots', methods=['GET'])
@require_role('admin')
def get_mailslots_config():
    """Get mail slot configuration preferences."""
    from backend.config_store import get_mail_slot_preferences
    return jsonify({
        'success': True,
        'data': get_mail_slot_preferences()
    })


@system_bp.route('/api/system/mailslots', methods=['POST'])
@require_role('admin')
def update_mailslots_config():
    """Update mail slot configuration preferences."""
    from flask import request
    from backend.config_store import update_config
    
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No data provided'}), 400
        
    # We expect a subset of the preferences dict
    # We wrap it in the 'tape' key for config_store compatibility
    update_payload = {"tape": {"mail_slots": data}}
    
    try:
        update_config(update_payload)
        return jsonify({'success': True, 'message': 'Mail slot configuration updated'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@system_bp.route('/api/system/metrics/prometheus')
@require_role('viewer')
def get_prometheus_metrics():
    """Get system metrics in Prometheus format."""
    from flask import current_app, Response
    
    # Check if metrics service is available
    metrics_service = getattr(current_app, 'metrics_service', None)
    if not metrics_service:
        return Response(
            "# Metrics service unavailable", 
            mimetype='text/plain', 
            status=503
        )
        
    try:
        metrics_data = metrics_service.get_prometheus_metrics()
        return Response(metrics_data, mimetype='text/plain')
    except Exception as e:
        current_app.log_error(f"Error generating metrics: {e}", "system")
        return Response(
            f"# Error generating metrics: {str(e)}", 
            mimetype='text/plain', 
            status=500
        )

# Streaming Pipeline Endpoints
@system_bp.route('/api/system/streaming', methods=['GET'])
@require_role('admin')
def get_streaming_config():
    """Get streaming pipeline configuration."""
    from flask import current_app
    from backend.streaming_pipeline import get_streaming_config
    
    config = get_streaming_config(current_app.db)
    return jsonify({'success': True, 'data': config.__dict__})

@system_bp.route('/api/system/streaming', methods=['POST'])
@require_role('admin')
def update_streaming_config():
    """Update streaming pipeline configuration."""
    from flask import request, current_app
    
    data = request.get_json()
    db = current_app.db
    
    try:
        if 'enabled' in data:
            db.set_setting('streaming_backup_enabled', str(data['enabled']).lower())
            
        if 'max_queue_size_gb' in data:
             db.set_setting('streaming_max_queue_gb', str(data['max_queue_size_gb']))
             
        if 'max_queue_files' in data:
            db.set_setting('streaming_max_queue_files', str(data['max_queue_files']))
            
        if 'producer_threads' in data:
            db.set_setting('streaming_producer_threads', str(data['producer_threads']))
            
        return jsonify({'success': True, 'message': 'Streaming configuration updated'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@system_bp.route('/api/system/health/drives')
@require_role('viewer')
def get_drives_health():
    """Get predictive health scores for all tape drives."""
    from flask import current_app
    health_service = getattr(current_app, 'health_service', None)
    tape_controller = getattr(current_app, 'tape_controller', None)
    db = getattr(current_app, 'db', None)
    
    if not health_service or not tape_controller:
        return jsonify({'success': False, 'error': 'Health service unavailable'})
        
    try:
        # Check for active hardware-locking jobs
        active_jobs = db.get_active_jobs() if db else []
        hardware_locked = any(j.get('job_type') in ('tape_wipe', 'tape_format') for j in active_jobs)
        if hardware_locked:
            return jsonify({'success': True, 'data': {}})
            
        # Get all configured drive device paths
        drive_paths = list(tape_controller.drive_devices.values())
        health_data = health_service.get_all_drives_health(drive_paths)
        
        return jsonify({
            'success': True,
            'data': health_data
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
