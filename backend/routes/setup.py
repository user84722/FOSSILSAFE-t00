from flask import Blueprint, request, jsonify, current_app
from backend.utils.responses import success_response, error_response
from backend.auth import get_auth_manager, require_role
from backend.utils.validation import validate_key_format
import time
import threading
import logging

logger = logging.getLogger(__name__)

setup_bp = Blueprint('setup', __name__)

# Global state for background tape initialization
_init_status = {
    'running': False,
    'current': 0,
    'total': 0,
    'last_barcode': None,
    'error': None,
    'complete': False
}
_init_lock = threading.Lock()

@setup_bp.route('/api/auth/setup-status', methods=['GET'])
def get_setup_status():
    """Check if initial setup is required."""
    print("DEBUG: get_setup_status called")
    auth_mgr = get_auth_manager()
    from backend.config_store import load_config
    try:
        setup_required = auth_mgr.is_setup_required()
        config = load_config()
        setup_mode = config.get('setup_mode', 'relaxed')
        
        print(f"DEBUG: setup_required = {setup_required}, mode = {setup_mode}")
        return success_response(data={
            'setup_required': setup_required,
            'setup_mode': setup_mode
        })
    except Exception as e:
        print(f"DEBUG: get_setup_status error: {e}")
        return error_response(str(e))

@setup_bp.route('/api/auth/setup', methods=['POST'])
def complete_setup():
    """Complete initial setup by creating the first admin user."""
    auth_mgr = get_auth_manager()
    from backend.config_store import load_config
    try:
        if not auth_mgr.is_setup_required():
            return error_response("Setup already completed", status_code=403)
            
        data = request.get_json() or {}
        username = data.get('username')
        password = data.get('password')
        api_key = data.get('api_key')
        
        # Security Check
        config = load_config()
        setup_mode = config.get('setup_mode', 'relaxed')
        
        if setup_mode == 'secure':
            expected_key = config.get('api_key')
            if not expected_key:
                # Should not happen if installed correctly, but fail safe
                return error_response("System misconfiguration: Secure mode enabled but no API key set.", status_code=500)
            
            if not api_key or api_key.strip() != expected_key.strip():
                return error_response("Invalid API Key for secure setup.", status_code=401)
        
        if not username or not password:
             return error_response("Username and password are required", status_code=400)
             
        # Create admin user
        # Note: We rely on AuthManager to allow creation if setup is required
        user_id = auth_mgr.create_user(username, password, role='admin')
        if not user_id:
             return error_response("Failed to create admin user", status_code=500)
             
        # Log them in immediately
        auth_mgr = get_auth_manager() # Re-fetch to be safe
        token = auth_mgr.login(username, password)
        if not token:
             return error_response("Login failed after creation", status_code=500)
             
        # Return standard login response structure expected by frontend
        user = auth_mgr.get_user(username)
        return success_response(data={
            'token': token,
            'username': user.username,
            'role': user.role,
            'id': user.id,
            'has_2fa': False 
        })
        
    except Exception as e:
        return error_response(str(e))

@setup_bp.route('/api/setup/tape-status', methods=['GET'])
def get_setup_tape_status():
    """Get library and tape status for initialization step."""
    try:
        ctrl = getattr(current_app, 'tape_controller', None)
        if not ctrl:
            return success_response(data={'has_library': False, 'tapes': [], 'count': 0})
        
        if ctrl.is_drive_only():
            return success_response(data={'has_library': False, 'tapes': [], 'count': 0})
        
        inventory = ctrl.inventory()
        tapes = [t for t in inventory if t.get('barcode') and not t.get('is_cleaning_tape')]
        
        # Check if any tapes are already initialized (rough check)
        db = getattr(current_app, 'db', None)
        if not db:
            return success_response(data={'has_library': True, 'tapes': tapes, 'count': len(tapes), 'already_initialized': False, 'initialized_count': 0})
        initialized_count = 0
        for t in tapes:
            db_tape = db.get_tape(t['barcode'])
            if db_tape and db_tape.get('ltfs_formatted'):
                initialized_count += 1
        
        return success_response(data={
            'has_library': True,
            'tapes': tapes,
            'count': len(tapes),
            'already_initialized': initialized_count > 0,
            'initialized_count': initialized_count
        })
    except Exception as e:
        return error_response(str(e))

@setup_bp.route('/api/setup/tape-init', methods=['POST'])
def start_tape_init():
    """Start bulk tape initialization in the background."""
    global _init_status
    with _init_lock:
        if _init_status['running']:
            return error_response("Initialization already in progress", status_code=409)
        
        ctrl = getattr(current_app, 'tape_controller', None)
        if not ctrl or ctrl.is_drive_only():
            return error_response("No tape library detected")
        
        inventory = ctrl.inventory()
        tapes = [t for t in inventory if t.get('barcode') and not t.get('is_cleaning_tape')]
        
        if not tapes:
            return error_response("No tapes found in library")
        
        # Reset status
        _init_status = {
            'running': True,
            'current': 0,
            'total': len(tapes),
            'last_barcode': None,
            'error': None,
            'complete': False
        }
        
        # Start thread
        thread = threading.Thread(target=_run_bulk_init, args=(current_app._get_current_object(), tapes))
        thread.daemon = True
        thread.start()
        
        return success_response(message="Tape initialization started")

@setup_bp.route('/api/setup/tape-init/status', methods=['GET'])
def get_tape_init_status():
    """Poll progress of tape initialization."""
    return success_response(data=_init_status)

def _run_bulk_init(app, tapes):
    """Background worker for bulk initialization."""
    global _init_status
    try:
        with app.app_context():
            ctrl = getattr(app, 'tape_controller', None)
            if not ctrl:
                with _init_lock:
                    _init_status['error'] = "Tape controller lost during initialization"
                    _init_status['running'] = False
                return

            for i, tape in enumerate(tapes):
                barcode = tape['barcode']
                with _init_lock:
                    _init_status['current'] = i + 1
                    _init_status['last_barcode'] = barcode
                
                try:
                    logger.info(f"Setup Wizard: Initializing tape {barcode} ({i+1}/{len(tapes)})")
                    # Force format as this is a fresh setup request
                    ctrl.format_tape(barcode, force=True)
                    # Unload explicitly after format to be ready for next or to leave drive clean
                    ctrl.unload_tape() 
                except Exception as e:
                    logger.error(f"Setup Wizard: Failed to initialize tape {barcode}: {e}")
                    # We continue with other tapes even if one fails?
                    # For a "bulletproof" appliance, maybe we should stop and report?
                    # Let's stop on first error to be safe, as it might indicate hardware issue.
                    with _init_lock:
                        _init_status['error'] = f"Failed at {barcode}: {str(e)}"
                        _init_status['running'] = False
                    return

            with _init_lock:
                _init_status['complete'] = True
                _init_status['running'] = False
                
    except Exception as e:
        logger.error(f"Setup Wizard: Bulk initialization crashed: {e}")
        with _init_lock:
            _init_status['error'] = f"Critical error: {str(e)}"
            _init_status['running'] = False
