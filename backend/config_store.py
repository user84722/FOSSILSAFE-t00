import json
import os
from typing import Dict

# Default paths - can be overridden by environment variables
def get_default_config_path() -> str:
    return os.environ.get('FOSSILSAFE_CONFIG_PATH', '/etc/fossilsafe/config.json')

def get_default_data_dir() -> str:
    return os.environ.get('FOSSILSAFE_DATA_DIR', '/var/lib/fossilsafe')

def get_default_credential_key_path() -> str:
    return os.environ.get(
        'FOSSILSAFE_CREDENTIAL_KEY_PATH',
        os.path.join(get_default_data_dir(), 'credential_key.bin')
    )

def get_default_state_path() -> str:
    return os.environ.get(
        'FOSSILSAFE_STATE_PATH',
        os.path.join(get_default_data_dir(), 'state.json')
    )

def get_default_catalog_backup_dir() -> str:
    return os.environ.get(
        'FOSSILSAFE_CATALOG_BACKUP_DIR',
        os.path.join(get_default_data_dir(), 'catalog-backups')
    )

def get_default_diagnostics_dir() -> str:
    return os.environ.get(
        'FOSSILSAFE_DIAGNOSTICS_DIR',
        os.path.join(get_default_data_dir(), 'diagnostics')
    )


def get_data_dir() -> str:
    return os.path.abspath(os.path.expanduser(get_default_data_dir()))


def get_default_db_path() -> str:
    return os.path.join(get_data_dir(), 'lto_backup.db')


def get_default_staging_dir() -> str:
    return os.path.join(get_data_dir(), 'staging')


def get_catalog_backup_dir(config: Dict = None) -> str:
    env_val = os.environ.get('FOSSILSAFE_CATALOG_BACKUP_DIR')
    if env_val:
        return os.path.abspath(os.path.expanduser(env_val))
    
    if config is None:
        config = load_config()
    backup_dir = config.get('catalog_backup_dir') or config.get('CATALOG_BACKUP_DIR')
    if isinstance(backup_dir, str) and backup_dir.strip():
        return os.path.abspath(os.path.expanduser(backup_dir.strip()))
    return os.path.abspath(os.path.expanduser(get_default_catalog_backup_dir()))


def get_diagnostics_dir(config: Dict = None) -> str:
    env_val = os.environ.get('FOSSILSAFE_DIAGNOSTICS_DIR')
    if env_val:
        return os.path.abspath(os.path.expanduser(env_val))

    if config is None:
        config = load_config()
    diagnostics_dir = config.get('diagnostics_dir') or config.get('DIAGNOSTICS_DIR')
    if isinstance(diagnostics_dir, str) and diagnostics_dir.strip():
        return os.path.abspath(os.path.expanduser(diagnostics_dir.strip()))
    return os.path.abspath(os.path.expanduser(get_default_diagnostics_dir()))


def get_config_path() -> str:
    return get_default_config_path()


def get_state_path() -> str:
    return get_default_state_path()


def get_credential_key_path(config: Dict = None) -> str:
    env_val = os.environ.get('FOSSILSAFE_CREDENTIAL_KEY_PATH')
    if env_val:
        return os.path.abspath(os.path.expanduser(env_val))

    if config is None:
        config = load_config()
    key_path = config.get('credential_key_path') or config.get('CREDENTIAL_KEY_PATH')
    if isinstance(key_path, str) and key_path.strip():
        return os.path.abspath(os.path.expanduser(key_path.strip()))
    return os.path.abspath(os.path.expanduser(get_default_credential_key_path()))


def load_config(path: str = None) -> Dict:
    config_path = path or get_default_config_path()
    config = {}
    
    # 1. Load base config
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as handle:
                data = json.load(handle)
                if isinstance(data, dict):
                    config = data
        except PermissionError as exc:
            raise PermissionError(
                f"Config file '{config_path}' is not readable. Adjust permissions for the FossilSafe service user."
            ) from exc
        except (json.JSONDecodeError, OSError):
            pass

    # 2. Overlay from config.d/
    config_dir = os.path.dirname(config_path)
    overlay_dir = os.path.join(config_dir, 'config.d')
    if os.path.isdir(overlay_dir):
        try:
            # Sort files to ensure deterministic override order
            for filename in sorted(os.listdir(overlay_dir)):
                if filename.endswith('.json'):
                    file_path = os.path.join(overlay_dir, filename)
                    try:
                        with open(file_path, 'r') as handle:
                            overlay_data = json.load(handle)
                            if isinstance(overlay_data, dict):
                                _recursive_merge(config, overlay_data)
                    except Exception as e:
                        print(f"Warning: Failed to load config overlay {file_path}: {e}")
        except Exception as e:
            print(f"Warning: Failed to scan config overlay directory {overlay_dir}: {e}")

    return config


def _recursive_merge(base: Dict, overlay: Dict):
    """Recursively merge overlay dict into base dict."""
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _recursive_merge(base[key], value)
        else:
            base[key] = value


def load_state(path: str = None) -> Dict:
    state_path = path or get_default_state_path()
    if not os.path.exists(state_path):
        return {}
    try:
        with open(state_path, 'r') as handle:
            data = json.load(handle)
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError):
        return {}
    return {}


def save_config(config: Dict, path: str = None) -> None:
    config_path = path or get_default_config_path()
    directory = os.path.dirname(os.path.abspath(config_path))
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
    with open(config_path, 'w') as handle:
        json.dump(config, handle, indent=2, sort_keys=True)


def save_state(state: Dict, path: str = None) -> None:
    state_path = path or get_default_state_path()
    directory = os.path.dirname(os.path.abspath(state_path))
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
    with open(state_path, 'w') as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


def update_config(updates: Dict, path: str = None) -> Dict:
    config = load_config(path)
    config.update(updates)
    save_config(config, path)
    return config


def update_state(updates: Dict, path: str = None) -> Dict:
    state = load_state(path)
    state.update(updates)
    save_state(state, path)
    return state


def ensure_state_file(path: str = None) -> None:
    """Ensure the state file exists and is a valid JSON dict."""
    state_path = path or get_default_state_path()
    if not os.path.exists(state_path):
        save_state({}, state_path)
    else:
        # Validate it's valid JSON
        try:
            with open(state_path, 'r') as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    save_state({}, state_path)
        except (json.JSONDecodeError, OSError):
            save_state({}, state_path)


def get_last_seen_timestamp() -> int:
    """Get the last recorded system timestamp (epoch seconds)."""
    state_path = get_state_path()
    try:
         if not os.path.exists(state_path): return 0
         with open(state_path, 'r') as f:
             data = json.load(f)
             return data.get('last_seen_timestamp', 0)
    except:
        return 0

def update_last_seen_timestamp(ts: int) -> None:
    """Update the last recorded system timestamp."""
    state_path = get_state_path()
    try:
        data = {}
        if os.path.exists(state_path):
            with open(state_path, 'r') as f:
                data = json.load(f)
        
        data['last_seen_timestamp'] = ts
        
        with open(state_path, 'w') as f:
            json.dump(data, f)
    except:
        pass # Best effort




def get_mail_slot_preferences() -> Dict:
    """Retrieve mail slot handling preferences."""
    config = load_config()
    prefs = config.get('preferences', {})
    return {
        'enabled': str(prefs.get('mail_slot_enabled', 'true')).lower() == 'true',
        'auto_detect': str(prefs.get('mail_slot_auto_detect', 'true')).lower() == 'true',
        'manual_index': int(prefs.get('mail_slot_manual_index', 0))
    }

