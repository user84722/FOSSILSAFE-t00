from flask import Blueprint, jsonify, request, current_app
from backend.auth import require_role
from backend.kms_provider import create_key_provider, VaultKeyProvider, LocalKeyProvider

kms_bp = Blueprint('kms', __name__)

@kms_bp.route('/api/kms/status', methods=['GET'])
@require_role('admin')
def get_kms_status():
    """Get connection status and health for the active KMS provider."""
    db = current_app.db
    kms_type = db.get_setting('kms_type', 'local')
    
    # Try to get existing provider from app context
    provider = getattr(current_app, 'key_provider', None)
    
    if not provider:
        # Re-initialize to check health
        try:
            config = {'type': kms_type}
            if kms_type == 'vault':
                config.update({
                    'vault_addr': db.get_setting('kms_vault_addr'),
                    'vault_token': db.get_setting('kms_vault_token'),
                    'mount_path': db.get_setting('kms_vault_mount_path', 'secret')
                })
            provider = create_key_provider(config)
        except Exception as e:
            return jsonify({
                'success': True,
                'data': {
                    'type': kms_type,
                    'available': False,
                    'error': str(e)
                }
            })

    info = provider.get_provider_info()
    return jsonify({'success': True, 'data': info})

@kms_bp.route('/api/kms/config', methods=['GET'])
@require_role('admin')
def get_kms_config():
    """Get current KMS configuration."""
    db = current_app.db
    config = {
        'type': db.get_setting('kms_type', 'local'),
        'vault_addr': db.get_setting('kms_vault_addr', ''),
        'mount_path': db.get_setting('kms_vault_mount_path', 'secret'),
        'vault_token_configured': bool(db.get_setting('kms_vault_token', ''))
    }
    return jsonify({'success': True, 'data': config})

@kms_bp.route('/api/kms/config', methods=['POST'])
@require_role('admin')
def update_kms_config():
    """Update KMS configuration and test connection."""
    data = request.get_json()
    provider_type = data.get('type', 'local')
    db = current_app.db
    
    if provider_type == 'vault':
        vault_addr = data.get('vault_addr')
        vault_token = data.get('vault_token')
        mount_path = data.get('mount_path', 'secret')
        
        if not vault_token and db.get_setting('kms_vault_token'):
            vault_token = db.get_setting('kms_vault_token')
            
        if not vault_addr or not vault_token:
            return jsonify({'success': False, 'error': 'Vault address and token are required'})
            
        try:
            provider = VaultKeyProvider(vault_addr, vault_token, mount_path)
            if not provider._check_availability():
                return jsonify({'success': False, 'error': 'Could not connect to Vault with provided credentials'})
        except Exception as e:
             return jsonify({'success': False, 'error': str(e)})
             
        db.set_setting('kms_type', 'vault')
        db.set_setting('kms_vault_addr', vault_addr)
        if data.get('vault_token'):
            db.set_setting('kms_vault_token', vault_token)
        db.set_setting('kms_vault_mount_path', mount_path)
    else:
        db.set_setting('kms_type', 'local')
        
    return jsonify({'success': True, 'message': 'KMS configuration updated'})

@kms_bp.route('/api/kms/rotate', methods=['POST'])
@require_role('admin')
def rotate_kms_key():
    """Rotate the master encryption key (Local provider only)."""
    db = current_app.db
    kms_type = db.get_setting('kms_type', 'local')
    
    if kms_type != 'local':
        return jsonify({'success': False, 'error': 'Key rotation only supported for local key provider.'})
        
    try:
        provider = getattr(current_app, 'key_provider', None)
        if not provider or not isinstance(provider, LocalKeyProvider):
             from backend.kms_provider import create_key_provider
             provider = create_key_provider({'type': 'local'})
             
        success, message = provider.rotate_master_key()
        if success:
             return jsonify({'success': True, 'message': message})
        else:
             return jsonify({'success': False, 'error': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
