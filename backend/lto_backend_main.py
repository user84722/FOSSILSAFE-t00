#!/usr/bin/env python3
"""
LTO Tape Library Backup System - Main Application
Bulletproof backup from SMB to LTO tape library

Supports common homelab tape libraries including:
- HP/HPE StoreEver series (MSL2024, MSL4048, MSL6480)
- Dell PowerVault TL series (TL1000, TL2000, TL4000)
- IBM TS series (TS2900, TS3100, TS3200)
- Quantum Scalar series
- Fujitsu Eternus LT series
- Overland NEO series
- Any library with mtx support

Enhanced with:
- Server-side log filtering and pagination
- Preflight checks before job start
- Health check endpoints
- Support bundle export
- Input validation
- Proper error handling
- Job checkpoint/resume capability
- Encrypted credential storage
- Global status banner
- Configuration export/import
"""

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
import atexit
import signal
from pathlib import Path
import json
import os

import subprocess
import shutil
import zipfile
import tempfile
import grp
import pwd
import stat
import re
import hashlib
import base64
import sqlite3
import sys
from backend.library_manager import LibraryManager
import importlib.util
import stat
import jinja2
import glob
import secrets
from typing import Optional, Dict, List, Tuple, Any, Union, cast

REQUIRED_PYTHON_DEPENDENCIES = {
    'flask': 'Flask (pip install Flask)',
    'flask_cors': 'Flask-CORS (pip install Flask-CORS)',
    'flask_socketio': 'Flask-SocketIO (pip install Flask-SocketIO)',
    'cryptography': 'cryptography (pip install cryptography)',
    'apscheduler': 'APScheduler (pip install APScheduler)'
}


def _validate_python_dependencies() -> list:
    missing = []
    for module_name, install_hint in REQUIRED_PYTHON_DEPENDENCIES.items():
        if importlib.util.find_spec(module_name) is None:
            missing.append(f"{module_name} missing - install via: {install_hint}")
    return missing


_skip_dep_check = os.environ.get("FOSSILSAFE_SKIP_DEP_CHECK", "").lower() in ("1", "true", "yes")
_missing_dependencies = [] if _skip_dep_check else _validate_python_dependencies()
if _missing_dependencies:
    print("Startup validation failed:", file=sys.stderr)
    for issue in _missing_dependencies:
        print(f"  - Missing Python dependency: {issue}", file=sys.stderr)
    raise SystemExit(1)



from flask import Flask, render_template, jsonify, request, send_file, after_this_request, g, send_from_directory
from werkzeug.exceptions import HTTPException
from flask_cors import CORS
from flask_socketio import SocketIO, emit, disconnect
from flask_wtf.csrf import CSRFProtect, generate_csrf
from cryptography.fernet import Fernet
from werkzeug.middleware.proxy_fix import ProxyFix


if __package__ is None:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import our modules
from backend.tape_controller import TapeLibraryController, LIBRARY_BUSY
from backend.backup_engine import BackupEngine
from backend.advanced_restore import AdvancedRestoreEngine
from backend.tape_duplication import TapeDuplicationEngine
from backend.streaming_pipeline import StreamingBackupPipeline, get_streaming_config
from backend.smb_client import SMBClient, SMBScanError
from backend.smb_selftest import create_smb_selftest_blueprint, SmbSelfTestDependencies
from backend.sources.local_source import LocalSource
from backend.sources.nfs_source import NFSSource
from backend.sources.ssh_source import SSHSource
from backend.sources.rclone_source import RcloneSource
from backend.database import Database
from backend.rate_limiter import RateLimiter
from backend.config_store import (
    load_config,
    load_state,
    update_state,
    ensure_state_file,
    get_config_path,
    get_state_path,
    get_data_dir,
    get_diagnostics_dir,
    get_credential_key_path,
    get_default_db_path,
    get_catalog_backup_dir,
)
from backend.diag import get_permission_snapshot, run_diagnostics as run_backend_diagnostics
from backend.scheduler import BackupScheduler
from backend.tape.devices import FOSSILSAFE_CHANGER_SYMLINK, discover_devices, get_devices, is_medium_changer_device
from backend.utils.responses import success_response, error_response

# Import blueprints
from backend.routes.auth import auth_bp
from backend.routes.logs import logs_bp
from backend.routes.system import system_bp
from backend.routes.tapes import tapes_bp
from backend.routes.jobs import jobs_bp
from backend.routes.files import files_bp
from backend.routes.restore import restore_bp
from backend.routes.recovery import recovery_bp
from backend.routes.external_catalog import external_catalog_bp
from backend.routes.setup import setup_bp
from backend.routes.audit import audit_bp
from backend.routes.verification import verification_bp
from backend.routes.preferences import preferences_bp
from backend.routes.uploads import uploads_bp
from backend.routes.webhooks import webhooks_bp
from backend.routes.backup_sets import backup_sets_bp

# Import services
from backend.services.tape_service import TapeService
from backend.services.job_service import JobService
from backend.services.file_service import FileService
from backend.services.webhook_service import init_webhook_service
from backend.services.restore_service import RestoreService
from backend.services.diagnostic_service import DiagnosticService
from backend.services.metrics_service import MetricsService
from backend.services.health_service import HealthService
from backend.log_manager import LogManager
from backend.routes.diagnostics import diagnostics_bp
from backend.auth import require_auth, require_role, require_admin, init_auth
from backend.utils.responses import success_response, error_response

# Initialize Flask app
# Serve static files from frontend/dist
gui_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if not os.path.exists(gui_dir):
    # Fallback for dev environment or wrong path
    gui_dir = os.path.join(os.path.dirname(__file__), "../frontend/dist")

print(f"DEBUG: gui_dir resolved to: {gui_dir}")
print(f"DEBUG: gui_dir exists: {os.path.exists(gui_dir)}")

# Disable default static handler to avoid conflict with SPA catch-all
# Even with static_folder=None, Flask might add default behavior. Move url_path out of way.
app = Flask(__name__, static_folder=None, static_url_path='/_unused_static')
from backend.utils.datetime import now_utc_iso


def _get_cors_origins() -> Optional[list]:
    allowed_origins = os.environ.get('FOSSILSAFE_CORS_ORIGINS')
    if not allowed_origins:
        config = load_config()
        allowed_origins = config.get('ALLOWED_ORIGINS') or config.get('allowed_origins')
        if isinstance(allowed_origins, list):
            origins = [str(origin).strip() for origin in allowed_origins if str(origin).strip()]
            return origins or None

CORS(app, resources={r"/api/*": {"origins": "*"}})
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# Compatibility route for Frontend 'Test connection' or status check
@app.route('/api/status', methods=['GET'])
def api_status_compat():
    """Compatibility endpoint for status checks."""
    return jsonify({
        "success": True, 
        "setup_complete": True, 
        "version": "1.0.0",
        "status": "online"
    })

# Secret Key for Sessions (Required for OIDC/SSO)
# In production, this should be set via environment variable or loaded from a secure file
# For appliance, we can generate a persistent one if not provided
_secret_key_path = os.path.join(get_data_dir(), '.flask_secret')
if os.environ.get('FOSSILSAFE_SECRET_KEY'):
    app.secret_key = os.environ.get('FOSSILSAFE_SECRET_KEY')
elif os.path.exists(_secret_key_path):
    with open(_secret_key_path, 'rb') as f:
        app.secret_key = f.read()
else:
    # Generate and save a new secret key
    try:
        _key = secrets.token_bytes(32)
        with open(_secret_key_path, 'wb') as f:
            f.write(_key)
        # Try to restrict permissions
        os.chmod(_secret_key_path, 0o600)
        app.secret_key = _key
    except Exception as e:
        print(f"Warning: Could not persist Flask secret key: {e}")
        app.secret_key = secrets.token_bytes(32)

# Components will be initialized later by initialize_app() or initialize_controllers()
socketio = None
_socketio_handlers_registered = False
_authorized_socketio_sids = set()
_auth_lock = threading.Lock()
db = None
db_unavailable_reason = None
source_manager = None
source_manager_unavailable_reason = None
smb_client = None
smb_unavailable_reason = None
tape_controller = None
backup_engine = None
advanced_restore_engine = None
duplication_engine = None
streaming_pipeline = None
scheduler = None
autopilot = None
preflight_checker = None
library_manager = None
tape_service = None
job_service = None
file_service = None
restore_service = None
verification_service = None
log_manager = None
checkpoint_manager = None
metrics_service = None
diagnostic_service = None
health_service = None
csrf = None
webhook_service = None
api_key_rate_limiter = None
_state_update_thread_started = False
_last_deep_scan_at = 0.0
_last_hardware_op_at = 0.0
HARDWARE_OP_COOLDOWN = 1.0  # seconds between hardware-modifying commands

@app.teardown_appcontext
def close_db_connection(exception):
    """Return database connection to pool after request."""
    if db:
        db.release_connection()

# =============================================================================
# Startup Validation
# =============================================================================

def _find_existing_parent(path: Path) -> Optional[Path]:
    current = path
    while not current.exists():
        if current.parent == current:
            return None
        current = current.parent
    return current


def _validate_writable_file_path(path: Path, description: str, errors: list,
                                 allow_parent_create: bool = False):
    if path.exists():
        if not os.access(path, os.W_OK):
            errors.append(f"{description} at '{path}' is not writable. "
                          f"Adjust permissions or choose a different path.")
        return

    parent = path.parent
    if not parent.exists():
        if allow_parent_create:
            existing_parent = _find_existing_parent(parent)
            if existing_parent is None or not os.access(existing_parent, os.W_OK):
                errors.append(f"{description} directory '{parent}' does not exist and "
                              "cannot be created. Create it and ensure it is writable.")
            return
        errors.append(f"{description} directory '{parent}' does not exist. "
                      "Create it and ensure it is writable.")
        return
    if not os.access(parent, os.W_OK):
        errors.append(f"{description} directory '{parent}' is not writable. "
                      "Adjust permissions or choose a different path.")


def _validate_readable_file_path(path: Path, description: str, errors: list):
    if not path.exists():
        errors.append(f"{description} at '{path}' does not exist. "
                      "Create it and ensure it is readable.")
        return
    if not os.access(path, os.R_OK):
        errors.append(f"{description} at '{path}' is not readable. "
                      "Adjust permissions or choose a different path.")


def _validate_readonly_file_path(path: Path, description: str, errors: list,
                                 warnings: Optional[list] = None,
                                 require_root_owner: bool = False):
    if not path.exists():
        return
    mode = path.stat().st_mode
    if mode & 0o022:
        errors.append(f"{description} at '{path}' is writable by group or other. "
                      "Adjust permissions so it is read-only for the service.")
    if require_root_owner and path.stat().st_uid != 0:
        if warnings is not None:
            warnings.append(f"{description} at '{path}' is not owned by root. "
                            "Recommended owner is root for /etc configuration files.")


def _validate_writable_directory(path: Path, description: str, errors: list):
    if path.exists():
        if not path.is_dir():
            errors.append(f"{description} at '{path}' is not a directory. "
                          "Update the path or remove the conflicting file.")
            return
        if not os.access(path, os.W_OK):
            errors.append(f"{description} at '{path}' is not writable. "
                          "Adjust permissions or choose a different directory.")
        return

    parent = path.parent
    if not parent.exists():
        existing_parent = _find_existing_parent(parent)
        if existing_parent is None or not os.access(existing_parent, os.W_OK):
            errors.append(f"{description} parent directory '{parent}' does not exist "
                          "and cannot be created. Create it and ensure it is writable.")
        return
    if not os.access(parent, os.W_OK):
        errors.append(f"{description} parent directory '{parent}' is not writable. "
                      "Adjust permissions or choose a different directory.")


def _validate_credential_key_dirs(errors: list):
    state_dir = Path(get_data_dir())
    if state_dir.exists() and os.access(state_dir, os.W_OK):
        return
    errors.append(
        "No writable credential key location found. "
        "Ensure /var/lib/fossilsafe is present and writable."
    )


def _ensure_db_path(db_path: Optional[str], source: str) -> str:
    if not db_path:
        print(
            f"Startup validation failed: database path is missing ({source}).",
            file=sys.stderr
        )
        raise SystemExit(1)
    if not isinstance(db_path, (str, os.PathLike)):
        print(
            "Startup validation failed: resolved database path is invalid "
            f"({source}: {db_path!r}).",
            file=sys.stderr
        )
        raise SystemExit(1)
    return os.fspath(db_path)


def run_startup_validation(db_path: str):
    errors = []
    warnings = []

    db_path = _ensure_db_path(db_path, "startup validation")
    _validate_writable_file_path(Path(db_path), "Database file", errors)
    config_path = Path(get_config_path())
    _validate_readable_file_path(config_path, "Config file", errors)
    _validate_readonly_file_path(
        config_path,
        "Config file",
        errors,
        warnings,
        require_root_owner=config_path.as_posix().startswith('/etc/')
    )
    state_dir = Path(get_data_dir())
    _validate_writable_directory(state_dir, "State directory", errors)
    state_path = Path(get_state_path())
    _validate_writable_file_path(state_path, "State file", errors, allow_parent_create=True)
    _validate_writable_directory(Path(get_catalog_backup_dir()),
                                 "Catalog backup directory", errors)
    # Use config-aware staging dir instead of hardcoded path
    config = load_config()
    staging_dir = config.get('staging_dir') or '/var/lib/fossilsafe/staging'
    _validate_writable_directory(Path(staging_dir),
                                 "Streaming staging directory", errors)
    _validate_credential_key_dirs(errors)

    if errors:
        print("Startup validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        raise SystemExit(1)

    if warnings:
        print("Startup validation warnings:")
        for warning in warnings:
            print(f"  - {warning}")

    try:
        devices, _health = get_devices()
        changer = devices.get("changer_sg")
        drive_sg = devices.get("drive_sg")
        if changer and drive_sg and os.path.realpath(changer) == os.path.realpath(drive_sg):
            print(
                "Startup validation failed: changer device resolves to the same sg path as the tape drive "
                f"({changer} == {drive_sg}).",
                file=sys.stderr,
            )
            raise SystemExit(1)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Startup validation warning: unable to validate tape devices ({exc})")

    try:
        ensure_state_file()
    except OSError as exc:
        print(
            "Startup validation failed: unable to create state file at "
            f"{get_state_path()}: {exc}",
            file=sys.stderr
        )
        raise SystemExit(1)


def _normalize_device_setting(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _get_tape_config(file_config: dict) -> dict:
    tape_config = file_config.get('tape') if isinstance(file_config, dict) else {}
    if not isinstance(tape_config, dict):
        tape_config = {}
    drive_devices = tape_config.get('drive_devices')
    if isinstance(drive_devices, list):
        drive_devices = [str(path).strip() for path in drive_devices if str(path).strip()]
    else:
        drive_devices = None
    return {
        'changer_device': _normalize_device_setting(
            tape_config.get('changer_device') or tape_config.get('changer')
        ),
        'drive_device': _normalize_device_setting(
            tape_config.get('drive_device') or tape_config.get('drive')
        ),
        'drive_devices': drive_devices,
    }


def _format_detected_devices(drives, changers) -> str:
    drive_paths = [drive.get('path') for drive in drives if drive.get('path')]
    changer_paths = [changer.get('path') for changer in changers if changer.get('path')]
    return f"Detected drives: {drive_paths or 'none'}; changers: {changer_paths or 'none'}"

_permission_warning_cache: Dict[str, float] = {}
_permission_warning_ttl_seconds = 3600


def _log_permission_warning(message: str, key: Optional[str] = None) -> None:
    cache_key = key or message
    now = time.time()
    last = _permission_warning_cache.get(cache_key)
    if last and now - last < _permission_warning_ttl_seconds:
        return
    _permission_warning_cache[cache_key] = now
    log_warning(message, 'tape')


def _log_changer_permission_diagnostics(changer_path: Optional[str]) -> None:
    """Warn if the changer device is not accessible to the current service user."""
    path = None
    if FOSSILSAFE_CHANGER_SYMLINK and os.path.exists(FOSSILSAFE_CHANGER_SYMLINK):
        path = FOSSILSAFE_CHANGER_SYMLINK
    elif changer_path and os.path.exists(changer_path):
        path = changer_path
    if not path:
        return
    try:
        stat_info = os.stat(path)
        mode = stat.S_IMODE(stat_info.st_mode)
        owner = pwd.getpwuid(stat_info.st_uid).pw_name
        group = grp.getgrgid(stat_info.st_gid).gr_name
        can_read = os.access(path, os.R_OK)
        can_write = os.access(path, os.W_OK)
        if not (can_read and can_write):
            _log_permission_warning(
                f"Changer device {path} is not readable/writable by the service user. "
                f"owner={owner} group={group} mode={oct(mode)}",
                key=f"device_access:{path}:{mode}:{owner}:{group}",
            )
        try:
            target_group = grp.getgrnam("fossilsafe-tape")
            gids = set(os.getgroups() + [os.getgid()])
            if target_group.gr_gid not in gids:
                _log_permission_warning(
                    "Service user is not in the fossilsafe-tape group; "
                    "device permissions may block changer access.",
                    key="missing_fossilsafe_tape_group",
                )
        except KeyError:
            _log_permission_warning(
                "Group fossilsafe-tape not found; ensure the service user has changer device access.",
                key="group_not_found_fossilsafe_tape",
            )
    except Exception as exc:
        _log_permission_warning(
            f"Unable to read changer permissions for {path}: {exc}",
            key=f"stat_error:{path}",
        )


def _autodetect_tape_devices():
    devices, _health = get_devices()
    drives, changers = discover_devices()
    detected = {
        'drives': [drive.__dict__ for drive in drives],
        'changers': [changer.__dict__ for changer in changers],
    }
    return devices.get("drive_nst"), devices.get("changer_sg"), detected


def _resolve_db_path(cli_db_path: Optional[str]) -> str:
    if cli_db_path:
        return _ensure_db_path(cli_db_path, "CLI --db-path")

    env_db_path = os.environ.get('FOSSILSAFE_DB_PATH')
    if env_db_path:
        return _ensure_db_path(env_db_path, "FOSSILSAFE_DB_PATH")

    state = load_state()
    state_db_path = state.get('DB_PATH')
    if state_db_path:
        return _ensure_db_path(state_db_path, f"state file {get_state_path()}")

    config_path = get_config_path()
    config = load_config(config_path)
    config_db_path = config.get('db_path') or config.get('DB_PATH')
    if config_db_path:
        return _ensure_db_path(config_db_path, f"config file {config_path}")

    return _ensure_db_path(get_default_db_path(), "default")

# =============================================================================
# Source Encryption System
# =============================================================================

class SourceManager:
    """
    Secure source storage with encryption for secrets.
    Uses Fernet symmetric encryption with a machine-specific key.
    Falls back to a local key file if /var/lib is not writable.
    """
    
    def __init__(self, db):
        self.db = db
        self._key: Optional[bytes] = None
        self._fernet = None
        self._initialize_key()
    
    def _initialize_key(self):
        """Initialize or load encryption key"""
        key_path = Path(get_credential_key_path())
        if key_path.exists():
            try:
                self._key = key_path.read_bytes()
                self._fernet = Fernet(self._key)
                return
            except Exception as e:
                raise RuntimeError(f"Could not read key from {key_path}: {e}")

        try:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            self._key = Fernet.generate_key()
            key_path.write_bytes(self._key)
            try:
                os.chmod(key_path, 0o600)  # Restrict permissions
            except Exception:
                pass
            service_user = os.environ.get("FOSSILSAFE_SERVICE_USER", "fossilsafe")
            if os.geteuid() == 0:
                try:
                    uid = pwd.getpwnam(service_user).pw_uid
                    gid = grp.getgrnam(service_user).gr_gid
                    os.chown(key_path, uid, gid)
                except Exception:
                    pass
            self._fernet = Fernet(self._key)
            print(f"Created new encryption key at {key_path}")
            return
        except Exception as e:
            raise RuntimeError(f"Could not persist encryption key at {key_path}: {e}")
    
    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string and return base64-encoded ciphertext"""
        if not plaintext:
            return ''
        if not self._fernet:
            return ''
        try:
            encrypted = self._fernet.encrypt(plaintext.encode())
            return base64.b64encode(encrypted).decode()
        except Exception as e:
            print(f"Encryption error: {e}")
            return ''
    
    def decrypt(self, ciphertext: str) -> str:
        """Decrypt base64-encoded ciphertext and return plaintext"""
        if not ciphertext:
            return ''
        if not self._fernet:
            return ''
        try:
            encrypted = base64.b64decode(ciphertext.encode())
            return self._fernet.decrypt(encrypted).decode()
        except Exception as e:
            print(f"Decryption error (key may have changed): {e}")
            return ''
    
    def store_source(self, source: Dict[str, object]):
        """Store encrypted source data."""
        password = source.get('password') or ''
        encrypted_password = source.get('password_encrypted') or self.encrypt(str(password))
        payload = dict(source)
        payload['password_encrypted'] = encrypted_password
        payload.pop('password', None)
        self.db.store_source(payload)

    def get_source(self, source_id: str, include_password: bool = True) -> Optional[Dict[str, Any]]:
        """Retrieve and decrypt source secrets."""
        source = self.db.get_source(source_id)
        if not source:
            return None
        encrypted_password = source.get('password_encrypted', '')
        if include_password:
            source['password'] = self.decrypt(encrypted_password)
        source['has_password'] = bool(encrypted_password)
        if not include_password:
            source.pop('password_encrypted', None)
        return source

    def list_sources(self) -> list:
        """List all stored sources (without secrets)."""
        sources = self.db.list_sources()
        for source in sources:
            source['has_password'] = bool(source.get('password_encrypted'))
            source.pop('password_encrypted', None)
        return sources

    def delete_source(self, source_id: str):
        """Delete stored source."""
        self.db.delete_source(source_id)

# Initialize credential manager
# Credential manager will be initialized by create_app()


# =============================================================================
# Job Checkpoint System
# =============================================================================

class CheckpointManager:
    """
    Manages job checkpoints for resume capability.
    Saves progress periodically so jobs can resume after restart.
    """
    
    def __init__(self, db):
        self.db = db
        self.checkpoint_interval = 100  # Save every N files
    
    def save_checkpoint(self, job_id: int, checkpoint_data: dict):
        """Save a job checkpoint"""
        checkpoint = {
            'job_id': job_id,
            'timestamp': now_utc_iso(),
            'last_file_index': checkpoint_data.get('last_file_index', 0),
            'last_file_path': checkpoint_data.get('last_file_path', ''),
            'files_completed': checkpoint_data.get('files_completed', 0),
            'bytes_written': checkpoint_data.get('bytes_written', 0),
            'current_tape': checkpoint_data.get('current_tape', ''),
            'tape_position': checkpoint_data.get('tape_position', 0),
            'state': json.dumps(checkpoint_data.get('state', {}))
        }
        self.db.save_job_checkpoint(job_id, checkpoint)
    
    def get_checkpoint(self, job_id: int) -> dict:
        """Get the latest checkpoint for a job"""
        checkpoint = self.db.get_job_checkpoint(job_id)
        if checkpoint and 'state' in checkpoint:
            try:
                checkpoint['state'] = json.loads(checkpoint['state'])
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                log_warning(f"Failed to parse checkpoint state: {e}", "checkpoint")
                checkpoint['state'] = {}
        return checkpoint
    
    def clear_checkpoint(self, job_id: int):
        """Clear checkpoint after job completes successfully"""
        self.db.clear_job_checkpoint(job_id)
    
    def get_interrupted_jobs(self) -> list:
        """Get jobs that were interrupted and have checkpoints"""
        return self.db.get_interrupted_jobs()
    
    def can_resume(self, job_id: int) -> tuple:
        """Check if a job can be resumed, return (can_resume, reason)"""
        checkpoint = self.get_checkpoint(job_id)
        if not checkpoint:
            return False, "No checkpoint found"
        
        job = self.db.get_job(job_id)
        if not job:
            return False, "Job not found"
        
        if job['status'] not in ('interrupted', 'paused', 'error'):
            return False, f"Job status is {job['status']}, not resumable"
        
        # Check if tape is still available
        tape_barcode = checkpoint.get('current_tape')
        if tape_barcode:
            tape = self.db.get_tape(tape_barcode)
            if not tape:
                return False, f"Tape {tape_barcode} not found"
            if tape.get('status') == 'in_use':
                return False, f"Tape {tape_barcode} is in use by another job"
        
        return True, "Job can be resumed"

# Initialize checkpoint manager
# Checkpoint manager will be initialized by create_app()

# =============================================================================
# Logging System
# =============================================================================

# Logging System
# =============================================================================

# LogManager imported from backend.log_manager

def _setup_request_tracing(app):
    @app.before_request
    def before_request():
        g.request_id = request.headers.get('X-Request-ID') or str(uuid.uuid4())
        g.start_time = time.time()
        
    @app.after_request
    def after_request(response):
        if hasattr(g, 'request_id'):
            response.headers['X-Request-ID'] = g.request_id
        
        # Record metrics
        if hasattr(g, 'start_time') and metrics_service:
            duration = time.time() - g.start_time
            metrics_service.record_api_timing(duration)
            
        return response

    

# LogManager already imported

def log_info(message, category='system', details=None):
    if not log_manager:
        return None
    return log_manager.add('info', message, category, details)

def log_debug(message, category='system', details=None):
    if not log_manager:
        return None
    return log_manager.add('debug', message, category, details)

def log_success(message, category='system', details=None):
    if not log_manager:
        return None
    return log_manager.add('success', message, category, details)

def log_warning(message, category='system', details=None):
    if not log_manager:
        return None
    return log_manager.add('warning', message, category, details)

def log_error(message, category='system', details=None):
    if not log_manager:
        return None
    return log_manager.add('error', message, category, details)


def _refresh_tape_ltfs_metadata(barcode: str, drive: int = 0) -> Optional[Dict[str, object]]:
    try:
        metadata = tape_controller.verify_ltfs(barcode, drive=drive)
        if not metadata.get("ok"):
            raise RuntimeError(metadata.get("error") or "LTFS verification failed")
        db.update_tape_ltfs_info(
            barcode,
            formatted=bool(metadata.get("ltfs_formatted")),
            ltfs_present=True,
            initialized=bool(metadata.get("ltfs_formatted")),
            capacity_bytes=metadata.get("capacity_bytes"),
            used_bytes=metadata.get("used_bytes"),
            volume_name=metadata.get("volume_name"),
            verified_at=now_utc_iso(),
        )
        return metadata
    except Exception as exc:
        log_warning(f"LTFS metadata check failed for {barcode}: {exc}", "tape")
        try:
            db.update_tape_ltfs_info(
                barcode,
                formatted=False,
                ltfs_present=False,
                initialized=False,
                verified_at=now_utc_iso(),
            )
        except Exception:
            pass
        return None


def _log_tape_command(entry: Dict[str, Any]) -> None:
    if not log_manager:
        return
    command = entry.get("command") or []
    if not command:
        return
    binary = os.path.basename(str(command[0]))
    if binary not in ("mtx", "mt", "ltfs", "mkltfs", "ltfsck"):
        return
    if entry.get("returncode") not in (None, 0) or entry.get("timed_out"):
        error_type = entry.get("error_type")
        suggested_fix = None
        if error_type == "permission_denied":
            suggested_fix = "Check /dev/sg permissions and ensure fossilsafe user is in fossilsafe-tape group."
        elif error_type == "illegal_request":
            suggested_fix = "Verify the changer device path (use the medium changer sg device, not the drive sg)."
        elif error_type == "device_busy":
            suggested_fix = "Ensure no other tape jobs are running and the drive is idle before retrying."
        elif error_type == "timeout":
            suggested_fix = "Ensure the library is responsive and not stuck; retry after verifying hardware."
        details = dict(entry)
        if suggested_fix:
            details["suggested_fix"] = suggested_fix
        log_error("Tape command failed", "tape_cmd", json.dumps(details))
    else:
        log_info("Tape command executed", "tape_cmd", json.dumps(entry))


def _log_tape_event(entry: Dict[str, object]) -> None:
    if not log_manager:
        return
    level = str(entry.get("level") or "info").lower()
    message = entry.get("message") or "Tape event"
    details = entry.get("details")
    payload = json.dumps(details) if details else None
    if level == "warning":
        log_warning(message, "tape", payload)
    elif level == "error":
        log_error(message, "tape", payload)
    else:
        log_info(message, "tape", payload)


def _get_request_context() -> Dict[str, object]:
    return {
        "request_id": getattr(g, "request_id", None),
        "path": request.path if request else None,
        "remote_addr": request.remote_addr if request else None,
    }


def _log_request_event(message: str, category: str, details: Optional[Dict[str, object]] = None) -> None:
    payload = _get_request_context()
    if details:
        payload.update(details)
    log_info(message, category, json.dumps(payload))




        
def _make_error_response(code: str, message: str, status_code: int, detail: Optional[str] = None):
    return jsonify({
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "detail": detail,
        },
        "request_id": getattr(g, "request_id", None),
    }), status_code


def _make_unavailable_response(code: str, message: str, detail: Optional[str], status_code: int):
    return _make_error_response(code, message, status_code, detail)


def _smb_test_response(result: Dict[str, object]):
    if result.get("ok"):
        payload = {
            "connected": True,
            "detail": result.get("detail"),
        }
        return jsonify({"success": True, "result": payload, "details": payload, "connected": True})
    error_code = str(result.get("error_code") or "connection_failed")
    message = str(result.get("message") or "SMB connection failed")
    detail = result.get("detail")
    status_map = {
        "invalid_request": 400,
        "auth_failed": 401,
        "timeout": 504,
        "smb_unavailable": 503,
        "smb_tool_missing": 503,
        "share_not_found": 404,
        "host_unreachable": 502,
        "connection_failed": 502,
    }
    status = status_map.get(error_code, 502)
    return _make_error_response(error_code, message, status, str(detail) if detail else None)


def _db_available() -> bool:
    return db is not None and db_unavailable_reason is None


def _source_manager_available() -> bool:
    return source_manager is not None and source_manager_unavailable_reason is None


def _hardware_available() -> Tuple[bool, Optional[str]]:
    if not hardware_availability["hardware_available"]:
        reason = hardware_availability["hardware_reason"]
        if not reason:
            if tape_controller is None:
                reason = "Hardware controllers not initialized"
            elif not hardware_init_status["ready"]:
                reason = hardware_init_status["error"] or "Hardware initialization in progress"
        return False, reason
    if tape_controller is None:
        return False, hardware_availability["hardware_reason"] or hardware_init_status["error"] or "Hardware controllers not initialized"
    if not hardware_init_status["ready"]:
        return False, hardware_init_status["error"] or hardware_availability["hardware_reason"] or "Hardware initialization in progress"
    return True, None


def initialize_smb_client() -> Dict[str, Any]:
    global smb_client, smb_unavailable_reason

    if smb_client is not None:
        smb_unavailable_reason = None
        app.smb_client = smb_client
        return {"smb_available": True, "smb_reason": None}

    try:
        smb_client = SMBClient()
        smb_unavailable_reason = None
        app.smb_client = smb_client
        return {"smb_available": True, "smb_reason": None}
    except Exception as exc:
        smb_client = None
        smb_unavailable_reason = f"{exc}"
        log_warning(f"SMB client initialization failed: {exc}", "smb")
        return {"smb_available": False, "smb_reason": smb_unavailable_reason}


def _clear_tape_controllers() -> None:
    global tape_controller, backup_engine, advanced_restore_engine, duplication_engine, streaming_pipeline, scheduler, autopilot, preflight_checker, library_manager

    tape_controller = None
    backup_engine = None
    advanced_restore_engine = None
    duplication_engine = None
    streaming_pipeline = None
    scheduler = None
    autopilot = None
    duplication_engine = None
    streaming_pipeline = None
    scheduler = None
    autopilot = None
    preflight_checker = None
    library_manager = None


def _get_open_fd_holders(paths: List[str]) -> Dict[str, object]:
    holders: List[Dict[str, object]] = []
    errors: List[str] = []
    lsof_path = shutil.which("lsof")
    if not lsof_path:
        return {"available": False, "holders": [], "errors": ["lsof not installed"]}
    for path in paths:
        if not path:
            continue
        try:
            result = subprocess.run(
                [lsof_path, "-n", "-F", "pcu", "--", path],
                capture_output=True,
                text=True,
            )
        except Exception as exc:
            errors.append(f"{path}: {exc}")
            continue
        if result.returncode not in (0, 1):
            errors.append(f"{path}: {result.stderr.strip() or 'lsof error'}")
            continue
        current: Dict[str, Any] = {}
        for line in (result.stdout or "").splitlines():
            if not line:
                continue
            prefix = line[0]
            value = line[1:]
            if prefix == "p":
                if current:
                    holders.append({**current, "path": path})
                current = {"pid": int(value)} if value.isdigit() else {"pid": value}
            elif prefix == "c":
                current["command"] = value
            elif prefix == "u":
                current["user"] = value
        if current:
            holders.append({**current, "path": path})
    return {"available": True, "holders": holders, "errors": errors}

# =============================================================================
# Job Logging Helpers
# =============================================================================


# =============================================================================
# Input Validation
# =============================================================================

def validate_smb_path(path: str) -> tuple:
    """Validate SMB path format"""
    if not path:
        return False, "SMB path is required"
    if not path.startswith('//'):
        return False, "SMB path must start with //"
    if len(path) < 4:
        return False, "SMB path is too short"
    parts = [segment for segment in path[2:].split('/') if segment]
    if len(parts) < 2:
        return False, "SMB path must include host and share"
    if re.search(r'[;&|`$]', path):
        return False, "SMB path contains invalid characters"
    return True, None

from backend.utils.validation import validate_job_name, validate_barcode, validate_tape_identifier

def is_verification_enabled() -> bool:
    """Global verify-after-write toggle (default enabled)."""
    return db.get_bool_setting('verification_enabled', True)

# =============================================================================
# Preflight Checks
# =============================================================================

class PreflightChecker:
    """Run preflight checks before job execution"""
    
    def __init__(self, tape_controller, smb_client, db, source_manager=None, library_manager=None):
        self.tape = tape_controller
        self.smb = smb_client
        self.db = db
        self.source_manager = source_manager
        self.library_manager = library_manager
    
    def run_all(self, job_config: dict) -> dict:
        """Run all preflight checks"""
        results: Dict[str, Any] = {
            'passed': True,
            'valid': True,
            'checks': [],
            'warnings': [],
            'errors': []
        }
        
        checks = []
        checks.extend(self._check_library_online())
        checks.extend(self._check_drive_available())
        checks.append(self._check_tapes_available(job_config.get('estimated_size', 0)))
        checks.append(self._check_disk_space())
        
        source_path = job_config.get('source_path') or ''
        if not source_path and job_config.get('source_id') and self.source_manager:
            source = self.source_manager.get_source(job_config.get('source_id'), include_password=True)
            if source:
                source_path = source.get('source_path') or ''
        if source_path and source_path.startswith('//') and not source_path.startswith('//browser/'):
            credentials = self._resolve_credentials(job_config)
            checks.append(self._check_smb_connectivity(
                source_path,
                credentials.get('username', ''),
                credentials.get('password', '')
            ))
        
        for check in checks:
            results['checks'].append(check)
            if check['status'] == 'error':
                results['errors'].append(check['message'])
                results['passed'] = False
                results['valid'] = False
            elif check['status'] == 'warning':
                results['warnings'].append(check['message'])
        
        return results
    
    def _check_library_online(self) -> list:
        results = []
        if self.library_manager:
            for lib_id, controller in self.library_manager.controllers.items():
                try:
                    online = controller.is_online()
                    results.append({
                        'name': f'Library Status ({lib_id})',
                        'status': 'pass' if online else 'error',
                        'message': 'Tape library is online' if online else 'Tape library is offline'
                    })
                except Exception as e:
                    results.append({'name': f'Library Status ({lib_id})', 'status': 'error', 'message': str(e)})
        else:
            try:
                online = self.tape.is_online()
                results.append({
                    'name': 'Library Status',
                    'status': 'pass' if online else 'error',
                    'message': 'Tape library is online' if online else 'Tape library is offline'
                })
            except Exception as e:
                results.append({'name': 'Library Status', 'status': 'error', 'message': str(e)})
        return results
    
    def _check_drive_available(self) -> list:
        results = []
        if self.library_manager:
            for lib_id, controller in self.library_manager.controllers.items():
                try:
                    status = controller.get_drive_status()
                    if status.get('available', False):
                        results.append({'name': f'Drive Status ({lib_id})', 'status': 'pass', 'message': 'Drive available'})
                    else:
                        results.append({'name': f'Drive Status ({lib_id})', 'status': 'warning', 'message': 'Drive status unknown'})
                except Exception as e:
                    results.append({'name': f'Drive Status ({lib_id})', 'status': 'warning', 'message': str(e)})
        else:
            try:
                status = self.tape.get_drive_status()
                if status.get('available', False):
                    results.append({'name': 'Drive Status', 'status': 'pass', 'message': 'Drive available'})
                else:
                    results.append({'name': 'Drive Status', 'status': 'warning', 'message': 'Drive status unknown'})
            except Exception as e:
                results.append({'name': 'Drive Status', 'status': 'warning', 'message': str(e)})
        return results

    def _resolve_credentials(self, job_config: dict) -> dict:
        """Resolve source_id to usable SMB credentials."""
        source_id = job_config.get('source_id')
        if source_id and self.source_manager:
            source = self.source_manager.get_source(source_id, include_password=True)
            if source:
                return {
                    'username': source.get('username', ''),
                    'password': source.get('password', '')
                }
        return {'username': '', 'password': ''}
    
    def _check_tapes_available(self, estimated_size: int) -> dict:
        try:
            tapes = self.db.get_tape_inventory()
            available = [t for t in tapes if t['status'] == 'available' and not t['barcode'].startswith('CLN')]
            tape_capacity = 2.5 * 1024 * 1024 * 1024 * 1024
            tapes_needed = max(1, int(estimated_size / (tape_capacity * 0.85)) + 1) if estimated_size > 0 else 1
            
            if len(available) >= tapes_needed:
                return {'name': 'Tape Availability', 'status': 'pass', 'message': f'{len(available)} tapes available'}
            elif len(available) > 0:
                return {'name': 'Tape Availability', 'status': 'warning', 'message': f'Only {len(available)} tapes available'}
            return {'name': 'Tape Availability', 'status': 'error', 'message': 'No tapes available'}
        except Exception as e:
            return {'name': 'Tape Availability', 'status': 'warning', 'message': str(e)}
    
    def _check_smb_connectivity(self, path: str, username: str, password: str) -> dict:
        try:
            result = self.smb.test_connection(path, username, password)
            return {
                'name': 'SMB Connectivity',
                'status': 'pass' if result else 'error',
                'message': f'Connected to {path}' if result else f'Cannot connect to {path}'
            }
        except Exception as e:
            return {'name': 'SMB Connectivity', 'status': 'error', 'message': str(e)}
    
    def _check_disk_space(self) -> dict:
        try:
            stat = os.statvfs(tempfile.gettempdir())
            free_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
            if free_gb > 10:
                return {'name': 'Disk Space', 'status': 'pass', 'message': f'{free_gb:.1f} GB free'}
            elif free_gb > 1:
                return {'name': 'Disk Space', 'status': 'warning', 'message': f'Low space: {free_gb:.1f} GB'}
            return {'name': 'Disk Space', 'status': 'error', 'message': f'Critical: {free_gb:.1f} GB'}
        except Exception as e:
            return {'name': 'Disk Space', 'status': 'warning', 'message': str(e)}

def _check_hardware_cooldown():
    """Prevent rapid-fire hardware operations."""
    global _last_hardware_op_at
    now = time.time()
    elapsed = now - _last_hardware_op_at
    if elapsed < HARDWARE_OP_COOLDOWN:
        return False, f"Hardware is cooling down, please wait {HARDWARE_OP_COOLDOWN - elapsed:.2f}s"
    _last_hardware_op_at = now
    return True, None

preflight_checker = None

# =============================================================================
# Support Bundle Generator
# =============================================================================


# =============================================================================
# Global State
# =============================================================================

hardware_init_status = {
    "ready": False,
    "error": None,
    "last_attempt": None,
}

autodetect_no_hardware_logged = False

hardware_availability = {
    "hardware_available": False,
    "hardware_reason": "Hardware controllers not initialized",
}

app.hardware_available = hardware_availability["hardware_available"]
app.hardware_reason = hardware_availability["hardware_reason"]


def _hardware_status_payload() -> Dict[str, object]:
    return {
        "ready": hardware_init_status["ready"],
        "error": hardware_init_status["error"],
        "last_attempt": hardware_init_status["last_attempt"],
    }


def _set_hardware_availability(available: bool, reason: Optional[str] = None) -> None:
    if available:
        reason = None
    elif not reason:
        reason = "Hardware controllers not initialized"
    hardware_availability["hardware_available"] = available
    hardware_availability["hardware_reason"] = reason
    system_state["hardware_available"] = available
    system_state["hardware_reason"] = reason
    app.hardware_available = available
    app.hardware_reason = reason


def _set_hardware_init_status(ready: bool, error: Optional[str] = None) -> None:
    hardware_init_status["ready"] = ready
    hardware_init_status["error"] = error
    hardware_init_status["last_attempt"] = now_utc_iso()
    system_state["hardware_status"] = _hardware_status_payload()
    _set_hardware_availability(ready, error)


system_state = {
    'library_online': False,
    'library_state': 'OFFLINE',
    'library_error': None,
    'current_jobs': [],
    'tape_inventory': [],
    'recovery_state': None,
    'tape_features': {
        'quick_erase_supported': True,
        'quick_erase_reason': None,
    },
    'library_info': {
        'name': 'Tape Library',
        'model': 'Unknown',
        'type': 'LTO',
        'drives': 0,
        'slots': 0
    },
    'stats': {
        'active_jobs': 0,
        'total_archived': 0,
        'available_tapes': 0,
        'total_slots': 0
    },
    'hardware_available': hardware_availability["hardware_available"],
    'hardware_reason': hardware_availability["hardware_reason"],
    'hardware_status': _hardware_status_payload(),
}

_RECOVERY_STATE_KEY = "tape_recovery"
_recovery_state_cache = None
_DESTRUCTIVE_JOB_TYPES = {"tape_wipe", "tape_initialize"}


def _get_recovery_state() -> Optional[Dict[str, object]]:
    global _recovery_state_cache
    if _recovery_state_cache is None:
        state = load_state()
        recovery = state.get(_RECOVERY_STATE_KEY)
        if isinstance(recovery, dict):
            _recovery_state_cache = recovery
        else:
            _recovery_state_cache = None
    return _recovery_state_cache


def _set_recovery_state(payload: Optional[Dict[str, object]]) -> None:
    global _recovery_state_cache
    _recovery_state_cache = payload
    update_state({_RECOVERY_STATE_KEY: payload})


def _clear_recovery_state(drive: Optional[int] = None) -> None:
    state = _get_recovery_state()
    if not state:
        return
    if drive is not None and state.get("drive") != drive:
        return
    barcode = state.get("barcode")
    if barcode:
        try:
            db.update_tape_status(barcode, "available")
        except Exception:
            pass
    _set_recovery_state(None)


def _apply_startup_recovery() -> None:
    if not tape_controller or not db:
        return
    try:
        last_job = db.get_last_job_by_types(sorted(_DESTRUCTIVE_JOB_TYPES))
    except Exception:
        last_job = None
    if not last_job:
        return
    job_status = (last_job.get("status") or "").lower()
    if job_status in ("completed", "cancelled"):
        return

    for drive in sorted(getattr(tape_controller, "drive_devices", {0: None}).keys()):
        current = tape_controller.get_current_tape(drive=drive)
        if not current:
            continue
        job_drive = last_job.get("drive")
        if job_drive is not None and int(job_drive) != int(drive):
            continue
        barcode = current.get("barcode")
        payload = {
            "drive": drive,
            "barcode": barcode,
            "job_id": last_job.get("id"),
            "job_type": last_job.get("job_type"),
            "job_status": last_job.get("status"),
            "detected_at": now_utc_iso(),
        }
        _set_recovery_state(payload)
        if barcode:
            try:
                db.update_tape_status(barcode, "recovery_needed")
            except Exception:
                pass
        log_warning(
            "Recovery required: destructive job ended unexpectedly with tape still in drive.",
            "tape",
        )
        break



def update_system_state():
    """Update system state from database and hardware"""
    try:
        if not db:
            system_state["hardware_status"] = _hardware_status_payload()
            return
        tape_features = _get_tape_feature_flags()
        system_state['current_jobs'] = db.get_active_jobs()
        system_state['recovery_state'] = _get_recovery_state()
        
        # Use tape_service to overlay active jobs and synthesize location fields
        tape_service = getattr(app, 'tape_service', None)
        if tape_service:
            system_state['tape_inventory'] = tape_service.apply_active_tape_job_state(
                db.get_tape_inventory(),
                system_state['current_jobs'],
            )
        else:
            system_state['tape_inventory'] = db.get_tape_inventory()
        system_state['tape_features'] = {
            "quick_erase_supported": tape_features["quick_erase_supported"],
            "quick_erase_reason": tape_features["quick_erase_reason"],
        }
        system_state["hardware_status"] = _hardware_status_payload()

        stats = cast(Dict[str, Any], system_state['stats'])
        if tape_controller:
            system_state['library_online'] = tape_controller.is_online()
            system_state['library_state'] = tape_controller.get_library_state()
            system_state['library_error'] = tape_controller.get_library_error()

            # Get library info from tape controller
            try:
                lib_info = tape_controller.get_library_info()
                system_state['library_info'] = lib_info
                stats['total_slots'] = lib_info.get('slots', len(cast(list, system_state['tape_inventory'])))
                if not system_state['library_error']:
                    system_state['library_error'] = lib_info.get('error')
            except Exception:
                stats['total_slots'] = len(cast(list, system_state['tape_inventory']))
        else:
            system_state['library_online'] = False
            system_state['library_state'] = (
                "UNAVAILABLE" if hardware_init_status["error"] else "INITIALIZING"
            )
            system_state['library_error'] = hardware_init_status["error"]
            stats['total_slots'] = len(cast(list, system_state['tape_inventory']))

        stats['active_jobs'] = len([j for j in cast(list, system_state['current_jobs']) if j.get('status') == 'running'])
        stats['available_tapes'] = len([t for t in cast(list, system_state['tape_inventory']) 
                                        if t.get('status') == 'available' and not t.get('barcode', '').startswith('CLN')])
        stats['total_archived'] = db.get_total_archived_size()
        
        stats['total_archived'] = db.get_total_archived_size()
    except Exception as e:
        log_error(f"Error updating system state: {e}", 'system')

def state_update_thread():
    while True:
        update_system_state()
        _emit_socketio_event('state_update', system_state)
        time.sleep(5)

def start_state_update_thread():
    global _state_update_thread_started
    if _state_update_thread_started:
        return
    _state_update_thread_started = True
    threading.Thread(target=state_update_thread, daemon=True).start()

# =============================================================================
# API Routes
# =============================================================================

def _get_api_key() -> Optional[str]:
    config = load_config()
    config_key = config.get('api_key') or config.get('API_KEY')
    if isinstance(config_key, str) and config_key.strip():
        return config_key.strip()
    return None


def _extract_api_key_from_request(req) -> Optional[str]:
    """Accept API key via Authorization: Bearer <key> or X-API-Key."""
    auth_header = req.headers.get('Authorization', '')
    if auth_header.lower().startswith('bearer '):
        return auth_header.split(' ', 1)[1].strip()
    header_key = req.headers.get('X-API-Key')
    if header_key:
        return header_key.strip()
    query_key = req.args.get('api_key') or req.args.get('token')
    if query_key:
        return str(query_key).strip()
    return None


def _extract_api_key_from_auth(auth_payload) -> Optional[str]:
    if not auth_payload or not isinstance(auth_payload, dict):
        return None
    auth_key = auth_payload.get('api_key')
    if auth_key:
        return str(auth_key).strip()
    token_key = auth_payload.get('token')
    if token_key:
        return str(token_key).strip()
    auth_header = auth_payload.get('authorization') or auth_payload.get('Authorization')
    if isinstance(auth_header, str) and auth_header.lower().startswith('bearer '):
        return auth_header.split(' ', 1)[1].strip()
    return None


def _is_authorized_request(req, auth_payload=None) -> bool:
    api_key = _get_api_key()
    if not api_key:
        return False
    provided = _extract_api_key_from_request(req)
    if not provided and auth_payload is not None:
        provided = _extract_api_key_from_auth(auth_payload)
    return bool(provided) and provided == api_key


def _socketio_auth_required() -> bool:
    return os.environ.get('FOSSILSAFE_REQUIRE_API_KEY', 'true').lower() not in ('0', 'false', 'no')


def _is_socketio_authorized(auth_payload=None) -> bool:
    if not _socketio_auth_required():
        return True
    
    # Check for Global API Key first
    if _is_authorized_request(request, auth_payload):
        return True
        
    # Check for User Session
    token = _extract_api_key_from_auth(auth_payload)
    if not token:
        # Check request headers/cookies too (Socket.IO handshake)
        token = request.headers.get('Authorization', '').replace('Bearer ', '') or request.cookies.get('session_token')
        
    if token:
        from backend.auth import get_auth_manager
        auth_manager = get_auth_manager()
        if auth_manager and auth_manager.validate_session(token):
            return True
            
    return False


def _register_socketio_authorized_sid() -> None:
    if request.sid:
        _authorized_socketio_sids.add(request.sid)


def _is_authorized_socketio_sid() -> bool:
    if not _socketio_auth_required():
        return True
    return request.sid in _authorized_socketio_sids


def _emit_socketio_event(event: str, payload) -> None:
    """
    Safely emit Socket.IO events.
    If auth is required, ONLY emit to authorized SIDs individually.
    """
    if not socketio:
        return
        
    if _socketio_auth_required():
        # NEVER call global emit if auth is required
        # Iterate over authorized SIDs and emit to them individually
        with _auth_lock: # Assuming there might be a lock for this set, but let's check
            authorized_sids = list(_authorized_socketio_sids)
            
        for sid in authorized_sids:
            try:
                socketio.emit(event, payload, to=sid)
            except Exception as e:
                # If a SID is stale or disconnected, it might fail; we can remove it
                # but let's be safe and just log it minimally or ignore
                pass
    else:
        # Public broadcast allowed only if auth disabled (e.g. dev mode)
        socketio.emit(event, payload)


@app.before_request
def _attach_request_id():
    from backend.auth import get_auth_manager
    g.auth_manager = get_auth_manager()
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    g.request_id = request_id
    return None


@app.before_request
def _require_api_key():
    if request.path in ('/api/healthz', '/healthz', '/api/csrf-token', '/api/auth/setup-status', '/api/auth/setup', '/api/auth/login', '/api/auth/logout', '/api/auth/sso/config'):
        return None
        
    # Check for session token (cookie or header)
    # This allows browser/authenticated users to work even if GLOBAL API KEY is required
    from backend.auth import get_auth_manager
    auth_manager = get_auth_manager()
    
    token = None
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header[7:]
    else:
        token = request.cookies.get('session_token') or request.args.get('token') or request.args.get('session_token')

    current_session = None
    if token and auth_manager:
        current_session = auth_manager.validate_session(token)
        if current_session:
            # Valid session found - allow request
            g.session = current_session
            return None


    if not request.path.startswith('/api/'):
        return None
        
    configured_key = _get_api_key()
    if not configured_key:
        # If no global API key is configured, allow the request to proceed to blueprints
        # which have their own session-based or role-based authentication.
        return None

    # If we are here, we need either a Global API Key OR a valid User Session (handled above)
    provided_key = _extract_api_key_from_request(request)
    identifier = request.remote_addr or "unknown"
    
    if api_key_rate_limiter:
        allowed, message = api_key_rate_limiter.check_rate_limit(identifier)
        if not allowed:
            # ... rate limit logic ...
            log_warning(
                "Auth rejected: rate limit exceeded",
                "auth",
                json.dumps({"path": request.path, "identifier": identifier, "request_id": g.request_id}),
            )
            return _make_error_response(
                "rate_limit_exceeded",
                message,
                429,
            )

    if not provided_key:
        # No key and no valid session (checked above)
        log_info(
            "Auth rejected: API key missing",
            "auth",
            json.dumps({"path": request.path, "reason": "missing_api_key"}),
        )
        return _make_error_response(
            "auth_missing_or_invalid",
            "API key required",
            401,
        )
        
    if provided_key != configured_key:
        if api_key_rate_limiter:
            api_key_rate_limiter.record_attempt(identifier)
            
        log_warning(
            "Auth rejected: invalid API key",
            "auth",
            json.dumps({
                "path": request.path, 
                "reason": "invalid_api_key", 
                "request_id": g.request_id,
                "remaining_attempts": api_key_rate_limiter.get_remaining_attempts(identifier) if api_key_rate_limiter else None
            }),
        )
        return _make_error_response(
            "auth_missing_or_invalid",
            "Invalid API key",
            403,
        )
    
    if api_key_rate_limiter:
        api_key_rate_limiter.clear_attempts(identifier)
        
    # Inject a system session for global API key holders to satisfy @require_role decorators
    if not hasattr(g, 'session'):
        from backend.auth import Session
        g.session = Session(
            token="global_api_key",
            user_id=0, # System user
            role="admin", # Give full access
            has_2fa=True, # API key bypasses native 2FA by design
            created_at=datetime.now(),
            expires_at=datetime.now() + timedelta(hours=1)
        )
        
    return None


@app.after_request
def _normalize_api_error_response(response):
    if response is None:
        return response
    if getattr(g, "request_id", None):
        response.headers.setdefault("X-Request-ID", g.request_id)

    if getattr(response, "direct_passthrough", False):
        return response

    status_code = getattr(response, "status_code", None)
    if status_code is None or status_code < 400:
        return response
    mimetype = (getattr(response, "mimetype", "") or "").lower()
    if "application/json" not in mimetype:
        return response

    payload = response.get_json(silent=True)
    if isinstance(payload, dict) and payload.get("success") is False:
        error = payload.get("error")
        if isinstance(error, str):
            payload["error"] = {"code": "error", "message": error}
            response.set_data(json.dumps(payload))
    return response

@app.errorhandler(HTTPException)
def handle_exception(e):
    """Return JSON instead of HTML for HTTP errors."""
    return error_response(
        message=e.description,
        code=e.name.lower().replace(" ", "_"),
        status_code=e.code
    )

@app.errorhandler(Exception)
def handle_unhandled_exception(e):
    """Global exception handler for unhandled errors."""
    # Log the full exception elsewhere, but return a clean JSON to client
    app.logger.exception("Unhandled exception: %s", e)
    return error_response(
        message=str(e),
        code="internal_server_error",
        status_code=500
    )

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def index(path):
    # Try to serve static file
    if path:
        full_path = os.path.join(gui_dir, path)
        if os.path.exists(full_path) and os.path.isfile(full_path):
            return send_from_directory(gui_dir, path)
            
    # API 404s should not serve index.html
    if path.startswith('api/'):
        return error_response("API endpoint not found", code="not_found", status_code=404)
    
    # Fallback to index.html for SPA
    return send_from_directory(gui_dir, 'index.html')

@app.route('/api/healthz')
def healthz():
    return jsonify({'success': True, 'status': 'healthy'})










@app.route('/api/schedules', methods=['GET'])
def get_schedules():
    schedules = db.get_schedules()
    return success_response(data={'schedules': schedules})

@app.route('/api/schedules', methods=['POST'])
@require_role('operator')
def create_schedule():
    try:
        data = request.json
        valid, error = validate_job_name(data.get('name'))
        if not valid:
            return jsonify({'success': False, 'error': error}), 400
        source_id = str(data.get('source_id') or '').strip()
        if not source_id:
            return jsonify({'success': False, 'error': 'source_id is required'}), 400
        if not _source_manager_available():
            return _make_unavailable_response(
                "sources_unavailable",
                "Source store not initialized",
                source_manager_unavailable_reason,
                503,
            )
        source = source_manager.get_source(source_id, include_password=False)
        if not source:
            return jsonify({'success': False, 'error': f"Source '{source_id}' not found"}), 400
        source_type = source.get('source_type') or 'smb'
        source_path = source.get('source_path') or ''
        if source_type != 'smb':
            return _make_error_response(
                "not_implemented",
                "Schedules currently require SMB sources",
                501,
                f"Source type '{source_type}' is not implemented for schedules",
            )
        valid, error = validate_smb_path(source_path)
        if not valid:
            return jsonify({'success': False, 'error': error}), 400

        cron_expr = data.get('cron', '')
        valid, error = scheduler.validate_cron_expression(cron_expr)
        if not valid:
            return error_response(message=error, code="invalid_cron", status_code=400)
        
        drive = int(data.get('drive', 0) or 0)
        drive_count = int(db.get_setting('drive_count', 1))
        if drive < 0 or drive >= drive_count:
            return error_response(message=f'Invalid drive selection: {drive}', code="invalid_drive", status_code=400)

        backup_mode = data.get('backup_mode', 'full')

        compression = data.get('compression')
        if not compression:
            compression = 'zstd' if data.get('compress') else 'none'

        schedule_id = db.create_schedule(
            name=data['name'],
            source_id=source_id,
            cron=cron_expr,
            tapes=data.get('tapes', []),
            verify=is_verification_enabled(),
            duplicate=data.get('duplicate', False),
            compression=compression,
            enabled=data.get('enabled', True),
            source_config={'source_id': source_id, 'source_path': source_path, 'source_type': source_type},
            drive=drive,
            backup_mode=backup_mode
        )
        scheduler.reload_schedules()
        log_success(f"Created schedule #{schedule_id}: {data['name']}", 'schedule')
        return success_response(data={'schedule_id': schedule_id}, status_code=201)
    except Exception as e:
        log_error(f"Failed to create schedule: {e}", 'schedule')
        return error_response(message=str(e))

@app.route('/api/schedules/<int:schedule_id>/toggle', methods=['POST'])
@require_role('operator')
def toggle_schedule(schedule_id):
    try:
        db.toggle_schedule(schedule_id)
        scheduler.reload_schedules()
        return success_response(message="Schedule toggled")
    except Exception as e:
        return error_response(message=str(e))

@app.route('/api/schedules/<int:schedule_id>', methods=['DELETE'])
@require_role('operator')
def delete_schedule(schedule_id):
    try:
        db.delete_schedule(schedule_id)
        scheduler.reload_schedules()
        log_success(f"Deleted schedule #{schedule_id}", 'schedule')
        return success_response(message="Schedule deleted")
    except Exception as e:
        log_error(f"Failed to delete schedule #{schedule_id}: {e}", 'schedule')
        return error_response(message=str(e))

@app.route('/api/schedules/<int:schedule_id>', methods=['PUT', 'PATCH'])
@require_role('operator')
def update_schedule_route(schedule_id):
    try:
        data = request.json
        updates: Dict[str, Any] = {}
        if 'name' in data:
            valid, error = validate_job_name(data['name'])
            if not valid: return jsonify({'success': False, 'error': error}), 400
            updates['name'] = data['name']
        
        if 'cron' in data:
            valid, error = scheduler.validate_cron_expression(data['cron'])
            if not valid: return error_response(message=error, code="invalid_cron", status_code=400)
            updates['cron'] = data['cron']
            
        if 'source_id' in data:
            source_id = str(data['source_id']).strip()
            source = source_manager.get_source(source_id, include_password=False)
            if not source: return jsonify({'success': False, 'error': f"Source '{source_id}' not found"}), 400
            updates['source_id'] = source_id
            updates['source_config'] = {
                'source_id': source_id,
                'source_path': source.get('source_path', ''),
                'source_type': source.get('source_type', 'smb')
            }

        for field in ('tapes', 'verify', 'duplicate', 'drive', 'backup_mode', 'enabled'):
            if field in data:
                updates[field] = data[field]
        
        if 'compression' in data:
            comp = data['compression']
            if isinstance(comp, bool):
                updates['compression'] = 'zstd' if comp else 'none'
            else:
                updates['compression'] = comp
                
        # Handle backward compatibility for compress/compression
        if 'compression' not in updates and 'compress' in data:
            updates['compression'] = 'zstd' if data['compress'] else 'none'

        if db.update_schedule(schedule_id, **updates):
            scheduler.reload_schedules()
            log_success(f"Updated schedule #{schedule_id}", 'schedule')
            return success_response(message="Schedule updated")
        else:
            # If nothing was updated but it exists, still return success or confirm existence
            schedule = db.get_schedule(schedule_id)
            if schedule:
                return success_response(message="No changes made")
            return error_response(message="Schedule not found", status_code=404)
    except Exception as e:
        log_error(f"Failed to update schedule #{schedule_id}: {e}", 'schedule')
        return error_response(message=str(e))









@app.route('/api/settings', methods=['GET'])
@require_role('viewer')
def get_settings():
    settings = db.get_settings()
    for key in list(settings.keys()):
        if 'password' in key.lower() or 'secret' in key.lower():
            settings[key] = '********'
    return success_response(data={'settings': settings})


@app.route('/api/settings', methods=['POST'])
@require_admin
def update_settings():
    try:
        data = request.json
        previous_verification = None
        if data and 'verification_enabled' in data:
            previous_verification = db.get_bool_setting('verification_enabled', True)
        
        db.update_settings(data)
        
        if previous_verification is not None:
            current_verification = db.get_bool_setting('verification_enabled', True)
            if previous_verification != current_verification:
                log_info(
                    f"Verification setting changed from {previous_verification} to {current_verification}",
                    'settings'
                )
                audit_entry = {
                    'action': 'VERIFICATION_TOGGLE',
                    'previous': previous_verification,
                    'current': current_verification,
                    'timestamp': now_utc_iso(),
                    'user': request.headers.get('X-User', 'unknown'),
                    'ip': request.remote_addr
                }
                db.add_audit_log(audit_entry)
        
        return success_response(message="Settings updated")
    except Exception as e:
        return error_response(message=str(e))


@app.route('/api/database/info')
def get_database_info():
    try:
        config = load_config()
        state = load_state()
        configured_path = state.get('DB_PATH') or config.get('DB_PATH', db.db_path)
        return success_response(data={
            'database': {
                'path': db.db_path,
                'configured_path': configured_path,
                'config_path': get_config_path(),
                'state_path': get_state_path()
            }
        })
    except Exception as e:
        return error_response(message=str(e))



# =============================================================================
# Section E: Developer Self-Tests
# =============================================================================

# =============================================================================
# Section J2: Concurrency + Locking
# =============================================================================

class ResourceLock:
    """Simple resource locking with TTL"""
    _locks: Dict[str, Any] = {}
    _lock = threading.Lock()
    
    @classmethod
    def acquire(cls, resource: str, holder: str, ttl_seconds: int = 3600) -> bool:
        """Acquire a lock on a resource"""
        with cls._lock:
            now = time.time()
            
            # Check existing lock
            if resource in cls._locks:
                lock_info = cls._locks[resource]
                if lock_info['expires'] > now:
                    return False  # Lock held by someone else
                # Lock expired, remove it
            
            # Acquire lock
            cls._locks[resource] = {
                'holder': holder,
                'acquired': now,
                'expires': now + ttl_seconds
            }
            return True
    
    @classmethod
    def release(cls, resource: str, holder: str) -> bool:
        """Release a lock"""
        with cls._lock:
            if resource in cls._locks:
                if cls._locks[resource]['holder'] == holder:
                    cls._locks.pop(resource, None)
                    return True
            return False
    
    @classmethod
    def get_status(cls) -> dict:
        """Get all active locks"""
        with cls._lock:
            now = time.time()
            active = {}
            for resource, info in cls._locks.items():
                if info['expires'] > now:
                    active[resource] = {
                        'holder': info['holder'],
                        'remaining_seconds': int(info['expires'] - now)
                    }
            return active

@app.route('/api/locks')
def get_locks():
    """Get current resource locks"""
    return success_response(data={'locks': ResourceLock.get_status()})

# =============================================================================
# Section J3: Maintenance Windows
# =============================================================================

@app.route('/api/maintenance/windows', methods=['GET'])
def get_maintenance_windows():
    """Get configured maintenance windows"""
    try:
        windows = db.get_maintenance_windows()
        return success_response(data={'windows': windows})
    except Exception as e:
        return error_response(message=str(e))

@app.route('/api/maintenance/windows', methods=['POST'])
@require_role('operator')
def add_maintenance_window():
    """Add a maintenance window"""
    try:
        data = request.json
        window_id = db.add_maintenance_window(
            name=data.get('name', 'Maintenance'),
            start_time=data['start_time'],
            end_time=data['end_time'],
            recurring=data.get('recurring', False),
            days=data.get('days', [])
        )
        log_info(f"Added maintenance window: {data.get('name')}", 'system')
        return success_response(data={'window_id': window_id}, message="Maintenance window added")
    except Exception as e:
        return error_response(message=str(e))

@app.route('/api/maintenance/active')
def is_maintenance_active():
    """Check if currently in a maintenance window"""
    try:
        active = db.is_in_maintenance_window()
        return success_response(data={'active': active})
    except Exception as e:
        return error_response(message=str(e))

@app.route('/api/jobs/<int:job_id>/pause', methods=['POST'])
def pause_job(job_id):
    """Pause a running job at next safe checkpoint"""
    try:
        job = db.get_job(job_id)
        if not job:
            return error_response(message="Job not found", code="not_found", status_code=404)
        if job['status'] != 'running':
            return error_response(message="Job is not running", code="invalid_state", status_code=400)
        
        db.update_job_status(job_id, 'pausing')
        backup_engine.request_pause(job_id)
        log_info(f"Pause requested for job #{job_id}", 'job')
        return success_response(message="Pause requested")
    except Exception as e:
        return error_response(message=str(e))

# =============================================================================
# Section J5: Catalog Backup + Restore
# =============================================================================

@app.route('/api/catalog/backup', methods=['POST'])
def backup_catalog():
    """Create a backup of the catalog database"""
    try:
        backup_dir = Path(get_catalog_backup_dir())
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = backup_dir / f'lto_backup_{timestamp}.db'
        
        # Copy database file
        db_path = Path(db.db_path)
        shutil.copy2(db_path, backup_path)
        
        # Verify backup
        backup_size = backup_path.stat().st_size
        original_size = db_path.stat().st_size
        
        if backup_size != original_size:
            backup_path.unlink()
            return error_response(message="Backup verification failed", detail="File size mismatch")
        
        # Calculate checksum
        checksum = hashlib.sha256(backup_path.read_bytes()).hexdigest()
        
        # Store backup info
        db.add_catalog_backup({
            'path': str(backup_path),
            'timestamp': timestamp,
            'size': backup_size,
            'checksum': checksum
        })
        
        log_success(f"Catalog backup created: {backup_path.name}", 'system')
        
        # Cleanup old backups (keep last 10)
        backups = sorted(backup_dir.glob('lto_backup_*.db'))
        if len(backups) > 10:
            for old_backup in backups[:-10]:
                old_backup.unlink()
        
        return success_response(data={
            'backup_path': str(backup_path),
            'size': backup_size,
            'checksum': checksum
        }, message="Catalog backup created")
        
    except Exception as e:
        log_error(f"Catalog backup failed: {e}", 'system')
        return error_response(message=str(e))

@app.route('/api/catalog/backups')
def list_catalog_backups():
    """List available catalog backups"""
    try:
        backups = db.get_catalog_backups()
        return success_response(data={'backups': backups})
    except Exception as e:
        return error_response(message=str(e))

@app.route('/api/catalog/restore', methods=['POST'])
def restore_catalog():
    """Restore catalog from a backup"""
    try:
        data = request.json
        backup_path = data.get('backup_path')
        
        if not backup_path or not Path(backup_path).exists():
            return error_response(message="Backup file not found", code="not_found", status_code=404)
        
        # Verify checksum
        expected_checksum = data.get('checksum')
        if expected_checksum:
            actual_checksum = hashlib.sha256(Path(backup_path).read_bytes()).hexdigest()
            if actual_checksum != expected_checksum:
                return error_response(message="Checksum verification failed", code="invalid_checksum", status_code=400)
        
        # Create safety backup of current DB
        db_path = Path(db.db_path)
        safety_backup = db_path.with_name(
            f'{db_path.name}.pre_restore_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        )
        shutil.copy2(db_path, safety_backup)
        
        # Restore
        shutil.copy2(backup_path, db_path)
        
        log_warning(f"Catalog restored from: {backup_path}", 'system')
        return success_response(data={
            'safety_backup': str(safety_backup)
        }, message="Catalog restored. Restart recommended.")
        
    except Exception as e:
        log_error(f"Catalog restore failed: {e}", 'system')
        return error_response(message=str(e))

@app.route('/api/catalog/rebuild-index', methods=['POST'])
def rebuild_catalog_index():
    """Rebuild the catalog search index (FTS)."""
    try:
        db.rebuild_archived_files_fts()
        log_info("Catalog search index rebuilt", 'system')
        return success_response(message="Catalog index rebuilt")
    except Exception as e:
        log_error(f"Catalog index rebuild failed: {e}", 'system')
        return error_response(message=str(e))

@app.route('/api/catalog/rescan-tape', methods=['POST'])
def rescan_tape_catalog():
    """Rescan a tape and rebuild catalog entries for it."""
    try:
        data = request.get_json() or {}
        barcode = data.get('barcode')
        if not barcode:
            return error_response(message="barcode is required", code="missing_parameter", status_code=400)

        valid, error = validate_tape_identifier(barcode)
        if not valid:
            return error_response(message=error, code="invalid_barcode", status_code=400)

        added = 0
        tape_controller.load_tape(barcode)
        mount_point = tape_controller.mount_ltfs(barcode)
        try:
            db.delete_archived_files_for_tape(barcode)

            for root, _, files in os.walk(mount_point):
                for filename in files:
                    file_path = os.path.join(root, filename)
                    relative_path = os.path.relpath(file_path, mount_point)
                    try:
                        stat = os.stat(file_path)
                    except Exception:
                        continue

                    tape_position = None
                    try:
                        result = subprocess.run(
                            ['getfattr', '-n', 'user.ltfs.startblock', '--only-values', file_path],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        if result.returncode == 0 and result.stdout.strip():
                            tape_position = int(result.stdout.strip())
                    except Exception:
                        tape_position = None

                    db.add_archived_file(
                        job_id=None,
                        tape_barcode=barcode,
                        file_path=relative_path,
                        file_path_on_tape=relative_path,
                        file_size=stat.st_size,
                        checksum=None,
                        tape_position=tape_position
                    )
                    added += 1
        finally:
            tape_controller.unmount_ltfs()
            tape_controller.unload_tape()

        db.rebuild_archived_files_fts()
        log_info(f"Catalog rescan complete for {barcode}: {added} files", 'system')
        return success_response(data={'files_added': added}, message=f"Catalog rescan complete: {added} files added")
    except Exception as e:
        log_error(f"Catalog rescan failed: {e}", 'system')
        return error_response(message=str(e))

# =============================================================================
# Section J6: Tape Manifest Export
# =============================================================================


# =============================================================================
# Section J8: Barcode/Slot Reconciliation  
# =============================================================================


# =============================================================================
# Section J9: Dry-Run / Estimate
# =============================================================================



# =============================================================================
# Section K1: Global Status Banner
# =============================================================================

@app.route('/api/status/banner')
def get_status_banner():
    """Get global status for status banner"""
    try:
        status = {
            'library': {'status': 'unknown', 'message': ''},
            'drive': {'status': 'unknown', 'message': ''},
            'scheduler': {'status': 'unknown', 'message': ''},
            'last_backup': {'status': 'unknown', 'message': ''},
            'alerts': []
        }
        
        # Library status
        try:
            if tape_controller.is_online():
                status['library'] = {'status': 'ok', 'message': 'Online'}
            else:
                status['library'] = {'status': 'error', 'message': 'Offline'}
                status['alerts'].append({'level': 'error', 'message': 'Tape library is offline'})
        except (RuntimeError, OSError, TimeoutError) as e:
            log_warning(f"Library status check failed: {e}", "hardware")
            status['library'] = {'status': 'error', 'message': f'Cannot connect: {type(e).__name__}'}
        
        # Drive status
        try:
            drive_status = tape_controller.get_drive_status()
            if drive_status.get('available'):
                status['drive'] = {'status': 'ok', 'message': 'Available'}
            elif drive_status.get('in_use'):
                status['drive'] = {'status': 'warning', 'message': f"In use: {drive_status.get('loaded_tape', 'tape')}"}
            else:
                status['drive'] = {'status': 'error', 'message': 'Unavailable'}
        except (RuntimeError, OSError, TimeoutError) as e:
            log_warning(f"Drive status check failed: {e}", "hardware")
            status['drive'] = {'status': 'unknown', 'message': f'Error: {type(e).__name__}'}
        
        # Scheduler status
        if scheduler.is_running():
            next_job = scheduler.get_next_scheduled()
            if next_job:
                status['scheduler'] = {'status': 'ok', 'message': f"Next: {next_job['name']}"}
            else:
                status['scheduler'] = {'status': 'ok', 'message': 'Running (no jobs scheduled)'}
        else:
            status['scheduler'] = {'status': 'error', 'message': 'Stopped'}
            status['alerts'].append({'level': 'error', 'message': 'Scheduler is not running'})
        
        # Last backup
        last_job = db.get_last_completed_job()
        if last_job:
            completed_at = datetime.fromisoformat(last_job['completed_at']) if last_job.get('completed_at') else None
            if completed_at:
                age = datetime.now() - completed_at
                if age.days > 7:
                    status['last_backup'] = {'status': 'warning', 'message': f"{age.days} days ago"}
                    status['alerts'].append({'level': 'warning', 'message': f'No backup in {age.days} days'})
                else:
                    status['last_backup'] = {'status': 'ok', 'message': completed_at.strftime('%Y-%m-%d %H:%M')}
        else:
            status['last_backup'] = {'status': 'warning', 'message': 'No backups yet'}
        
        # Check for tapes needing attention
        low_space_tapes = db.get_tapes_below_threshold(10)  # <10% free
        if low_space_tapes:
            status['alerts'].append({
                'level': 'warning',
                'message': f'{len(low_space_tapes)} tape(s) nearly full'
            })
        
        # Overall status
        statuses = [s['status'] for s in [status['library'], status['drive'], status['scheduler'], status['last_backup']]]
        if 'error' in statuses:
            status['overall'] = 'error'
        elif 'warning' in statuses:
            status['overall'] = 'warning'
        else:
            status['overall'] = 'ok'
        
        return success_response(data={'status': status})
        
    except Exception as e:
        return error_response(message=str(e))

# =============================================================================
# Section K1.3: Job Timeline
# =============================================================================

@app.route('/api/jobs/<int:job_id>/timeline')
def get_job_timeline(job_id):
    """Get detailed timeline for a job"""
    try:
        # Check if job exists first
        job = db.get_job(job_id)
        if not job:
            return error_response('Job not found', code="not_found", status_code=404)
            
        timeline = db.get_job_timeline(job_id)
        return success_response(data={'timeline': timeline})
    except Exception as e:
        return error_response(str(e))

# =============================================================================
# PHASE 2: Operator Autopilot System
# =============================================================================

class AutopilotEngine:
    """
    Operator Autopilot: Automated workflow management with safe auto-resolution.
    
    Key principles:
    - Backend owns truth, UI is just view/controller
    - All auto-actions are deterministic, logged, reversible
    - Safety rails prevent destructive automation by default
    - Works without connected web clients
    """
    
    def __init__(self, db, tape_controller, smb_client, backup_engine, source_manager=None, library_manager=None):
        self.db = db
        self.tape_controller = tape_controller
        self.smb_client = smb_client
        self.backup_engine = backup_engine
        self.source_manager = source_manager
        self.library_manager = library_manager
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        
        # Auto-resolve settings
        self.auto_resolve_enabled = True
        self.destructive_automation_enabled = False
        
        # Retry configuration
        self.max_retries = 3
        self.retry_backoff_base = 30  # seconds
        
        # State tracking
        self.current_action = None
        self.pending_alerts = []
        self._last_inventory_scan_at = 0.0
        self._last_inventory_alert_at = 0.0
        self._inventory_alert_cooldown = 300
        self._inventory_alert_ttl = 300
        self._inventory_alert_signatures: Dict[str, float] = {}
        
    def start(self):
        """Start the autopilot background thread"""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            log_info("Autopilot engine started", 'autopilot')
    
    def stop(self):
        """Stop the autopilot"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        log_info("Autopilot engine stopped", 'autopilot')
    
    def _run_loop(self):
        """Main autopilot loop - runs continuously in background"""
        while self.running:
            try:
                self._check_and_resolve()
                time.sleep(10)  # Check every 10 seconds
            except Exception as e:
                log_error(f"Autopilot loop error: {e}", 'autopilot')
                time.sleep(30)  # Back off on error
    
    def _check_and_resolve(self):
        """Check system state and auto-resolve issues where safe"""
        if self.tape_controller.is_busy():
            # Skip resolution cycle if hardware is actively performing a long-running task
            return
            
        with self.lock:
            # Check maintenance windows
            if self.db.is_in_maintenance_window():
                self._handle_maintenance_window()
                return
            
            # Check for SMB connectivity issues on active jobs
            self._check_smb_health()
            
            # Check LTFS mount status
            self._check_ltfs_health()
            
            # Check for library inventory changes
            self._check_library_inventory()
            
            # Resume paused jobs if conditions allow
            self._check_paused_jobs()
    
    def _handle_maintenance_window(self):
        """Pause active jobs during maintenance window"""
        active_jobs = self.db.get_jobs_by_status('running')
        for job in active_jobs:
            if job.get('can_pause', True):
                self._add_timeline_event(job['id'], 'maintenance_pause', 'pending',
                    'Pausing for maintenance window')
                self.backup_engine.request_pause(job['id'])
                self._add_alert('info', f"Job #{job['id']} paused for maintenance", job['id'])
    
    def _check_smb_health(self):
        """Check and auto-resolve SMB connectivity issues"""
        active_jobs = self.db.get_jobs_by_status('running')
        
        for job in active_jobs:
            source = self._get_job_source(job)
            if not source or source.get('source_type') != 'smb':
                continue
            smb_path = source.get('source_path')
            if not smb_path:
                continue
            
            # Check connectivity
            try:
                credentials = self._get_job_credentials(job)
                if not credentials:
                    continue
                connected = self.smb_client.test_connection(smb_path, **credentials)
                if not connected:
                    self._attempt_smb_reconnect(job)
            except Exception as e:
                self._attempt_smb_reconnect(job)
    
    def _attempt_smb_reconnect(self, job):
        """Attempt to reconnect SMB with retries and backoff"""
        if not self.auto_resolve_enabled:
            self._add_alert('warning', f"SMB disconnected for job #{job['id']} - auto-resolve disabled", job['id'])
            return

        source = self._get_job_source(job)
        smb_path = source.get('source_path') if source else None
        if not smb_path:
            self._add_alert('warning', f"Missing source path for job #{job['id']}", job['id'])
            return
        
        job_id = job['id']
        retry_key = f"smb_retry_{job_id}"
        retries = self.db.get_autopilot_state(retry_key, 0)
        
        if retries >= self.max_retries:
            # Escalate to operator
            self._add_alert('error', 
                f"SMB connection failed after {self.max_retries} attempts for job #{job_id}. Manual intervention required.",
                job_id)
            self.db.update_job_status(job_id, 'error', 'SMB connection failed - manual intervention required')
            return
        
        # Attempt reconnect with backoff
        backoff = self.retry_backoff_base * (2 ** retries)
        log_info(f"Attempting SMB reconnect for job #{job_id} (attempt {retries + 1}/{self.max_retries})", 'autopilot')
        
        self._add_timeline_event(job_id, 'smb_reconnect', 'running', 
            f'Auto-reconnect attempt {retries + 1}')
        
        try:
            # Try to remount
            credentials = self._get_job_credentials(job)
            if not credentials:
                raise Exception("Missing credentials for SMB reconnect")
            self.smb_client.reconnect(smb_path, **credentials)
            
            # Verify access
            if self.smb_client.can_read(smb_path, **credentials):
                self._add_timeline_event(job_id, 'smb_reconnect', 'pass', 'Reconnected successfully')
                self.db.set_autopilot_state(retry_key, 0)  # Reset retries
                log_success(f"SMB reconnected for job #{job_id}", 'autopilot')
            else:
                raise Exception("Reconnected but cannot read")
                
        except Exception as e:
            self._add_timeline_event(job_id, 'smb_reconnect', 'fail', str(e))
            self.db.set_autopilot_state(retry_key, retries + 1)
            log_warning(f"SMB reconnect failed for job #{job_id}: {e}", 'autopilot')
            time.sleep(backoff)
    
    def _check_ltfs_health(self):
        """Check and auto-resolve LTFS mount issues"""
        active_jobs = self.db.get_jobs_by_status('running')
        
        for job in active_jobs:
            tape_barcode = job.get('current_tape')
            if not tape_barcode:
                continue
            drive = int(job.get('drive', 0) or 0)
            
            # Check LTFS mount
            try:
                controller = self.tape_controller
                if self.library_manager:
                    found = self.library_manager.find_controller_for_tape(tape_barcode)
                    if found: controller = found

                if not controller.is_ltfs_mounted(drive=drive):
                    self._attempt_ltfs_remount(job, tape_barcode, drive, controller)
            except Exception as e:
                self._attempt_ltfs_remount(job, tape_barcode, drive, controller)

    def _attempt_ltfs_remount(self, job, tape_barcode, drive: int, controller=None):
        """Attempt to remount LTFS with retries"""
        if not self.auto_resolve_enabled:
            self._add_alert('warning', f"LTFS unmounted for tape {tape_barcode} - auto-resolve disabled", job['id'])
            return
        
        job_id = job['id']
        retry_key = f"ltfs_retry_{job_id}_{tape_barcode}"
        retries = self.db.get_autopilot_state(retry_key, 0)
        
        if retries >= self.max_retries:
            self._add_alert('error',
                f"LTFS mount failed after {self.max_retries} attempts for tape {tape_barcode}. Manual intervention required.",
                job_id)
            self.db.update_job_status(job_id, 'error', f'LTFS mount failed for {tape_barcode}')
            return
        
        log_info(
            f"Attempting LTFS remount for {tape_barcode} on drive {drive} "
            f"(attempt {retries + 1})",
            'autopilot'
        )
        
        self._add_timeline_event(job_id, 'ltfs_remount', 'running',
            f'Auto-remount attempt {retries + 1} for {tape_barcode}')
        
        drive_lock = None
        if not self.backup_engine:
            self._add_alert('warning', "Drive locks unavailable - LTFS remount deferred", job_id)
            return
        drive_lock = self.backup_engine.drive_locks[drive]
        if not drive_lock.acquire(blocking=False):
            self._add_alert('warning', f"Drive {drive} busy - LTFS remount deferred", job_id)
            return

        try:
            target_controller = controller or self.tape_controller
            target_controller.mount_ltfs(tape_barcode, drive=drive)
            
            # Verify mount
            if target_controller.is_ltfs_mounted(drive=drive):
                self._add_timeline_event(job_id, 'ltfs_remount', 'pass', 'Remounted successfully')
                self.db.set_autopilot_state(retry_key, 0)
                log_success(f"LTFS remounted for {tape_barcode}", 'autopilot')
            else:
                raise Exception("Mount completed but verification failed")
                
        except Exception as e:
            self._add_timeline_event(job_id, 'ltfs_remount', 'fail', str(e))
            self.db.set_autopilot_state(retry_key, retries + 1)
            log_warning(f"LTFS remount failed for {tape_barcode}: {e}", 'autopilot')
        finally:
            if drive_lock and drive_lock.locked():
                drive_lock.release()
    
    def _check_library_inventory(self):
        """Check for library inventory changes and reconcile"""
        try:
            # Quick status check
            if not self.tape_controller.is_online():
                return
            
            # Rate-gate: only poll the hardware bus at most once per 60 seconds.
            # inventory_may_have_changed() runs `mtx status` internally; calling it
            # every 10 s saturates the SCSI bus and starves HTTP workers.
            now = time.time()
            if now - self._last_inventory_scan_at < 60:
                return
            
            self._last_inventory_scan_at = now

            # Check if inventory might have changed (door opened, etc.)
            if self.tape_controller.inventory_may_have_changed():
                if now - self._last_inventory_alert_at > self._inventory_alert_cooldown:
                    log_info("Library inventory change detected, initiating scan", 'autopilot')
                    self._add_alert('info', 'Library inventory change detected - scanning', None)
                    self._last_inventory_alert_at = now
                
                # Scan and reconcile (fast mode: mtx status only, no blocking mtx inventory)
                scan = []
                if self.library_manager:
                    for c in self.library_manager.controllers.values():
                        try:
                            scan.extend(c.scan_library('fast'))
                        except: pass
                else:
                    scan = self.tape_controller.scan_library('fast')
                
                # Get reconciliation status
                recon = self._get_reconciliation_status(scan=scan)
                if recon['needs_reconciliation']:
                    signature = json.dumps({
                        "library_only": sorted(recon['library_only'], key=lambda entry: entry.get('barcode') or ''),
                        "db_only": sorted(recon['db_only'], key=lambda entry: entry.get('barcode') or ''),
                        "slot_mismatch": sorted(recon['slot_mismatch'], key=lambda entry: entry.get('barcode') or ''),
                        "unknown_drives": sorted(recon.get('unknown_drives', []), key=lambda entry: entry.get('drive') or 0),
                    }, sort_keys=True)
                    if self._should_emit_inventory_alert(signature, now):
                        self._last_inventory_alert_at = now
                        if self.destructive_automation_enabled:
                            # Auto-reconcile if destructive automation enabled
                            self._perform_auto_reconciliation(recon)
                        else:
                            mismatch_count = len(recon['library_only']) + len(recon['db_only']) + len(recon['slot_mismatch'])
                            if mismatch_count:
                                self._add_alert(
                                    'warning',
                                    f"Library inventory mismatch detected: {len(recon['library_only'])} new, {len(recon['db_only'])} missing. Manual reconciliation required.",
                                    None,
                                )
                            if recon.get('unknown_drives'):
                                self._add_alert(
                                    'warning',
                                    "Drive contains unknown tape. Rescan or label the tape to reconcile inventory.",
                                    None,
                                )
                else:
                    self._last_inventory_alert_at = now
                            
        except Exception as e:
            log_warning(f"Library inventory check failed: {e}", 'autopilot')

    def _should_emit_inventory_alert(self, signature: str, now: float) -> bool:
        last_alert = self._inventory_alert_signatures.get(signature)
        if last_alert and now - last_alert < self._inventory_alert_ttl:
            return False
        self._inventory_alert_signatures[signature] = now
        expired_cutoff = now - self._inventory_alert_ttl
        for key, timestamp in list(self._inventory_alert_signatures.items()):
            if timestamp < expired_cutoff:
                self._inventory_alert_signatures.pop(key, None)
        return True
    
    def _get_reconciliation_status(self, scan: Optional[List[Dict[str, object]]] = None):
        """Get reconciliation status between DB and library"""
        db_tapes = {t['barcode']: t for t in self.db.get_tape_inventory()}
        if scan is None:
            scan = self.tape_controller.scan_barcodes()
        library_tapes = {}
        for tape in scan:
            barcode = tape.get('barcode')
            if not barcode:
                continue
            slot = tape.get('slot')
            if slot is None and tape.get('drive_source_slot') is not None:
                slot = tape.get('drive_source_slot')
            library_tapes[barcode] = {**tape, "slot": slot}
        unknown_drives = [
            {
                'drive': t.get('drive_index'),
                'source_slot': t.get('drive_source_slot'),
            }
            for t in scan
            if t.get('location_type') == 'drive' and t.get('drive_full') and not t.get('barcode')
        ]
        
        result = {
            'db_only': [],
            'library_only': [],
            'slot_mismatch': [],
            'matched': [],
            'unknown_drives': unknown_drives,
            'needs_reconciliation': False
        }
        
        all_barcodes = set(db_tapes.keys()) | set(library_tapes.keys())
        
        for barcode in all_barcodes:
            in_db = barcode in db_tapes
            in_library = barcode in library_tapes
            
            if in_db and not in_library:
                result['db_only'].append({'barcode': barcode, 'slot': db_tapes[barcode].get('slot')})
            elif in_library and not in_db:
                result['library_only'].append({'barcode': barcode, 'slot': library_tapes[barcode].get('slot')})
            elif in_db and in_library:
                if db_tapes[barcode].get('slot') != library_tapes[barcode].get('slot'):
                    result['slot_mismatch'].append({
                        'barcode': barcode,
                        'db_slot': db_tapes[barcode].get('slot'),
                        'library_slot': library_tapes[barcode].get('slot')
                    })
                else:
                    result['matched'].append(barcode)
        
        result['needs_reconciliation'] = bool(
            result['db_only'] or result['library_only'] or result['slot_mismatch'] or result['unknown_drives']
        )
        return result
    
    def _perform_auto_reconciliation(self, recon):
        """Perform automatic reconciliation (only if destructive automation enabled)"""
        log_info("Performing automatic reconciliation", 'autopilot')
        
        actions = []
        
        # Add new tapes from library
        for tape in recon['library_only']:
            self.db.add_tape(tape['barcode'], tape['slot'])
            actions.append(f"Added {tape['barcode']}")
        
        # Update slot mismatches
        for tape in recon['slot_mismatch']:
            self.db.update_tape_slot(tape['barcode'], tape['library_slot'])
            actions.append(f"Updated slot for {tape['barcode']}")
        
        # Note: We don't auto-remove tapes from DB - that requires manual confirmation
        if recon['db_only']:
            self._add_alert('warning',
                f"{len(recon['db_only'])} tape(s) in database but not in library. Manual review required.",
                None)
        
        if actions:
            log_success(f"Auto-reconciled library: {len(actions)} changes", 'autopilot')
            self._add_alert('info', f"Auto-reconciled library: {len(actions)} changes", None)
    
    def _check_paused_jobs(self):
        """Check if paused jobs can be resumed"""
        if self.db.is_in_maintenance_window():
            return  # Don't resume during maintenance
        
        paused_jobs = self.db.get_jobs_by_status('paused')
        
        for job in paused_jobs:
            pause_reason = job.get('pause_reason', '')
            
            # Check if pause reason is resolved
            can_resume = True
            
            if 'maintenance' in pause_reason.lower():
                can_resume = not self.db.is_in_maintenance_window()
            elif 'smb' in pause_reason.lower():
                credentials = self._get_job_credentials(job)
                source = self._get_job_source(job)
                source_path = source.get('source_path') if source else ''
                can_resume = credentials and source_path and self.smb_client.test_connection(
                    source_path,
                    **credentials
                )
            elif 'ltfs' in pause_reason.lower():
                tape = job.get('current_tape')
                can_resume = tape and self.tape_controller.is_ltfs_mounted(tape)
            
            if can_resume and self.auto_resolve_enabled:
                log_info(f"Resuming job #{job['id']} - conditions resolved", 'autopilot')
                self._add_timeline_event(job['id'], 'auto_resume', 'pass', 'Conditions resolved, resuming')
                self.backup_engine.resume_job(job['id'])
    
    def _add_timeline_event(self, job_id, step, status, message):
        """Add event to job timeline"""
        try:
            self.db.add_timeline_event(job_id, f'autopilot_{step}', status, message)
        except (sqlite3.Error, OSError) as e:
            log_warning(f"Failed to add timeline event for job {job_id}: {e}", "autopilot")

    def _get_job_source(self, job: dict) -> Optional[dict]:
        """Resolve source_id to source details."""
        source_id = job.get('source_id')
        if not source_id or not self.source_manager:
            return None
        return self.source_manager.get_source(source_id, include_password=True)

    def _get_job_credentials(self, job: dict) -> Optional[dict]:
        """Resolve SMB credentials for a job."""
        source = self._get_job_source(job)
        if not source:
            self._add_alert('warning', f"Missing source for job #{job.get('id')}", job.get('id'))
            return None
        if source.get('source_type') != 'smb':
            return None
        return {
            'username': source.get('username', ''),
            'password': source.get('password', ''),
            'domain': source.get('domain', '')
        }
    
    def _add_alert(self, level, message, job_id=None):
        """Add an alert for operator attention"""
        correlation_id = f"alert_{int(time.time() * 1000)}"
        alert = {
            'id': correlation_id,
            'level': level,
            'message': message,
            'job_id': job_id,
            'timestamp': now_utc_iso(),
            'acknowledged': False
        }
        self.db.add_autopilot_alert(alert)
        log_info(f"Alert [{level}]: {message}", 'autopilot')
    
    def get_next_action(self):
        """Get the next required operator action"""
        # Check for unacknowledged critical alerts
        alerts = self.db.get_autopilot_alerts(acknowledged=False, level='error')
        if alerts:
            alert = alerts[0]
            return {
                'type': 'resolve_alert',
                'message': alert['message'],
                'correlation_id': alert['id'],
                'required_inputs': [],
                'preconditions': [],
                'severity': 'high'
            }
        
        # Check for blocked jobs
        blocked_jobs = self.db.get_jobs_by_status('blocked')
        if blocked_jobs:
            job = blocked_jobs[0]
            return {
                'type': 'unblock_job',
                'message': f"Job #{job['id']} is blocked: {job.get('block_reason', 'Unknown')}",
                'correlation_id': f"job_{job['id']}",
                'required_inputs': ['resolution_action'],
                'preconditions': [],
                'severity': 'medium'
            }
        
        # Check for queued jobs that need resources
        queued_jobs = self.db.get_jobs_by_status('queued')
        for job in queued_jobs:
            lock_holder = ResourceLock.get_status().get(f"drive_0")
            if lock_holder:
                return {
                    'type': 'wait_for_resource',
                    'message': f"Job #{job['id']} waiting for drive (held by {lock_holder['holder']})",
                    'correlation_id': f"job_{job['id']}",
                    'required_inputs': [],
                    'preconditions': ['drive_available'],
                    'severity': 'low'
                }
        
        # Check for tapes needing attention
        low_space_tapes = self.db.get_tapes_below_threshold(5)
        if low_space_tapes:
            return {
                'type': 'tape_attention',
                'message': f"{len(low_space_tapes)} tape(s) nearly full (<5% free)",
                'correlation_id': 'tape_space',
                'required_inputs': [],
                'preconditions': [],
                'severity': 'low'
            }
        
        # All clear
        return {
            'type': 'none',
            'message': 'System operating normally',
            'correlation_id': None,
            'required_inputs': [],
            'preconditions': [],
            'severity': 'none'
        }
    
    def get_status(self):
        """Get complete autopilot status"""
        return {
            'enabled': self.auto_resolve_enabled,
            'destructive_automation': self.destructive_automation_enabled,
            'running': self.running,
            'next_action': self.get_next_action(),
            'active_alerts': self.db.get_autopilot_alerts(acknowledged=False),
            'recent_actions': self.db.get_recent_autopilot_actions(limit=10),
            'resource_locks': ResourceLock.get_status()
        }
    
    def set_auto_resolve(self, enabled: bool):
        """Enable/disable auto-resolve"""
        self.auto_resolve_enabled = enabled
        self.db.set_setting('autopilot_auto_resolve', enabled)
        log_info(f"Auto-resolve {'enabled' if enabled else 'disabled'}", 'autopilot')
    
    def set_destructive_automation(self, enabled: bool):
        """Enable/disable destructive automation (admin only)"""
        self.destructive_automation_enabled = enabled
        self.db.set_setting('autopilot_destructive', enabled)
        log_warning(f"Destructive automation {'ENABLED' if enabled else 'disabled'}", 'autopilot')
    
    def acknowledge_alert(self, alert_id: str):
        """Acknowledge an alert"""
        self.db.acknowledge_autopilot_alert(alert_id)
        log_info(f"Alert {alert_id} acknowledged", 'autopilot')



# Autopilot will be initialized by create_app()

# =============================================================================
# Autopilot API Endpoints
# =============================================================================

def _ensure_autopilot_available():
    available, reason = _hardware_available()
    if not available or autopilot is None:
        return _make_unavailable_response(
            "hardware_unavailable",
            "Tape hardware not available",
            reason or "Autopilot not initialized",
            503,
        )
    return None


@app.route('/api/autopilot/status')
def get_autopilot_status():
    """Get complete autopilot status"""
    try:
        unavailable = _ensure_autopilot_available()
        if unavailable:
            return unavailable
        return success_response(data={'status': autopilot.get_status()})
    except Exception as e:
        return error_response(message=str(e))

@app.route('/api/autopilot/next-action')
def get_next_action_api():
    """Get the next required operator action"""
    try:
        unavailable = _ensure_autopilot_available()
        if unavailable:
            return unavailable
        return success_response(data={'action': autopilot.get_next_action()})
    except Exception as e:
        return error_response(message=str(e))

@app.route('/api/autopilot/settings', methods=['GET'])
def get_autopilot_settings():
    """Get autopilot settings"""
    try:
        unavailable = _ensure_autopilot_available()
        if unavailable:
            return unavailable
        return success_response(data={
            'settings': {
                'auto_resolve_enabled': autopilot.auto_resolve_enabled,
                'destructive_automation_enabled': autopilot.destructive_automation_enabled,
                'max_retries': autopilot.max_retries,
                'retry_backoff_base': autopilot.retry_backoff_base
            }
        })
    except Exception as e:
        return error_response(message=str(e))

@app.route('/api/autopilot/settings', methods=['POST'])
def update_autopilot_settings():
    """Update autopilot settings"""
    try:
        unavailable = _ensure_autopilot_available()
        if unavailable:
            return unavailable
        data = request.json
        if not isinstance(data, dict):
            return error_response(message="Invalid settings payload", code="invalid_request", status_code=400)
        
        if 'auto_resolve_enabled' in data:
            autopilot.set_auto_resolve(data['auto_resolve_enabled'])
        
        if 'destructive_automation_enabled' in data:
            # This is a sensitive setting - log it
            log_warning(f"Destructive automation setting changed to: {data['destructive_automation_enabled']}", 'autopilot')
            autopilot.set_destructive_automation(data['destructive_automation_enabled'])
        
        return success_response(message="Autopilot settings updated")
    except Exception as e:
        return error_response(message=str(e))

@app.route('/api/autopilot/alerts')
def get_autopilot_alerts():
    """Get autopilot alerts"""
    try:
        acknowledged = request.args.get('acknowledged', 'false').lower() == 'true'
        alerts = db.get_autopilot_alerts(acknowledged=acknowledged)
        return success_response(data={'alerts': alerts})
    except Exception as e:
        return error_response(message=str(e))

@app.route('/api/autopilot/alerts/<alert_id>/acknowledge', methods=['POST'])
def acknowledge_alert(alert_id):
    """Acknowledge an alert"""
    try:
        unavailable = _ensure_autopilot_available()
        if unavailable:
            return unavailable
        autopilot.acknowledge_alert(alert_id)
        return success_response(message="Alert acknowledged")
    except Exception as e:
        return error_response(message=str(e))

@app.route('/api/autopilot/actions')
def get_autopilot_actions():
    """Get recent autopilot actions"""
    try:
        limit = request.args.get('limit', 50, type=int)
        actions = db.get_recent_autopilot_actions(limit=limit)
        return success_response(data={'actions': actions})
    except Exception as e:
        return error_response(message=str(e))

# =============================================================================
# Global Status Banner API
# =============================================================================

@app.route('/api/banner')
def get_global_banner():
    """Get global status banner data for header display"""
    try:
        banner = {
            'library_online': False,
            'library_state': 'OFFLINE',
            'active_jobs': 0,
            'queued_jobs': 0,
            'critical_alerts': 0,
            'maintenance_mode': False,
            'last_backup': None,
            'warnings': []
        }
        recovery_state = _get_recovery_state()
        if recovery_state:
            banner['warnings'].append(
                "Recovery required: unload tape to a chosen slot before continuing."
            )
        
        # Library status
        try:
            banner['library_state'] = tape_controller.get_library_state()
            banner['library_online'] = banner['library_state'] != 'OFFLINE'
            if banner['library_state'] == 'OFFLINE':
                banner['warnings'].append('Library offline')
            elif banner['library_state'] == 'DEGRADED':
                banner['warnings'].append('Library degraded')
            elif banner['library_state'] == 'BUSY':
                banner['warnings'].append('Library busy')
        except (RuntimeError, OSError, TimeoutError) as e:
            log_warning(f"Failed to check library status for banner: {e}", "banner")
            banner['warnings'].append('Cannot check library status')
        
        # Active/queued jobs
        try:
            jobs = db.get_active_jobs()
            banner['active_jobs'] = len([j for j in jobs if j.get('status') == 'running'])
            banner['queued_jobs'] = len([j for j in jobs if j.get('status') == 'queued'])
        except (sqlite3.Error, OSError) as e:
            log_warning(f"Failed to get active jobs for banner: {e}", "banner")
        
        # Critical alerts
        try:
            alerts = db.get_autopilot_alerts(acknowledged=False, level='error')
            banner['critical_alerts'] = len(alerts)
            if banner['critical_alerts'] > 0:
                banner['warnings'].append(f'{banner["critical_alerts"]} critical alert(s)')
        except (sqlite3.Error, OSError) as e:
            log_warning(f"Failed to get alerts for banner: {e}", "banner")
        
        # Maintenance mode
        try:
            banner['maintenance_mode'] = db.is_in_maintenance_window()
            if banner['maintenance_mode']:
                banner['warnings'].append('Maintenance mode active')
        except (sqlite3.Error, OSError) as e:
            log_warning(f"Failed to check maintenance mode for banner: {e}", "banner")
        
        # Last successful backup
        try:
            last_job = db.get_last_completed_job()
            if last_job and last_job.get('completed_at'):
                banner['last_backup'] = last_job['completed_at']
                # Check if stale
                completed = datetime.fromisoformat(last_job['completed_at'])
                if (datetime.now() - completed).days > 7:
                    banner['warnings'].append(f'No backup in {(datetime.now() - completed).days} days')
        except (sqlite3.Error, OSError, ValueError) as e:
            log_warning(f"Failed to get last backup for banner: {e}", "banner")
        
        # Determine overall status
        if banner['critical_alerts'] > 0 or not banner['library_online']:
            banner['status'] = 'error'
        elif len(banner['warnings']) > 0:
            banner['status'] = 'warning'
        else:
            banner['status'] = 'ok'
        
        return success_response(data={'banner': banner})
    except Exception as e:
        return error_response(message=str(e))

# Source Management API - HANDLED BY BLUEPRINT in routes/sources.py

@app.route('/api/sources/browse', methods=['POST'])
def browse_source():
    """Browse paths for a source (Local or SMB)."""
    try:
        payload = request.get_json(silent=True) or {}
        source_type = str(payload.get('source_type') or 'local').strip().lower()
        path = str(payload.get('path') or '').strip()
        
        if source_type == 'rsync':
            # Use SSHSource to browse
            host = payload.get('rsync_host')
            user = payload.get('rsync_user')
            port = payload.get('rsync_port', 22)
            if not host or not user:
                return _make_error_response("invalid_request", "Host and user required for remote browse", 400)
            
            result = SSHSource.list_remote_dir(host, user, path, port)
            return jsonify({'success': True, 'data': result})
            
        elif source_type == 's3':
            # Use RcloneSource to browse
            remote = payload.get('s3_bucket')
            if not remote:
                return _make_error_response("invalid_request", "Remote name required for rclone browse", 400)
            
            result = RcloneSource.list_files(remote, path)
            return jsonify({'success': True, 'data': result})
            
        elif source_type == 'local':
            # Identify home directory if path is empty/root for better UX
            if not path or path == '/':
                 path = os.path.expanduser("~")

            try:
                result = LocalSource.list_files(path, show_hidden=False)
                return jsonify({'success': True, 'data': result})
            except Exception as e:
                return _make_error_response("browse_failed", str(e), 400)
        
        elif source_type == 'smb':
            smb_path = payload.get('source_path')
            if not smb_path:
                return _make_error_response("invalid_request", "SMb source_path is required", 400)

            username = payload.get('username') or ''
            password = payload.get('password') or ''
            domain = payload.get('domain')
            
            # Combine smb_path with browsed sub-path if needed
            # For now, list_files takes the full share path + remote path
            # But the frontend might send just the appended path.
            # Let's assume 'path' is relative to the share root if provided.
            
            if smb_client is None:
                 return _make_unavailable_response("smb_unavailable", "SMB client missing", None, 503)
            
            # If path is provided, it's relative to the share
            remote_path = path if path and path != '/' else ''
            # Normalize slashes for SMB
            remote_path = remote_path.replace('/', '\\')
            
            try:
                files = smb_client.list_files(smb_path, username, password, remote_path=remote_path, domain=domain)
                # Normalize response to match LocalSource structure
                entries = []
                for f in files:
                    entries.append({
                        'name': f['name'],
                        'path': f['name'], # For SMB, path is name relative to current dir usually
                        'is_dir': f['is_dir'],
                        'size': f['size'],
                        'mtime': 0, # SMB might not give this cheaply
                        'last_modified': '' 
                    })
                # Helper for parent logic?
                # For SMB, we rely on client state or just let UI handle breadcrumbs.
                
                return jsonify({
                    'success': True, 
                    'data': {
                        'path': path, 
                        'entries': entries,
                        'parent': '..' if path else None 
                    }
                })
            except Exception as e:
                 return _make_error_response("browse_failed", str(e), 400)
                 
        return _make_error_response("invalid_request", f"Browse not supported for {source_type}", 400)

    except Exception as e:
        app.logger.exception("Browse failed")
        return _make_error_response("unknown_error", str(e), 500)

# =============================================================================
# External Media API
# =============================================================================

@app.route('/api/external-media')
def list_external_media():
    """List all detected external/removable drives."""
    try:
        from backend.utils.external_media import list_external_drives
        drives = list_external_drives()
        return jsonify({'success': True, 'drives': drives})
    except Exception as e:
        app.logger.exception("Failed to list external media")
        return _make_error_response("external_media_error", str(e), 500)

@app.route('/api/external-media/<path:device>/info')
def get_external_media_info(device):
    """Get detailed information about a specific external drive."""
    try:
        from backend.utils.external_media import get_drive_info
        # Prepend /dev/ if not present
        if not device.startswith('/dev/'):
            device = f'/dev/{device}'
        
        info = get_drive_info(device)
        if not info:
            return _make_error_response("not_found", "Drive not found", 404)
        
        return jsonify({'success': True, 'drive': info})
    except Exception as e:
        app.logger.exception("Failed to get drive info")
        return _make_error_response("external_media_error", str(e), 500)

@app.route('/api/external-media/<path:device>/mount', methods=['POST'])
def mount_external_media(device):
    """Mount an external drive."""
    try:
        from backend.utils.external_media import mount_drive
        # Prepend /dev/ if not present
        if not device.startswith('/dev/'):
            device = f'/dev/{device}'
        
        payload = request.get_json(silent=True) or {}
        mount_point = payload.get('mount_point')
        
        success, result = mount_drive(device, mount_point)
        if success:
            log_info(f"Mounted external drive {device} at {result}", 'system')
            return jsonify({'success': True, 'mount_point': result})
        else:
            return _make_error_response("mount_failed", result, 400)
    except Exception as e:
        app.logger.exception("Failed to mount drive")
        return _make_error_response("external_media_error", str(e), 500)

@app.route('/api/external-media/<path:device>/unmount', methods=['POST'])
def unmount_external_media(device):
    """Unmount an external drive."""
    try:
        from backend.utils.external_media import unmount_drive, is_safe_to_unmount
        # Prepend /dev/ if not present
        if not device.startswith('/dev/'):
            device = f'/dev/{device}'
        
        # Check if safe to unmount
        is_safe, reason = is_safe_to_unmount(device)
        if not is_safe:
            return _make_error_response("unsafe_unmount", reason, 400)
        
        success, result = unmount_drive(device)
        if success:
            log_info(f"Unmounted external drive {device}", 'system')
            return jsonify({'success': True, 'message': result})
        else:
            return _make_error_response("unmount_failed", result, 400)
    except Exception as e:
        app.logger.exception("Failed to unmount drive")
        return _make_error_response("external_media_error", str(e), 500)


# =============================================================================
# NFS API
# =============================================================================

@app.route('/api/ssh/public_key', methods=['GET'])
def get_appliance_public_key():
    """Retrieve the appliance SSH public key for remote setup."""
    try:
        pub_key = SSHSource.get_public_key()
        if not pub_key:
            return _make_error_response("key_error", "Failed to retrieve SSH key", 500)
        return jsonify({'success': True, 'public_key': pub_key})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ssh/test', methods=['POST'])
def test_ssh_connection():
    """Test SSH connectivity using the appliance key."""
    try:
        payload = request.get_json(silent=True) or {}
        host = payload.get('host')
        user = payload.get('user')
        port = payload.get('port', 22)
        if not host or not user:
            return _make_error_response("invalid_request", "Host and user required", 400)
            
        success, message = SSHSource.test_connection(host, user, port)
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/rclone/remotes', methods=['GET'])
def list_rclone_remotes():
    """List configured rclone remotes."""
    try:
        remotes = RcloneSource.list_remotes()
        return jsonify({'success': True, 'remotes': remotes})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/rclone/test', methods=['POST'])
def test_rclone_remote():
    """Test rclone remote access."""
    try:
        payload = request.get_json(silent=True) or {}
        remote = payload.get('remote')
        if not remote:
            return _make_error_response("invalid_request", "Remote name required", 400)
            
        success, message = RcloneSource.test_remote(remote)
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/nfs/exports')
def list_nfs_exports():
    """List available NFS exports from a server."""
    try:
        server = request.args.get('server')
        if not server:
            return _make_error_response("invalid_request", "Server parameter is required", 400)
        
        from backend.sources.nfs_source import NFSSource
        success, exports, error = NFSSource.list_exports(server)
        
        if not success:
            return _make_error_response("nfs_error", error, 400)
        
        return jsonify({'success': True, 'exports': exports})
    except Exception as e:
        app.logger.exception("Failed to list NFS exports")
        return _make_error_response("nfs_error", str(e), 500)



# =============================================================================
# Drive Maintenance API
# =============================================================================

@app.route('/api/drive/health')
def get_drive_health():
    """Get drive health status including cleaning requirements."""
    try:
        drive_id = request.args.get('drive')
        
        # Check for active hardware-locking jobs
        active_jobs = db.get_active_jobs() if db else []
        hardware_locked = any(j.get('job_type') in ('tape_wipe', 'tape_format') for j in active_jobs)
        if hardware_locked:
            return jsonify({'success': True, 'health': {'status': 'busy', 'message': 'Operation in progress'}})
        
        from backend.drive_maintenance import DriveMaintenanceManager
        settings = db.get_settings()
        maintenance_config = settings.get('maintenance', {})
        
        manager = DriveMaintenanceManager(
            tape_controller,
            config=maintenance_config,
            log_callback=lambda msg, lvl: log_info(msg, lvl)
        )
        
        if drive_id is not None:
            # Single drive requested
            drive_idx = int(drive_id)
            health = manager.get_drive_health(drive_idx)
            return jsonify({
                'success': True,
                'health': {
                    'cleaning_required': health.cleaning_required,
                    'cleaning_suggested': health.cleaning_suggested,
                    'last_cleaned': health.last_cleaned.isoformat() if health.last_cleaned else None,
                    'cleaning_cycles': health.cleaning_cycles,
                    'head_hours': health.head_hours,
                    'error_rate': health.error_rate,
                    'last_check': health.last_check.isoformat() if health.last_check else None,
                }
            })
        else:
            # All drives requested (as expected by Dashboard)
            drives = []
            if tape_controller:
                # Use drive_devices to determine count or tape_controller properties
                drive_count = len(getattr(tape_controller, 'drive_devices', [0]))
                for i in range(drive_count):
                    health = manager.get_drive_health(i)
                    # We also need basic drive info like status and current_tape
                    # Get this from tape_controller if available
                    status = "idle"
                    current_tape = None
                    try:
                        info = tape_controller.get_drive_status(i)
                        status = info.get('status', 'idle')
                        current_tape = info.get('current_tape')
                    except:
                        pass
                        
                    drives.append({
                        'id': i,
                        'type': 'LTO',
                        'status': status,
                        'current_tape': current_tape,
                        'cleaning_needed': health.cleaning_required,
                        'health': {
                            'cleaning_required': health.cleaning_required,
                            'cleaning_suggested': health.cleaning_suggested,
                            'last_cleaned': health.last_cleaned.isoformat() if health.last_cleaned else None,
                            'cleaning_cycles': health.cleaning_cycles,
                            'head_hours': health.head_hours,
                            'error_rate': health.error_rate,
                        }
                    })
            
            return jsonify({
                'success': True,
                'data': {
                    'drives': drives
                },
                'drives': drives # Backward compatibility if needed
            })
    except Exception as e:
        app.logger.exception("Failed to get drive health")
        return _make_error_response("drive_error", str(e), 500)
    except Exception as e:
        app.logger.exception("Failed to get drive health")
        return _make_error_response("drive_error", str(e), 500)

@app.route('/api/drive/clean', methods=['POST'])
def trigger_drive_cleaning():
    """Trigger drive cleaning operation."""
    try:
        payload = request.get_json(silent=True) or {}
        drive = int(payload.get('drive', 0))
        force = payload.get('force', False)
        
        from backend.drive_maintenance import DriveMaintenanceManager
        settings = db.get_settings()
        maintenance_config = settings.get('maintenance', {})
        
        manager = DriveMaintenanceManager(
            tape_controller,
            config=maintenance_config,
            log_callback=lambda msg, lvl: log_info(msg, lvl)
        )
        
        success, message = manager.run_cleaning(drive, force=force)
        
        if success:
            log_info(f"Drive {drive} cleaning completed", 'system')
            return jsonify({'success': True, 'message': message})
        else:
            return _make_error_response("cleaning_failed", message, 400)
    except Exception as e:
        app.logger.exception("Failed to clean drive")
        return _make_error_response("drive_error", str(e), 500)

@app.route('/api/drive/cleaning-tapes')
def get_cleaning_tapes():
    """Get list of cleaning tapes and their usage."""
    try:
        from backend.drive_maintenance import DriveMaintenanceManager
        settings = db.get_settings()
        maintenance_config = settings.get('maintenance', {})
        
        manager = DriveMaintenanceManager(
            tape_controller,
            config=maintenance_config
        )
        
        tapes = manager.get_cleaning_tape_usage()
        return jsonify({'success': True, 'tapes': tapes})
    except Exception as e:
        app.logger.exception("Failed to get cleaning tapes")
        return _make_error_response("drive_error", str(e), 500)


# =============================================================================
# Notifications API
# =============================================================================

@app.route('/api/notifications/settings')
def get_notification_settings():
    """Get notification configuration."""
    try:
        settings = get_settings()
        notifications = settings.get('notifications', {})
        return jsonify({'success': True, 'settings': notifications})
    except Exception as e:
        app.logger.exception("Failed to get notification settings")
        return _make_error_response("settings_error", str(e), 500)

@app.route('/api/notifications/settings', methods=['POST'])
def update_notification_settings():
    """Update notification configuration."""
    try:
        payload = request.get_json()
        if not payload:
            return _make_error_response("invalid_request", "Request body is required", 400)
        
        # Get current settings
        settings = get_settings()
        
        # Update notifications section
        settings['notifications'] = payload
        
        # Save settings
        save_settings(settings)
        
        # Reload notification manager with new config
        from backend.notifications.manager import notification_manager
        notification_manager.load_config(payload)
        
        log_info("Notification settings updated", 'system')
        return jsonify({'success': True})
    except Exception as e:
        app.logger.exception("Failed to update notification settings")
        return _make_error_response("settings_error", str(e), 500)

@app.route('/api/notifications/test', methods=['POST'])
def test_notification():
    """Test notification delivery."""
    try:
        payload = request.get_json()
        if not payload:
            return _make_error_response("invalid_request", "Request body is required", 400)
        
        provider_type = payload.get('provider')  # 'smtp' or 'webhook'
        if not provider_type:
            return _make_error_response("invalid_request", "Provider type is required", 400)
        
        # Get current settings
        settings = get_settings()
        notifications = settings.get('notifications', {})
        
        # Load config and test
        from backend.notifications.manager import notification_manager
        notification_manager.load_config(notifications)
        
        success, message = notification_manager.test_provider(provider_type)
        
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return _make_error_response("test_failed", message, 400)
    except Exception as e:
        app.logger.exception("Failed to test notification")
        return _make_error_response("test_error", str(e), 500)


# =============================================================================
# Authentication API
# =============================================================================

@app.route('/api/csrf-token', methods=['GET'])
def get_csrf_token():
    """Return a CSRF token for the frontend."""
    return jsonify({'csrf_token': generate_csrf()})



# Note: Auth, Setup, and User management routes have been moved to:
# - backend/routes/auth.py (auth_bp)
# - backend/routes/setup.py (setup_bp)


# =============================================================================
# Tape Spanning API
# =============================================================================

# Initialize Spanning Manager
from backend.tape_spanning import TapeSpanningManager
spanning_manager = TapeSpanningManager(tape_controller, socketio=socketio)

@app.route('/api/spanning/<int:job_id>/status')
def get_spanning_status(job_id):
    """Get spanning session status for a job."""
    try:
        status = spanning_manager.get_session_status(job_id)
        
        if not status:
            return _make_error_response("not_found", "No spanning session for job", 404)
        
        return jsonify({'success': True, 'status': status})
    except Exception as e:
        app.logger.exception("Failed to get spanning status")
        return _make_error_response("spanning_error", str(e), 500)

@app.route('/api/spanning/<int:job_id>/request-change', methods=['POST'])
def request_tape_change(job_id):
    """Request a tape change (called by tar new-volume-script)."""
    try:
        payload = request.get_json(silent=True) or {}
        reason = payload.get('reason', 'Tape full')
        
        success = spanning_manager.request_tape_change(job_id, reason)
        
        if success:
            return jsonify({'success': True})
        else:
            return _make_error_response("spanning_error", "Failed to request tape change", 400)
    except Exception as e:
        app.logger.exception("Failed to request tape change")
        return _make_error_response("spanning_error", str(e), 500)

@app.route('/api/spanning/<int:job_id>/provide-tape', methods=['POST'])
def provide_next_tape(job_id):
    """Provide next tape for spanning operation."""
    try:
        payload = request.get_json(silent=True) or {}
        barcode = payload.get('barcode')
        
        if not barcode:
            return _make_error_response("invalid_request", "Tape barcode required", 400)
        
        success = spanning_manager.provide_next_tape(job_id, barcode)
        
        if success:
            log_info(f"Tape change for job {job_id}: loaded {barcode}", 'system')
            return jsonify({'success': True})
        else:
            return _make_error_response("spanning_error", "Failed to change tape", 400)
    except Exception as e:
        app.logger.exception("Failed to provide tape")
        return _make_error_response("spanning_error", str(e), 500)


# =============================================================================
# Job Checkpoint/Resume API
# =============================================================================

@app.route('/api/jobs/interrupted')
def get_interrupted_jobs():
    """Get jobs that can be resumed"""
    try:
        jobs = checkpoint_manager.get_interrupted_jobs()
        for job in jobs:
            can_resume, reason = checkpoint_manager.can_resume(job['id'])
            job['can_resume'] = can_resume
            job['resume_reason'] = reason
            checkpoint = checkpoint_manager.get_checkpoint(job['id'])
            if checkpoint:
                job['checkpoint'] = {
                    'files_completed': checkpoint.get('files_completed', 0),
                    'bytes_written': checkpoint.get('bytes_written', 0),
                    'timestamp': checkpoint.get('timestamp')
                }
        return jsonify({'success': True, 'jobs': jobs})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/jobs/<int:job_id>/checkpoint')
@require_role('viewer')
def get_job_checkpoint(job_id):
    """Get checkpoint for a specific job"""
    try:
        checkpoint = checkpoint_manager.get_checkpoint(job_id)
        if not checkpoint:
            return jsonify({'success': False, 'error': 'No checkpoint found'}), 404
        return jsonify({'success': True, 'checkpoint': checkpoint})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/jobs/<int:job_id>/resume', methods=['POST'])
@require_role('operator')
def resume_interrupted_job(job_id):
    """Resume a paused or interrupted job"""
    try:
        job = db.get_job(job_id)
        if not job:
            return jsonify({'success': False, 'error': 'Job not found'}), 404

        if job.get('status') == 'paused':
            log_info(f"Resuming paused job #{job_id}", 'job')
            threading.Thread(target=backup_engine.resume_job, args=(job_id,), daemon=True).start()
            return jsonify({'success': True, 'message': 'Job resuming'})

        can_resume, reason = checkpoint_manager.can_resume(job_id)
        if not can_resume:
            return jsonify({'success': False, 'error': reason}), 400

        checkpoint = checkpoint_manager.get_checkpoint(job_id)
        log_info(f"Resuming job #{job_id} from checkpoint", 'job')

        # Update job status
        db.update_job_status(job_id, 'running', 'Resuming from checkpoint')

        # Start resume in background
        def do_resume():
            try:
                backup_engine.resume_from_checkpoint(job_id, checkpoint)
            except Exception as e:
                log_error(f"Resume failed for job #{job_id}: {e}", 'job')
                db.update_job_status(job_id, 'error', str(e))

        threading.Thread(target=do_resume, daemon=True).start()
        return jsonify({'success': True, 'message': 'Job resuming'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# =============================================================================
# Configuration Export/Import API
# =============================================================================

@app.route('/api/config/export')
@require_admin
def export_config():
    """Export all configuration as JSON"""
    try:
        def _redact_sensitive(payload: Dict) -> Dict:
            if not isinstance(payload, dict):
                return {}
            redacted = {}
            for key, value in payload.items():
                lowered = str(key).lower()
                if any(token in lowered for token in ('password', 'secret', 'api_key', 'apikey', 'token')):
                    continue
                redacted[key] = value
            return redacted

        runtime_config = {}
        runtime_config_error = None
        try:
            runtime_config = _redact_sensitive(load_config())
        except PermissionError as exc:
            runtime_config_error = str(exc)

        runtime_state = load_state()
        settings = db.get_settings() if db else {}
        schedules = db.get_schedules() if db else []
        tape_aliases = db.get_tape_aliases() if db else {}
        maintenance_windows = db.get_maintenance_windows() if db else []
        credentials_metadata = []
        if db:
            for credential in db.list_credentials():
                credentials_metadata.append({
                    'name': credential.get('name') or '',
                    'display_name': credential.get('display_name') or '',
                    'type': credential.get('type') or '',
                    'domain': credential.get('domain') or '',
                    'smb_path': credential.get('smb_path') or '',
                    'has_password': bool(credential.get('password_encrypted')),
                    'created_at': credential.get('created_at'),
                    'updated_at': credential.get('updated_at'),
                })

        hardware_available = hardware_availability.get("hardware_available", False)
        hardware_reason = hardware_availability.get("hardware_reason") or "Tape hardware unavailable"
        build_info = {
            'version': os.environ.get('FOSSILSAFE_VERSION') or 'unknown',
        }
        if os.environ.get('FOSSILSAFE_BUILD'):
            build_info['build'] = os.environ.get('FOSSILSAFE_BUILD')
        if os.environ.get('FOSSILSAFE_BUILD_SHA'):
            build_info['build_sha'] = os.environ.get('FOSSILSAFE_BUILD_SHA')

        autopilot_settings = {
            'available': False,
        }
        if autopilot is not None:
            autopilot_settings = {
                'available': True,
                'auto_resolve_enabled': autopilot.auto_resolve_enabled,
                'destructive_automation_enabled': autopilot.destructive_automation_enabled
            }

        config = {
            'version': '1.0',
            'exported_at': now_utc_iso(),
            'build': build_info,
            'config_path': get_config_path(),
            'state_path': get_state_path(),
            'runtime_config': runtime_config,
            'runtime_config_error': runtime_config_error,
            'runtime_state': runtime_state,
            'settings': _redact_sensitive(settings),
            'schedules': schedules,
            'tape_aliases': tape_aliases,
            'maintenance_windows': maintenance_windows,
            'credentials_metadata': credentials_metadata,
            'hardware': {
                'available': hardware_available,
                'reason': None if hardware_available else hardware_reason,
            },
            'autopilot_settings': autopilot_settings,
        }
        
        # Create temp file
        filename = f'fossilsafe_config_{datetime.now().strftime("%Y%m%d")}.json'
        export_path = tempfile.NamedTemporaryFile(delete=False, suffix='.json').name
        with open(export_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        log_info("Configuration exported", 'system')
        return send_file(
            export_path,
            as_attachment=True,
            download_name=filename,
            mimetype='application/json',
        )
    except Exception as e:
        return _make_error_response(
            "config_export_failed",
            "Configuration export failed",
            500,
            str(e),
        )

@app.route('/api/config/import', methods=['POST'])
@require_admin
def import_config():
    """Import configuration from JSON"""
    try:
        if 'file' in request.files:
            file = request.files['file']
            config = json.load(file)
        else:
            config = request.json
        
        if not config or 'version' not in config:
            return jsonify({'success': False, 'error': 'Invalid config file'}), 400
        
        imported = []
        
        # Import settings
        if 'settings' in config:
            for key, value in config['settings'].items():
                db.set_setting(key, value)
            imported.append('settings')
        
        # Import schedules
        if 'schedules' in config:
            for schedule in config['schedules']:
                try:
                    db.add_schedule(schedule)
                except (sqlite3.IntegrityError, ValueError, KeyError):
                    pass  # Skip duplicates or invalid schedules
            imported.append('schedules')

        # Import tape aliases
        if 'tape_aliases' in config:
            for barcode, alias in config['tape_aliases'].items():
                try:
                    db.update_tape_alias(barcode, alias)
                except (sqlite3.Error, ValueError):
                    pass  # Skip invalid aliases
            imported.append('tape_aliases')
        
        # Import maintenance windows
        if 'maintenance_windows' in config:
            for window in config['maintenance_windows']:
                try:
                    db.add_maintenance_window(
                        window['name'],
                        window['start_time'],
                        window['end_time'],
                        window.get('recurring', False),
                        window.get('days', [])
                    )
                except (sqlite3.IntegrityError, ValueError, KeyError):
                    pass  # Skip duplicates or invalid windows
            imported.append('maintenance_windows')
        
        # Import autopilot settings
        if 'autopilot_settings' in config:
            ap_settings = config['autopilot_settings']
            if 'auto_resolve_enabled' in ap_settings:
                autopilot.set_auto_resolve(ap_settings['auto_resolve_enabled'])
            imported.append('autopilot_settings')
        
        log_info(f"Configuration imported: {', '.join(imported)}", 'system')
        return jsonify({'success': True, 'imported': imported})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# =============================================================================
# Tape Capacity API
# =============================================================================


# WebSocket events
def handle_connect(auth=None):
    if _is_socketio_authorized(auth):
        _register_socketio_authorized_sid()
        emit('connected', {'message': 'Connected to LTO Backup System'})
        update_system_state()
        emit('state_update', system_state)
        return True
    log_info("Socket.IO connect without valid auth", 'auth')
    disconnect()
    return False


def handle_update_request():
    if not _is_authorized_socketio_sid():
        disconnect()
        return
    update_system_state()
    emit('state_update', system_state)


def handle_auth_ping(auth_payload=None):
    if _is_socketio_authorized(auth_payload):
        _register_socketio_authorized_sid()
        return {'ok': True}
    log_info("Socket.IO auth_ping rejected", 'auth')
    disconnect()
    return {'ok': False}


def handle_disconnect():
    if request.sid:
        _authorized_socketio_sids.discard(request.sid)

# =============================================================================
# Setup Wizard API
# =============================================================================

@app.route('/api/setup/status')
def get_setup_status():
    """Check if initial setup has been completed"""
    try:
        setup_complete = db.get_setting('setup_complete', False)
        library_info = {}

        try:
            library_info = tape_controller.get_library_info()
        except Exception:
            pass

        return jsonify({
            'success': True,
            'setup_complete': setup_complete,
            'library_info': library_info
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/setup/complete', methods=['POST'])
def complete_setup():
    """Mark setup as complete"""
    try:
        db.set_setting('setup_complete', True)
        db.set_setting('setup_completed_at', now_utc_iso())
        log_success("Initial setup completed", 'system')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/setup/scan-hardware', methods=['POST'])
def scan_hardware():
    """Scan for tape drives and changers/libraries using lsscsi and sg_inq"""
    try:
        drives = []
        changers = []
        discovered_drives, discovered_changers = discover_devices()
        for drive in discovered_drives:
            model = drive.model or ''
            drives.append({
                'path': drive.path,
                'sg_path': drive.sg_path,
                'vendor': drive.vendor,
                'model': model,
                'generation': _detect_lto_generation(model)
            })

        for changer in discovered_changers:
            sg_path = changer.path
            slots = None
            drive_count = None
            try:
                mtx_result = subprocess.run(['mtx', '-f', sg_path, 'status'],
                                            capture_output=True, text=True, timeout=10)
                if mtx_result.returncode == 0:
                    for mtx_line in mtx_result.stdout.split('\n'):
                        if 'Storage Element' in mtx_line:
                            slots = 1 if slots is None else slots + 1
                        if 'Data Transfer Element' in mtx_line:
                            drive_count = 1 if drive_count is None else drive_count + 1
            except Exception:
                pass

            changers.append({
                'path': sg_path,
                'vendor': changer.vendor,
                'model': changer.model,
                'slots': slots,
                'drives': drive_count
            })
        
        log_info(f"Hardware scan: {len(drives)} drives, {len(changers)} changers", 'setup')
        
        return jsonify({
            'success': True,
            'drives': drives,
            'changers': changers
        })
        
    except Exception as e:
        log_error(f"Hardware scan failed: {e}", 'setup')
        return jsonify({'success': False, 'error': str(e)}), 500

def _detect_lto_generation(model_string):
    """Detect LTO generation from model string"""
    model_upper = (model_string or '').upper()
    if 'LTO-9' in model_upper or 'LTO9' in model_upper:
        return 'LTO-9'
    elif 'LTO-8' in model_upper or 'LTO8' in model_upper:
        return 'LTO-8'
    elif 'LTO-7' in model_upper or 'LTO7' in model_upper:
        return 'LTO-7'
    elif 'LTO-6' in model_upper or 'LTO6' in model_upper:
        return 'LTO-6'
    elif 'LTO-5' in model_upper or 'LTO5' in model_upper:
        return 'LTO-5'
    elif 'ULTRIUM' in model_upper:
        # Try to extract generation from Ultrium number
        import re
        match = re.search(r'ULTRIUM[- ]?(\d)', model_upper)
        if match:
            gen = int(match.group(1))
            return f'LTO-{gen}'
    return 'LTO'


def _validate_device_path(path: str, pattern: str, description: str):
    if not path or not isinstance(path, str):
        return False, f"{description} is required"
    if not os.path.isabs(path) or not path.startswith('/dev/'):
        return False, f"{description} must be a /dev path"
    if not re.match(pattern, path):
        return False, f"{description} must match {pattern}"
    if not os.path.exists(path):
        return False, f"{description} does not exist: {path}"
    try:
        mode = os.stat(path).st_mode
        if not stat.S_ISCHR(mode):
            return False, f"{description} is not a character device: {path}"
    except Exception as e:
        return False, f"{description} could not be validated: {e}"
    return True, None


def _parse_confirmation_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(',') if item.strip()]
    return []


def _tape_in_active_job(barcode: str) -> Optional[str]:
    active_jobs = db.get_active_jobs()
    for job in active_jobs:
        tapes = job.get('tapes', [])
        if isinstance(tapes, str):
            try:
                tapes = json.loads(tapes)
            except Exception:
                tapes = []
        if barcode in tapes or job.get('current_tape') == barcode:
            return job.get('name') or f"job {job.get('id')}"
    return None


def _get_tape_feature_flags() -> Dict[str, object]:
    tape_settings = (load_config().get("tape", {}) or {})
    return {
        "quick_erase_supported": bool(tape_settings.get("quick_erase_supported", True)),
        "quick_erase_reason": tape_settings.get(
            "quick_erase_reason",
            "Quick erase is disabled for this library configuration.",
        ),
        "quick_erase_command": tape_settings.get("quick_erase_command") or ["erase", "short"],
        "erase_status_poll_seconds": int(tape_settings.get("erase_status_poll_seconds", 20)),
        "erase_status_patterns": tape_settings.get("erase_status_patterns")
        or ["erase", "erasing", "erase in progress"],
    }




def _reconcile_library_state(reason: str, expected_barcode: Optional[str] = None, drive: int = 0) -> None:
    """Reconcile library state after interrupted operations without assuming unload succeeded."""
    if not tape_controller or not db:
        return
    try:
        inventory = tape_controller.scan_barcodes()
        db.update_tape_inventory(inventory)
        active_jobs = db.get_active_jobs()
        reconciled = _apply_active_tape_job_state(inventory, active_jobs)
        db.update_tape_inventory(reconciled)
        drive_status = None
        if not tape_controller.is_drive_only():
            drive_status = tape_controller.get_drive_status()
        log_info(
            f"Library reconciliation complete ({reason})",
            "tape",
            json.dumps({
                "expected_barcode": expected_barcode,
                "drive": drive,
                "drive_status": drive_status,
            }, default=str),
        )
    except Exception as exc:
        log_warning(f"Library reconciliation failed ({reason}): {exc}", "tape")


def _acquire_drive_lock(
    drive: int,
    purpose: str,
    allow_recovery_unload: bool = False,
    dest_slot: Optional[int] = None,
):
    if backup_engine is None:
        return None, f"Drive locks unavailable for {purpose}"
    recovery_state = _get_recovery_state()
    if recovery_state:
        if not allow_recovery_unload:
            return None, (
                "Recovery required before running operations. "
                "Unload the tape to a chosen slot to clear recovery."
            )
        if dest_slot is None:
            return None, (
                "Recovery required: specify dest_slot to unload the tape and clear recovery."
            )
    drive_lock = backup_engine.drive_locks[drive]
    if not drive_lock.acquire(blocking=False):
        return None, f"Drive {drive} is busy"
    return drive_lock, None

@app.route('/api/setup/library-settings', methods=['POST'])
def save_library_settings():
    """Save library configuration settings"""
    try:
        data = request.json
        
        if 'library_name' in data:
            db.set_setting('library_name', data['library_name'])
        
        if 'device_path' in data:
            device_path = data['device_path']
            valid, error = _validate_device_path(
                device_path,
                r'^/dev/(nst|st)\d+$',
                "Tape device path"
            )
            if not valid:
                return jsonify({'success': False, 'error': error}), 400
            db.set_setting('device_path', device_path)
            tape_controller.device = device_path
        
        if 'changer_path' in data:
            changer_path = data['changer_path']
            if changer_path in (None, ''):
                db.set_setting('changer_path', '')
                tape_controller.changer = None
            else:
                valid, error = _validate_device_path(
                    changer_path,
                    r'^/dev/sg\d+$',
                    "Changer device path"
                )
                if not valid:
                    return jsonify({'success': False, 'error': error}), 400
                if not is_medium_changer_device(changer_path):
                    _drives, changers = discover_devices()
                    detected = [changer.path for changer in changers if changer.path]
                    hint = f" Detected changers: {detected}" if detected else ""
                    return jsonify({
                        'success': False,
                        'error': (
                            f"Changer device path {changer_path} is not a medium changer (SCSI type 8)."
                            f"{hint}"
                        )
                    }), 400
                db.set_setting('changer_path', changer_path)
                tape_controller.changer = changer_path
        
        log_info(f"Library settings updated: {data.get('library_name', 'unnamed')}", 'system')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/setup/initialize-tape/<barcode>', methods=['POST'])
def initialize_single_tape(barcode):
    """Initialize a single tape with LTFS format"""
    try:
        data = request.json or {}
        format_type = data.get('format', 'ltfs')
        confirmation = data.get('confirmation', '')
        drive = int(data.get('drive', 0) or 0)
        run_async = bool(data.get('async', False))

        _log_request_event(
            "Tape initialization requested",
            "setup",
            {
                "barcode": barcode,
                "format": format_type,
                "drive": drive,
                "async": run_async,
                "drive_device": tape_controller.device,
                "drive_sg": tape_controller.drive_sg,
                "changer": tape_controller.changer,
            },
        )
        
        valid, error = validate_tape_identifier(barcode)
        if not valid:
            return jsonify({'success': False, 'error': error}), 400

        if confirmation != barcode:
            return jsonify({
                'success': False,
                'error': f'Confirmation required. Type "{barcode}" to confirm initialization.'
            }), 400

        active_job = _tape_in_active_job(barcode)
        if active_job:
            return jsonify({
                'success': False,
                'error': f'Tape is currently in use by {active_job}'
            }), 400

        drive_lock, lock_error = _acquire_drive_lock(drive, "tape initialization")
        if not drive_lock:
            return jsonify({'success': False, 'error': lock_error}), 409
        
        log_info(f"Initializing tape {barcode} with {format_type}", 'setup')

        if run_async:
            try:
                job_id = _create_internal_job(
                    f"Initialize tape {barcode}",
                    "tape_initialize",
                    [barcode],
                    drive=drive,
                    total_files=1,
                )
            except Exception:
                drive_lock.release()
                raise

            def _do_initialize():
                try:
                    if _is_internal_job_cancelled(job_id):
                        _mark_internal_job_cancelled(job_id, f"Initialization for {barcode} cancelled before start")
                        return
                    _update_job_with_log(job_id, "running", f"Initializing tape {barcode}")
                    _set_job_progress(job_id, "formatting", f"Formatting tape {barcode}")
                    _add_job_log(job_id, "info", f"Initializing tape {barcode} on drive {drive}")
                    tape_controller.load_tape(barcode, drive)
                    tape_controller.format_tape(barcode, drive=drive)
                    _refresh_tape_ltfs_metadata(barcode, drive)
                    db.update_tape_status(barcode, 'available')
                    db.update_tape_usage(barcode, 0)
                    db.update_job_progress(job_id, files_written=1)
                    _set_job_progress(job_id, "completed", f"Tape {barcode} initialized", level="success")
                    _update_job_with_log(job_id, "completed", f"Tape {barcode} initialized", "success")
                    log_success(f"Tape {barcode} initialized successfully", 'setup')
                except Exception as exc:
                    _update_job_with_log(job_id, "error", f"Failed to initialize tape {barcode}: {exc}", "error")
                    log_error(f"Failed to initialize tape {barcode}: {exc}", 'setup')
                finally:
                    try:
                        tape_controller.unload_tape(drive)
                    except Exception:
                        pass
                    _reconcile_library_state("initialize cleanup", expected_barcode=barcode, drive=drive)
                    _internal_job_cancel_flags.pop(job_id, None)
                    drive_lock.release()

            threading.Thread(target=_do_initialize, daemon=True).start()
            return jsonify({'success': True, 'job_id': job_id}), 202

        try:
            tape_controller.load_tape(barcode, drive)
            tape_controller.format_tape(barcode, drive=drive)
            _refresh_tape_ltfs_metadata(barcode, drive)
            db.update_tape_status(barcode, 'available')
            db.update_tape_usage(barcode, 0)
        finally:
            try:
                tape_controller.unload_tape(drive)
            except Exception:
                pass
            drive_lock.release()
        
        log_success(f"Tape {barcode} initialized successfully", 'setup')
        return jsonify({'success': True, 'barcode': barcode})
        
    except Exception as e:
        log_error(f"Failed to initialize tape {barcode}: {e}", 'setup')
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/setup/initialize-all', methods=['POST'])
def initialize_all_tapes():
    """Initialize all selected tapes"""
    try:
        data = request.json or {}
        tapes = data.get('tapes', [])
        format_type = data.get('format', 'ltfs')
        confirmation = data.get('confirmation')
        drive = int(data.get('drive', 0) or 0)
        
        if not tapes:
            return jsonify({'success': False, 'error': 'No tapes specified'}), 400

        confirmation_list = _parse_confirmation_list(confirmation)
        if confirmation_list != tapes:
            return jsonify({
                'success': False,
                'error': 'Confirmation required. Provide a confirmation list matching the tapes.'
            }), 400

        for barcode in tapes:
            valid, error = validate_tape_identifier(barcode)
            if not valid:
                return jsonify({'success': False, 'error': error}), 400
            active_job = _tape_in_active_job(barcode)
            if active_job:
                return jsonify({
                    'success': False,
                    'error': f'Tape {barcode} is currently in use by {active_job}'
                }), 400

        drive_lock, lock_error = _acquire_drive_lock(drive, "bulk tape initialization")
        if not drive_lock:
            return jsonify({'success': False, 'error': lock_error}), 409
        
        results = []
        try:
            for barcode in tapes:
                try:
                    tape_controller.load_tape(barcode, drive)
                    tape_controller.format_tape(barcode, drive=drive)
                    _refresh_tape_ltfs_metadata(barcode, drive)
                    db.update_tape_status(barcode, 'available')
                    db.update_tape_usage(barcode, 0)
                    results.append({'barcode': barcode, 'success': True})
                    log_success(f"Initialized tape {barcode}", 'setup')
                except Exception as e:
                    results.append({'barcode': barcode, 'success': False, 'error': str(e)})
                    log_error(f"Failed to initialize tape {barcode}: {e}", 'setup')
                finally:
                    try:
                        tape_controller.unload_tape(drive)
                    except Exception:
                        pass
        finally:
            drive_lock.release()
        
        successful = sum(1 for r in results if r['success'])
        return jsonify({
            'success': True,
            'results': results,
            'summary': f'{successful}/{len(tapes)} tapes initialized'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# =============================================================================
# Application Initialization Functions
# =============================================================================

def _init_db(db_path: str) -> None:
    global db, db_unavailable_reason, checkpoint_manager, log_manager
    try:
        db_path = _ensure_db_path(db_path, "app init")
        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)
        db_unavailable_reason = None
        if checkpoint_manager is None:
            checkpoint_manager = CheckpointManager(db)
        if log_manager is None:
            log_manager = LogManager(db, socket_emit_func=_emit_socketio_event)
    except Exception as exc:
        db = None
        db_unavailable_reason = f"{exc}"
        checkpoint_manager = None
        log_manager = None
        app.logger.exception("Database initialization failed (db_path=%s)", db_path)


def _init_source_manager() -> None:
    global source_manager, source_manager_unavailable_reason
    if db is None:
        source_manager = None
        source_manager_unavailable_reason = db_unavailable_reason or "Database not initialized"
        return

    errors = []
    _validate_credential_key_dirs(errors)
    if errors:
        source_manager = None
        source_manager_unavailable_reason = errors[0]
        return

    try:
        source_manager = SourceManager(db)
        source_manager_unavailable_reason = None
    except Exception as exc:
        source_manager = None
        source_manager_unavailable_reason = f"{exc}"
        app.logger.exception(
            "Source manager initialization failed (key_path=%s)",
            get_credential_key_path(),
        )


def initialize_app(config=None):
    """
    Initialize application components (database, credentials).
    Safe to call multiple times - idempotent.
    
    Args:
        config: Optional configuration dict
        
    Returns:
        Flask app instance
    """
    global db, db_unavailable_reason, source_manager, source_manager_unavailable_reason
    global checkpoint_manager, log_manager, metrics_service, csrf, api_key_rate_limiter, library_manager
    
    # Initialize CSRF protection (if not already initialized)
    if csrf is None:
        if not app.config.get('SECRET_KEY'):
            app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', os.urandom(32).hex())
        app.config['WTF_CSRF_TIME_LIMIT'] = None  # Consistent with audit recommendation
        
        # Use ProxyFix to handle X-Forwarded-For correctly behind Nginx
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
        
        # Disable SSL strictness for dev/test to allow HTTP
        app.config['WTF_CSRF_SSL_STRICT'] = True
        if os.environ.get("FOSSILSAFE_DISABLE_CSRF") == "1" or app.debug:
            app.config['WTF_CSRF_ENABLED'] = False
        csrf = CSRFProtect(app)
        
        # Exempt specific endpoints from CSRF
        csrf.exempt('backend.routes.system.get_healthz')
        csrf.exempt('backend.routes.auth.get_csrf_token')
        csrf.exempt(auth_bp)        # Auth endpoints use Bearer tokens, not CSRF cookies
        csrf.exempt(tapes_bp)
        csrf.exempt(diagnostics_bp)
        csrf.exempt(get_spanning_status)
        csrf.exempt(request_tape_change)
        csrf.exempt(provide_next_tape)

    # Initialize rate limiter for API key validation (if not already initialized)
    if api_key_rate_limiter is None:
        api_key_rate_limiter = RateLimiter()
        api_key_rate_limiter.max_attempts = 5
        api_key_rate_limiter.window_seconds = 300  # 5 minutes
        api_key_rate_limiter.lockout_duration = 900  # 15 minutes
    
    # Apply configuration
    file_config = load_config()
    state_config = load_state()
    if config:
        app.config.update(config)
    if 'DB_PATH' not in app.config:
        env_db_path = os.environ.get('FOSSILSAFE_DB_PATH')
        if state_config.get('DB_PATH'):
            app.config['DB_PATH'] = state_config['DB_PATH']
        elif file_config.get('db_path') or file_config.get('DB_PATH'):
            app.config['DB_PATH'] = file_config.get('db_path') or file_config.get('DB_PATH')
        elif env_db_path:
            app.config['DB_PATH'] = os.path.abspath(os.path.expanduser(env_db_path))
        else:
            app.config['DB_PATH'] = get_default_db_path()
    
    # Initialize database (if not already initialized)
    if db is None:
        db_path = app.config.get('DB_PATH', get_default_db_path())
        app.config['DB_PATH'] = db_path
        app.config['CONFIG_PATH'] = get_config_path()
        app.config['STATE_PATH'] = get_state_path()
        _init_db(db_path)

    # Initialize source manager (if not already initialized)
    if source_manager is None:
        _init_source_manager()
    elif source_manager_unavailable_reason is not None:
        source_manager_unavailable_reason = None

    if db is not None:
        if db_unavailable_reason is not None:
            db_unavailable_reason = None
        if checkpoint_manager is None:
            checkpoint_manager = CheckpointManager(db)
        if log_manager is None:
            log_manager = LogManager(db, socket_emit_func=_emit_socketio_event)
        
        # Initialize authentication
        init_auth(db)
        
        if metrics_service is None:
            metrics_service = MetricsService(db)
    else:
        checkpoint_manager = None
        log_manager = None
        metrics_service = None

    if "smb_selftest" not in app.blueprints:
        selftest_deps = SmbSelfTestDependencies(
            get_smb_client=lambda: smb_client,
            initialize_smb_client=initialize_smb_client,
            get_smb_unavailable_reason=lambda: smb_unavailable_reason,
            validate_smb_path=validate_smb_path,
            log_info=log_info,
            log_error=log_error,
            log_request_event=_log_request_event,
        )
        app.register_blueprint(create_smb_selftest_blueprint(selftest_deps))
        app.register_blueprint(auth_bp)
        app.register_blueprint(setup_bp)
        app.register_blueprint(logs_bp)
        app.register_blueprint(system_bp)
        app.register_blueprint(tapes_bp)
        app.register_blueprint(jobs_bp)
        app.register_blueprint(files_bp)
        app.register_blueprint(restore_bp)
        app.register_blueprint(recovery_bp)
        app.register_blueprint(external_catalog_bp)
        app.register_blueprint(audit_bp)
        app.register_blueprint(diagnostics_bp)
        app.register_blueprint(verification_bp, url_prefix='/api/verification')
        app.register_blueprint(preferences_bp, url_prefix='/api/preferences')
        from backend.routes.sources import sources_bp
        from backend.routes.kms import kms_bp
        from backend.routes.webhooks import webhooks_bp
        app.register_blueprint(sources_bp)
        app.register_blueprint(kms_bp)
        app.register_blueprint(uploads_bp)
        app.register_blueprint(webhooks_bp)
        app.register_blueprint(backup_sets_bp)
    
    # Initialize Webhook Service
    webhook_service = init_webhook_service(db)
    app.config['webhook_service'] = webhook_service
    
    # Store in app context for route access
    app.db = db
    app.db_unavailable_reason = db_unavailable_reason
    app.source_manager = source_manager
    app.source_manager_unavailable_reason = source_manager_unavailable_reason
    app.checkpoint_manager = checkpoint_manager
    app.log_manager = log_manager
    app.metrics_service = metrics_service
    app.smb_client = smb_client
    app.smb_unavailable_reason = smb_unavailable_reason
    app.api_key_rate_limiter = api_key_rate_limiter
    app.csrf = csrf
    
    # Core services (may be None if not autostarted, but allow mocking)
    app.tape_controller = tape_controller
    app.library_manager = library_manager
    app.backup_engine = backup_engine
    app.advanced_restore_engine = advanced_restore_engine
    app.duplication_engine = duplication_engine
    app.streaming_pipeline = streaming_pipeline
    app.scheduler = scheduler
    app.autopilot = autopilot
    app.preflight_checker = preflight_checker
    app.tape_service = tape_service
    app.job_service = job_service
    app.file_service = file_service
    app.restore_service = restore_service
    app.diagnostic_service = diagnostic_service
    if 'verification_service' in globals():
        app.verification_service = globals().get('verification_service')
    
    # Expose helper functions on app
    app.db_available = _db_available
    app.source_manager_available = _source_manager_available
    app.hardware_status_payload = _hardware_status_payload
    app.update_system_state = update_system_state
    app.log_info = log_info
    app.log_warning = log_warning
    app.log_error = log_error
    app.log_request_event = _log_request_event
    app.now_utc_iso = now_utc_iso
    
    # Expose state
    app.system_state = system_state
    app.hardware_init_status = hardware_init_status
    app.hardware_availability = hardware_availability

    return app


def create_app(config=None, autostart_services: Optional[bool] = None):
    """
    Create and configure the Flask app without starting background services.

    Args:
        config: Optional configuration dict
        autostart_services: Optional override for background service autostart.

    Returns:
        Flask app instance
    """
    initialized_app = initialize_app(config)
    _setup_request_tracing(initialized_app)
    if autostart_services is None:
        autostart = os.environ.get('FOSSILSAFE_AUTOSTART_SERVICES', 'true').lower() not in ('0', 'false', 'no')
    else:
        autostart = autostart_services
    if autostart:
        create_socketio()
        controllers = initialize_controllers()
        if controllers.get("hardware_available"):
            if scheduler and not scheduler.is_running():
                scheduler.start()
            if autopilot and not autopilot.running:
                autopilot.start()
    return initialized_app


def register_socketio_handlers(socketio_instance: SocketIO):
    """Register Socket.IO event handlers for the provided SocketIO instance."""
    socketio_instance.on_event('connect', handle_connect)
    socketio_instance.on_event('disconnect', handle_disconnect)
    socketio_instance.on_event('request_update', handle_update_request)
    socketio_instance.on_event('auth_ping', handle_auth_ping)


def initialize_socketio():
    """
    Initialize SocketIO instance.
    
    Returns:
        SocketIO instance
    """
    global socketio, _socketio_handlers_registered
    
    if socketio is None:
        socketio = SocketIO(app, cors_allowed_origins=_get_cors_origins(), async_mode='threading')
        app.socketio = socketio

    if not _socketio_handlers_registered:
        register_socketio_handlers(socketio)
        _socketio_handlers_registered = True
    
    return socketio


def create_socketio(app_instance=None):
    """
    Create and configure SocketIO without starting background services.

    Args:
        app_instance: Optional Flask app to bind to (defaults to global app)

    Returns:
        SocketIO instance
    """
    if app_instance is not None and app_instance is not app:
        raise ValueError("FossilSafe backend currently supports a single Flask app instance")
    return initialize_socketio()


def _initialize_dependent_services(tape_ctrl):
    """Initialize services that depend on tape controller, even if None."""
    global tape_controller, backup_engine, advanced_restore_engine, duplication_engine, streaming_pipeline, scheduler, autopilot, preflight_checker, tape_service, job_service, file_service, restore_service, diagnostic_service, health_service, verification_service, library_manager, webhook_service, spanning_manager
    
    
    tape_controller = tape_ctrl
    if spanning_manager:
        spanning_manager.tape_controller = tape_ctrl
    
    duplication_engine = TapeDuplicationEngine(db, tape_controller, smb_client, socketio, library_manager=library_manager)
    streaming_pipeline = StreamingBackupPipeline(
        db,
        tape_controller,
        smb_client,
        socketio,
        get_streaming_config(db),
        library_manager=library_manager
    )
    backup_engine = BackupEngine(db, tape_controller, smb_client, socketio, source_manager, library_manager=library_manager, webhook_service=webhook_service, spanning_manager=spanning_manager)
    advanced_restore_engine = AdvancedRestoreEngine(db, tape_controller, socketio, duplication_engine=duplication_engine, library_manager=library_manager)
    
    # Import and initialize VerificationService
    from backend.services.verification_service import VerificationService
    verification_service = VerificationService(db, tape_controller, socketio, library_manager=library_manager)
    
    scheduler = BackupScheduler(db, backup_engine, verification_service)
    autopilot = AutopilotEngine(db, tape_controller, smb_client, backup_engine, source_manager, library_manager=library_manager)
    preflight_checker = PreflightChecker(tape_controller, smb_client, db, source_manager=source_manager, library_manager=library_manager)
    tape_service = TapeService(db, tape_controller, library_manager=library_manager)
    job_service = JobService(db, backup_engine, scheduler, preflight_checker, tape_controller, source_manager, smb_client=smb_client)
    
    # Cleanup orphaned jobs from previous crashes/restarts
    try:
        job_service.cleanup_orphaned_jobs()
    except Exception:
        pass
    file_service = FileService(db)
    restore_service = RestoreService(db, backup_engine, advanced_restore_engine, tape_controller)
    diagnostic_service = DiagnosticService(db, tape_controller, log_manager, get_devices, library_manager=library_manager)
    health_service = HealthService(db)

    # Store in app context
    app.tape_controller = tape_controller
    app.library_manager = library_manager
    app.backup_engine = backup_engine
    app.advanced_restore_engine = advanced_restore_engine
    app.duplication_engine = duplication_engine
    app.streaming_pipeline = streaming_pipeline
    app.scheduler = scheduler
    app.autopilot = autopilot
    app.preflight_checker = preflight_checker
    app.tape_service = tape_service
    app.job_service = job_service
    app.file_service = file_service
    app.restore_service = restore_service
    app.diagnostic_service = diagnostic_service
    app.health_service = health_service
    app.verification_service = verification_service
    
    # Also store in config for blueprint access
    app.config['db'] = db
    app.config['verification_service'] = verification_service
    
    # Expose more helpers that depend on controllers
    app.get_devices = get_devices


def initialize_tape_controller() -> Dict[str, Optional[str]]:
    """
    Initialize tape controller, backup engine, scheduler, and autopilot.
    Call this ONLY when ready to start services.

    Requires: initialize_app() and initialize_socketio() must be called first

    Returns:
        Dictionary describing tape hardware availability
    """
    global tape_controller, library_manager, backup_engine, advanced_restore_engine, duplication_engine, streaming_pipeline, scheduler, autopilot, preflight_checker, autodetect_no_hardware_logged

    # Ensure dependencies are initialized
    if db is None:
        raise RuntimeError("Must call initialize_app() before initialize_tape_controller()")
    if socketio is None:
        raise RuntimeError("Must call initialize_socketio() before initialize_tape_controller()")

    if smb_client is None:
        reason = smb_unavailable_reason or "SMB client not initialized"
        log_warning(reason, "smb")
        _set_hardware_init_status(False, reason)
        _clear_tape_controllers()
        _initialize_dependent_services(None) # Ensure services available even if SMB fails
        return {"hardware_available": False, "hardware_reason": reason}

    # Get device settings
    file_config = load_config()
    state_config = load_state()
    tape_config = _get_tape_config(file_config)
    device_path = _normalize_device_setting(db.get_setting('device_path', None))
    changer_path = _normalize_device_setting(db.get_setting('changer_path', None))
    drive_count = int(db.get_setting('drive_count', 1))
    device_paths_setting = db.get_setting('drive_devices', None)
    if isinstance(device_paths_setting, list):
        device_paths_setting = [str(path).strip() for path in device_paths_setting if str(path).strip()]

    if tape_config.get('changer_device'):
        changer_path = tape_config['changer_device']
    if tape_config.get('drive_devices'):
        drive_devices = tape_config['drive_devices']
    elif tape_config.get('drive_device'):
        drive_devices = [tape_config['drive_device']]
    elif isinstance(device_paths_setting, list) and device_paths_setting:
        drive_devices = device_paths_setting
    else:
        drive_devices = []
        if device_path:
            match = re.match(r'^(.*?)(\d+)$', device_path)
            if match:
                base, _ = match.groups()
                drive_devices = [f"{base}{i}" for i in range(drive_count)]
            else:
                drive_devices = [device_path] + [f"/dev/nst{i}" for i in range(1, drive_count)]

    tape_config_override = dict(tape_config)
    if changer_path:
        tape_config_override['changer_device'] = changer_path
    if drive_devices:
        tape_config_override['drive_devices'] = drive_devices
    elif device_path:
        tape_config_override['drive_device'] = device_path

    config_for_devices = dict(file_config)
    config_for_devices['tape'] = tape_config_override
    resolved_devices, health = get_devices(config_for_devices, state_config)

    if resolved_devices.get("drive_nst"):
        if not drive_devices or not os.path.exists(drive_devices[0]):
            drive_devices = [resolved_devices["drive_nst"]]
    if not changer_path or (changer_path and not os.path.exists(changer_path)):
        changer_path = resolved_devices.get("changer_sg")

    if (not changer_path or not drive_devices) and os.environ.get('VTL_ENABLED') != '1':
        auto_drive, auto_changer, detected = _autodetect_tape_devices()
        if not drive_devices and auto_drive:
            drive_devices = [auto_drive]
        if not changer_path and auto_changer:
            changer_path = auto_changer
        if not drive_devices:
            reason = (
                "Unable to autodetect tape devices. "
                "Ensure the changer is a 'mediumx' device and the drive is a tape device. "
                f"{_format_detected_devices(detected['drives'], detected['changers'])}"
            )
            if not autodetect_no_hardware_logged:
                log_info(reason, "tape")
                autodetect_no_hardware_logged = True
            _set_hardware_init_status(False, reason)
            _clear_tape_controllers()
            _initialize_dependent_services(None) # Ensure services available even if detection fails
            return {"hardware_available": False, "hardware_reason": reason}
    if health.get("warnings"):
        log_warning(f"Tape device warnings: {'; '.join(health['warnings'])}", 'tape')

    if changer_path and not is_medium_changer_device(changer_path):
        auto_changer = resolved_devices.get("changer_sg")
        if auto_changer and is_medium_changer_device(auto_changer):
            log_warning(
                f"Configured changer {changer_path} appears to be a tape drive. "
                f"Using detected changer {auto_changer} instead.",
                'tape'
            )
            changer_path = auto_changer

    # Create controllers
    if os.environ.get('VTL_ENABLED') == '1':
        from backend.tape.vtl import VirtualTapeController
        log_info(f"Initializing Virtual Tape Library (VTL)...", 'tape')
        tape_controller = VirtualTapeController(
            device=drive_devices,
            changer=changer_path,
            config=config_for_devices,
            state=state_config,
            log_callback=_log_tape_command,
            event_logger=_log_tape_event,
        )
    else:
        log_info(f"Using tape devices: drives={drive_devices}, changer={changer_path}", 'tape')
        # Initialize controllers and engines
        from backend.services.webhook_service import init_webhook_service
        webhook_service = init_webhook_service(db)
        tape_controller = TapeLibraryController(
            device=drive_devices,
            changer=changer_path,
            config=config_for_devices,
            state=state_config,
            log_callback=_log_tape_command,
            event_logger=_log_tape_event,
            db=db,
            webhook_service=webhook_service,
        )
    _log_changer_permission_diagnostics(changer_path)
    
    # Initialize LibraryManager and register the primary/default controller
    library_manager = LibraryManager(db, event_logger=_log_tape_event)
    library_manager.register_controller('default', tape_controller, make_default=True)
    # Attempt to load additional libraries from config
    try:
        library_manager.initialize()
    except Exception as e:
        log_error(f"Failed to initialize additional libraries: {e}", 'system')

    _initialize_dependent_services(tape_controller)

    # Expose permission snapshot helper if available
    if 'get_permission_snapshot' in globals():
        app.get_permission_snapshot = get_permission_snapshot

    start_state_update_thread()
    _register_shutdown_handlers()

    _set_hardware_init_status(True, None)

    return {"hardware_available": True, "hardware_reason": None}


def initialize_controllers():
    """
    Initialize tape controller, backup engine, scheduler, and autopilot.
    Call this ONLY when ready to start services.

    Requires: initialize_app() and initialize_socketio() must be called first

    Returns:
        Dictionary of initialized controllers and availability status
    """
    global tape_controller, library_manager, smb_client, backup_engine, advanced_restore_engine, duplication_engine, streaming_pipeline, scheduler, autopilot, preflight_checker

    # Ensure dependencies are initialized
    if db is None:
        raise RuntimeError("Must call initialize_app() before initialize_controllers()")
    if socketio is None:
        raise RuntimeError("Must call initialize_socketio() before initialize_controllers()")

    smb_status = initialize_smb_client()
    tape_status = initialize_tape_controller()

    return {
        "hardware_available": tape_status.get("hardware_available"),
        "hardware_reason": tape_status.get("hardware_reason"),
        "smb_available": smb_status.get("smb_available"),
        "smb_reason": smb_status.get("smb_reason"),
        "tape": tape_controller,
        "library_manager": library_manager,
        "smb": smb_client,
        "backup": backup_engine,
        "scheduler": scheduler,
        "autopilot": autopilot,
    }


_shutdown_in_progress = False


def _register_shutdown_handlers():
    def shutdown_services(signum: int = None, frame=None):
        global _shutdown_in_progress
        if _shutdown_in_progress:
            return
        _shutdown_in_progress = True
        skip_unload = os.environ.get("FOSSILSAFE_SKIP_SHUTDOWN_UNLOAD", "0") == "1"
        try:
            if scheduler and scheduler.is_running():
                scheduler.stop()
        except Exception:
            pass
        try:
            if autopilot and autopilot.running:
                autopilot.stop()
        except Exception:
            pass
        try:
            if tape_controller and not skip_unload:
                log_warning("Shutdown: attempting safe tape unload", 'tape')
                results = tape_controller.safe_shutdown_cleanup()
                if results.get("errors"):
                    log_warning(f"Shutdown cleanup issues: {results['errors']}", 'tape')
            elif tape_controller and skip_unload:
                log_warning("Shutdown: skipping tape unload (FOSSILSAFE_SKIP_SHUTDOWN_UNLOAD=1)", 'tape')
        except Exception:
            pass

    atexit.register(shutdown_services)
    signal.signal(signal.SIGTERM, shutdown_services)
    signal.signal(signal.SIGINT, shutdown_services)


# =============================================================================
# Main Entry Point
# =============================================================================

# =============================================================================
# Search API
# =============================================================================

@app.route('/api/search')
def api_search_files():
    """Global file search."""
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'success': True, 'files': []})
    if len(query) < 2:
        return _make_error_response("invalid_query", "Query too short", 400)
    
    try:
        files = db.search_files(query)
        return jsonify({'success': True, 'files': files})
    except Exception as e:
        log_error(f"Search failed: {e}", 'search')
        return _make_error_response("search_error", str(e), 500)


@app.route('/api/stats/dashboard')
def api_get_dashboard_stats():
    """Get aggregated dashboard statistics."""
    try:
        stats = db.get_dashboard_stats()
        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        log_error(f"Failed to get dashboard stats: {e}", 'system')
        return _make_error_response("stats_error", str(e), 500)


def initialize_backend(db_path: Optional[str] = None) -> Flask:
    resolved_db_path = _resolve_db_path(db_path)
    run_startup_validation(resolved_db_path)

    config = {'DB_PATH': resolved_db_path}
    create_app(config, autostart_services=False)
    create_socketio()
    controllers = initialize_controllers()
    if not controllers.get("hardware_available"):
        reason = controllers.get("hardware_reason") or "hardware unavailable"
        db.log_entry('warning', 'system', f'Hardware controllers unavailable; running in no-hardware mode ({reason})')
        _apply_startup_recovery()
        return app

    # Log startup
    db.log_entry('info', 'system', 'FossilSafe starting up')

    # Start remaining services in background
    def _bg_init():
        try:
            # Initialize tape library
            db.log_entry('info', 'system', 'Initializing tape library...')
            try:
                controllers['tape'].initialize()
                db.log_entry('info', 'system', '✓ Tape library online')
                _set_hardware_init_status(True, None)
            except Exception as e:
                db.log_entry('warning', 'system', f'Tape library initialization failed: {e}')
                db.log_entry('warning', 'system', '  Continuing in drive-only mode...')
                _set_hardware_init_status(False, str(e))
            finally:
                _apply_startup_recovery()

            # Start scheduler
            db.log_entry('info', 'system', 'Starting scheduler...')
            controllers['scheduler'].start()
            db.log_entry('info', 'system', '✓ Scheduler started')

            # Start autopilot
            db.log_entry('info', 'system', 'Starting autopilot engine...')
            controllers['autopilot'].start()
            db.log_entry('info', 'system', '✓ Autopilot started')
        except Exception as e:
            log_error(f"Background initialization failed: {e}", 'system')

    threading.Thread(target=_bg_init, daemon=True).start()

    return app

def main():
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='FossilSafe LTO Backup System')
    parser.add_argument('--port', type=int, default=int(os.environ.get('FOSSILSAFE_BACKEND_PORT', 5000)),
                       help='Port to listen on (default: 5000)')
    parser.add_argument('--host', default='0.0.0.0',
                       help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--debug', action='store_true',
                       help='Enable debug mode')
    parser.add_argument('--db-path', default=None,
                       help='Path to database file')
    args = parser.parse_args()

    print("="*60)
    print("  FossilSafe - LTO Tape Backup System")
    print("  Bulletproof backups for the ages")
    print("="*60)
    print()

    initialize_backend(args.db_path)

    print()
    print("="*60)
    print(f"  FossilSafe ready on {args.host}:{args.port}")
    print("  Press Ctrl+C to stop")
    print("="*60)
    print()

    # Start web server
    try:
        socketio.run(app, host=args.host, port=args.port,
                    debug=args.debug, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        print("\n\nShutting down gracefully...")
        controllers['scheduler'].stop()
        controllers['autopilot'].stop()
        print("Goodbye!")


if __name__ == '__main__':
    main()
