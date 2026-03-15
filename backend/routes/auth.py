from flask import Blueprint, current_app, jsonify
from flask_wtf.csrf import generate_csrf
from backend.utils.responses import success_response
import os

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/api/csrf-token', methods=['GET'])
def get_csrf_token():
    """Get CSRF token for frontend"""
    # Flask-WTF stores the CSRFProtect instance in extensions
    csrf_enabled = current_app.config.get('WTF_CSRF_ENABLED', True)
    
    if not csrf_enabled:
        return success_response(data={'csrf_token': None, 'csrf_enabled': False})
        
    print("DEBUG: Entered get_csrf_token in auth.py")
    try:
        from flask import jsonify
        token = generate_csrf()
        print(f"DEBUG: Generated token: {token}")
        response, status = success_response(data={'csrf_token': token, 'csrf_enabled': True})
        print(f"DEBUG: Response data: {response.get_data(as_text=True)}")
        return response, status
    except Exception as e:
        print(f"DEBUG: generate_csrf failed: {e}")
        import traceback
        traceback.print_exc()
        return success_response(data={'csrf_token': None, 'csrf_enabled': False})

# --- SSO / OIDC Implementation ---
import requests
import secrets
import json
from flask import request, redirect, url_for, session as flask_session
from backend.auth import get_auth_manager, require_admin, require_auth
from backend.utils.responses import error_response

def get_oidc_config(db):
    """Retrieve OIDC config from settings."""
    row = db.execute("SELECT value FROM settings WHERE key = 'oidc_config'").fetchone()
    if row and row['value']:
        return json.loads(row['value'])
    return None

def save_oidc_config(db, config):
    """Save OIDC config to settings."""
    db.execute(
        "INSERT INTO settings (key, value) VALUES ('oidc_config', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (json.dumps(config),)
    )
    db.commit()

@auth_bp.route('/api/auth/sso/config', methods=['GET'])
def get_sso_config():
    """Get current SSO configuration (Publicly accessible for login screen)."""
    from backend.auth import get_auth_manager
    auth_mgr = get_auth_manager()
    if not auth_mgr:
        return error_response("Auth subsystem not initialized", 500)
    
    config = get_oidc_config(auth_mgr.db)
    if not config:
        return success_response(data={'enabled': False})
    
    # Don't return the client secret
    safe_config = config.copy()
    if 'client_secret' in safe_config:
        safe_config['client_secret'] = '********'
        
    return success_response(data=safe_config)

@auth_bp.route('/api/auth/sso/config', methods=['POST'])
@require_admin
def update_sso_config():
    """Update SSO configuration."""
    auth_mgr = get_auth_manager()
    if not auth_mgr:
        return error_response("Auth subsystem not initialized", 500)
        
    data = request.json
    issuer = data.get('issuer')
    client_id = data.get('client_id')
    client_secret = data.get('client_secret')
    enabled = data.get('enabled', False)
    
    groups_claim = data.get('groups_claim', 'groups')
    role_map = data.get('role_map', {}) # Dict of { "group_name": "role" }
    
    if not issuer or not client_id:
        return error_response("Issuer and Client ID are required")
        
    # If client_secret is masked, fetch existing to preserve it
    current_config = get_oidc_config(auth_mgr.db) or {}
    if client_secret == '********' and 'client_secret' in current_config:
        client_secret = current_config['client_secret']
    
    # Auto-discovery
    try:
        discovery_url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
        resp = requests.get(discovery_url, timeout=5)
        resp.raise_for_status()
        provider_config = resp.json()
    except Exception as e:
         # Fallback or strict error? Let's verify discovery works as a validation step.
         return error_response(f"OIDC Discovery failed for {issuer}: {str(e)}")

    new_config = {
        'enabled': enabled,
        'issuer': issuer,
        'client_id': client_id,
        'client_secret': client_secret,
        'groups_claim': groups_claim,
        'role_map': json.dumps(role_map) if isinstance(role_map, dict) else role_map,
        'authorization_endpoint': provider_config.get('authorization_endpoint'),
        'token_endpoint': provider_config.get('token_endpoint'),
        'userinfo_endpoint': provider_config.get('userinfo_endpoint'),
        'jwks_uri': provider_config.get('jwks_uri')
    }
    
    save_oidc_config(auth_mgr.db, new_config)
    return success_response(message="SSO configuration saved")

@auth_bp.route('/api/auth/sso/login', methods=['GET'])
def sso_login():
    """Start SSO login flow."""
    auth_mgr = get_auth_manager()
    config = get_oidc_config(auth_mgr.db)
    
    if not config or not config.get('enabled'):
        return error_response("SSO is not enabled", 400)
        
    # Generate state and nonce
    state = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(16)
    
    # Store state in backend-side session (requires standard Flask session setup)
    # Since we are using token-based mostly, we might rely on a cookie for the callback
    # For now, let's assume we can use flask_session (signed cookie)
    flask_session['oidc_state'] = state
    flask_session['oidc_nonce'] = nonce
    
    # Build redirect URL
    redirect_uri = url_for('auth.sso_callback', _external=True)
    
    auth_url = (
        f"{config['authorization_endpoint']}?"
        f"client_id={config['client_id']}&"
        f"response_type=code&"
        f"scope=openid email profile&"
        f"redirect_uri={redirect_uri}&"
        f"state={state}&"
        f"nonce={nonce}"
    )
    
    return redirect(auth_url)

@auth_bp.route('/api/auth/sso/callback', methods=['GET'])
def sso_callback():
    """Handle SSO callback."""
    error = request.args.get('error')
    if error:
        return redirect(f"/?error=sso_failed&details={error}")
        
    code = request.args.get('code')
    state = request.args.get('state')
    
    if not code or not state:
         return redirect("/?error=sso_invalid_request")
         
    # Verify state
    stored_state = flask_session.get('oidc_state')
    if not stored_state or state != stored_state:
        return redirect("/?error=sso_state_mismatch")
        
    auth_mgr = get_auth_manager()
    config = get_oidc_config(auth_mgr.db)
    
    if not config:
        return redirect("/?error=sso_config_missing")

    redirect_uri = url_for('auth.sso_callback', _external=True)
    
    # Exchange code for token
    try:
        token_resp = requests.post(
            config['token_endpoint'],
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': redirect_uri,
                'client_id': config['client_id'],
                'client_secret': config['client_secret'],
            },
            headers={'Accept': 'application/json'},
            timeout=10
        )
        token_resp.raise_for_status()
        tokens = token_resp.json()
        access_token = tokens.get('access_token')
        
        # Get User Info
        user_resp = requests.get(
            config['userinfo_endpoint'],
            headers={'Authorization': f"Bearer {access_token}"},
            timeout=10
        )
        user_resp.raise_for_status()
        user_info = user_resp.json()
        
    except Exception as e:
        print(f"SSO Token Exchange Error: {e}")
        return redirect("/?error=sso_token_exchange_failed")
    
    # Map user
    sso_id = user_info.get('sub')
    email = user_info.get('email')
    username = user_info.get('preferred_username', email)
    
    if not sso_id or not username:
        return redirect("/?error=sso_user_info_incomplete")
        
    # --- Role Mapping Logic ---
    # Default to 'viewer'
    assigned_role = 'viewer'
    
    # Get role mapping config
    role_map_json = config.get('role_map', '{}')
    groups_claim = config.get('groups_claim', 'groups')
    
    try:
        role_map = json.loads(role_map_json)
        # Extract user groups from claims
        # Handling dot-notation for nested claims could be added (e.g. 'realm_access.roles')
        # For now, simplistic check on top-level or 'groups'
        user_groups = user_info.get(groups_claim, [])
        if isinstance(user_groups, str):
            user_groups = [user_groups]
            
        # Check against map
        # Priority: Admin > Operator > Viewer
        for group in user_groups:
            mapped = role_map.get(str(group))
            if mapped == 'admin':
                assigned_role = 'admin'
                break # Max privilege found
            elif mapped == 'operator' and assigned_role != 'admin':
                assigned_role = 'operator'
            # viewer is default
            
    except Exception as e:
        print(f"Role Mapping Error: {e}")
        # Fallback to viewer on error
    
    # Check if user exists
    user = auth_mgr.get_sso_user(config['issuer'], sso_id)
    
    if not user:
        # Check if username exists (legacy match)
        existing = auth_mgr.get_user(username)
        if existing:
            # Link accounts? For now, we fail safe or auto-provision.
            pass 
            
        # JIT Provisioning
        user_id = auth_mgr.create_sso_user(username, config['issuer'], sso_id, role=assigned_role)
        if not user_id:
             return redirect("/?error=sso_provision_failed")
        user = auth_mgr.get_user_by_id(user_id)
    else:
        # Update role on login if it changed (and we entrust the IdP)
        # Only if the mapped role is "stronger" or explicit sync is desired?
        # Let's enforcing sync:
        if user.role != assigned_role:
             auth_mgr.update_user_role(user.id, assigned_role)
             user.role = assigned_role 
             
    # Login
    # Security Enforcement: If the user has a native TOTP secret, they must verify it
    # even if logged in via SSO. Marking has_2fa=False if user.totp_secret is present
    # forces native 2FA verification path.
    requires_native_2fa = bool(user.totp_secret)
    
    try:
        session_token = auth_mgr.login_sso_user(user, has_2fa=not requires_native_2fa)
    except ValueError:
        return redirect("/?error=account_disabled")
        
    # Redirect to frontend with token
    # We set a cookie because the frontend is SPA and we came from a full redirect
    # Or we redirect to /?token=XVZ... and let frontend grab it
    
    resp = redirect("/")
    # Set a short-lived cookie for the frontend to consume. 
    # Secure=True is required for modern browsers over HTTPS.
    # HttpOnly=False allows the frontend JS to read the token and store it in localStorage.
    resp.set_cookie('sso_token', session_token, max_age=60, secure=True, httponly=False, samesite='Lax')
    # NOTE: Secure=False for local dev ease, should be True in prod. 
    # HttpOnly=False so JS can read and put it in localStorage/headers for API calls? 
    # Actually, better to use standard session cookie if we switched to that, but current arch usage is header-based 'Authorization: Bearer'
    # So we let frontend read this cookie, set its own store, and then delete it.
    
    return resp

# --- Standard Auth Routes ---

@auth_bp.route('/api/auth/login', methods=['POST'])
def login():
    """Authenticate and create session."""
    try:
        data = request.json or {}
        username = data.get('username')
        password = data.get('password')
        totp_code = data.get('totp_code')
        
        if not username or not password:
             return error_response("Username and password are required", status_code=400)
             
        auth_mgr = get_auth_manager()
        user = auth_mgr.get_user(username)
        
        if not user or not user.is_active:
             return error_response("Invalid username or password", status_code=401)
             
        # Verify password
        if not auth_mgr.verify_password(password, user.password_hash):
             return error_response("Invalid username or password", status_code=401)
             
        # Check 2FA
        if user.totp_secret:
            if not totp_code:
                # Return 2FA required hint
                return jsonify({
                    'success': False, 
                    'error': '2FA code required',
                    'require_2fa': True 
                }), 401
            
            if not auth_mgr.verify_2fa(user.id, totp_code):
                 return error_response("Invalid 2FA code", status_code=401)
                 
        # Create session
        token = auth_mgr.login(username, password, has_2fa=bool(user.totp_secret))
        if not token:
             return error_response("Login failed", status_code=401)
             
        return success_response(data={
            'token': token,
            'username': user.username,
            'role': user.role,
            'id': user.id,
            'has_2fa': bool(user.totp_secret)
        })
        
    except Exception as e:
        return error_response(str(e))

@auth_bp.route('/api/auth/logout', methods=['POST'])
def logout():
    """Clear session."""
    auth_mgr = get_auth_manager()
    auth_header = request.headers.get('Authorization', '')
    token = None
    if auth_header.startswith('Bearer '):
        token = auth_header[7:]
    
    if token:
        auth_mgr.logout(token)
        
    return success_response(message="Logged out")

@auth_bp.route('/api/auth/me', methods=['GET'])
@require_auth
def get_me():
    """Get current user info."""
    auth_mgr = get_auth_manager()
    user = auth_mgr.get_current_user()
    if not user:
        return error_response("Not authenticated", status_code=401)
        
    return success_response(data={
        'id': user.id,
        'username': user.username,
        'role': user.role,
        'has_2fa': bool(user.totp_secret)
    })

# --- 2FA Management ---

@auth_bp.route('/api/auth/2fa/setup', methods=['POST'])
@require_admin # Adjust if non-admins can setup 2FA for themselves
def setup_2fa():
    auth_mgr = get_auth_manager()
    user = auth_mgr.get_current_user()
    if not user:
        return error_response("Not authenticated", status_code=401)
        
    secret, uri = auth_mgr.generate_totp_secret(user.id)
    return success_response(data={
        'secret': secret,
        'provisioning_uri': uri 
    })

@auth_bp.route('/api/auth/2fa/enable', methods=['POST'])
@require_auth
def enable_2fa():
    auth_mgr = get_auth_manager()
    user = auth_mgr.get_current_user()
    if not user:
        return error_response("Not authenticated", status_code=401)
        
    data = request.json or {}
    secret = data.get('secret')
    code = data.get('code')
    
    if not secret or not code:
        return error_response("Secret and code are required", status_code=400)
        
    # TEST_MODE check removed for security hardening
    # If tests need to enable 2FA, they must provide a valid code.

    if auth_mgr.enable_2fa(user.id, secret, code):
        return success_response(message="2FA enabled")
    else:
        return error_response("Invalid verification code", status_code=400)

@auth_bp.route('/api/auth/2fa/disable', methods=['POST'])
@require_auth
def disable_2fa():
    auth_mgr = get_auth_manager()
    user = auth_mgr.get_current_user()
    if not user:
        return error_response("Not authenticated", status_code=401)
        
    if auth_mgr.disable_2fa(user.id):
        return success_response(message="2FA disabled")
    else:
        return error_response("Failed to disable 2FA")

# --- User Management (Admin Only) ---

@auth_bp.route('/api/auth/users', methods=['GET'])
@require_admin
def list_users():
    auth_mgr = get_auth_manager()
    users = auth_mgr.list_users()
    return success_response(data={'users': users})

@auth_bp.route('/api/auth/users', methods=['POST'])
@require_admin
def create_user():
    auth_mgr = get_auth_manager()
    data = request.json or {}
    username = data.get('username')
    password = data.get('password')
    role = data.get('role', 'viewer')
    
    if not username or not password:
        return error_response("Username and password are required", status_code=400)
        
    user_id = auth_mgr.create_user(username, password, role)
    if user_id:
        return success_response(data={'user_id': user_id}, status_code=201)
    else:
        return error_response("Failed to create user (maybe username exists?)")

@auth_bp.route('/api/auth/users/<int:user_id>', methods=['PUT'])
@require_admin
def update_user(user_id):
    auth_mgr = get_auth_manager()
    data = request.json or {}
    role = data.get('role')
    is_active = data.get('is_active')
    
    if auth_mgr.update_user(user_id, role=role, is_active=is_active):
        return success_response(message="User updated")
    else:
        return error_response("Failed to update user")

@auth_bp.route('/api/auth/users/<int:user_id>', methods=['DELETE'])
@require_admin
def delete_user(user_id):
    auth_mgr = get_auth_manager()
    if auth_mgr.delete_user(user_id):
        return success_response(message="User deleted")
    else:
        return error_response("Failed to delete user")
@auth_bp.route('/api/auth/users/<int:user_id>/disable-2fa', methods=['POST'])
@require_admin
def admin_disable_2fa(user_id):
    auth_mgr = get_auth_manager()
    if auth_mgr.disable_2fa(user_id):
        return success_response(message="2FA disabled for user")
    else:
        return error_response("Failed to disable 2FA")
