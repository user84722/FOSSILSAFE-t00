from flask import Blueprint, request, jsonify, current_app, g
from backend.auth import require_role

preferences_bp = Blueprint('preferences', __name__)

def get_db():
    return current_app.config.get('db') or current_app.db

def get_user_id():
    """Get current user ID from session."""
    session = getattr(g, 'session', None)
    if not session:
        return 'default' # Fallback for local dev without auth if allowed
    return session.user_id

@preferences_bp.route('', methods=['GET'])
@require_role('viewer')
def get_preferences():
    """Get all user preferences."""
    db = get_db()
    user_id = get_user_id()
    
    preferences = db.get_all_user_preferences(user_id)
    return jsonify(preferences)

@preferences_bp.route('', methods=['PUT'])
@require_role('viewer')
def update_preferences():
    """Update user preferences."""
    db = get_db()
    user_id = get_user_id()
    data = request.json
    
    if not data:
        return jsonify({'error': 'No preferences provided'}), 400
    
    # Update each preference
    for key, value in data.items():
        db.set_user_preference(user_id, key, str(value))
    
    return jsonify({'status': 'success'})

@preferences_bp.route('/<key>', methods=['GET'])
@require_role('viewer')
def get_preference(key):
    """Get a specific preference."""
    db = get_db()
    user_id = get_user_id()
    
    value = db.get_user_preference(user_id, key)
    if value is None:
        return jsonify({'error': 'Preference not found'}), 404
    
    return jsonify({key: value})
