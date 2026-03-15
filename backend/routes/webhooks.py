from flask import Blueprint, request, jsonify, current_app
from backend.utils.responses import success_response, error_response
from backend.auth import require_admin
from backend.services.webhook_service import WebhookService

webhooks_bp = Blueprint('webhooks', __name__)

def get_webhook_service():
    db = current_app.config.get('db')
    return WebhookService(db)

@webhooks_bp.route('/api/webhooks', methods=['GET'])
@require_admin
def list_webhooks():
    """List all configured webhooks."""
    db = current_app.config.get('db')
    return success_response(data={'webhooks': db.get_webhooks()})

@webhooks_bp.route('/api/webhooks', methods=['POST'])
@require_admin
def create_webhook():
    """Create a new webhook configuration."""
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({'error': 'URL is required'}), 400

    name = data.get('name')
    event_types = data.get('event_types', [])
    secret = data.get('secret')

    db = current_app.config.get('db')
    webhook_id = db.add_webhook(url, name, event_types, secret)
    
    return success_response(
        message='Webhook created',
        data={'id': webhook_id},
        status_code=201
    )

@webhooks_bp.route('/api/webhooks/<int:webhook_id>', methods=['DELETE'])
@require_admin
def delete_webhook(webhook_id):
    """Delete a webhook."""
    db = current_app.config.get('db')
    if db.delete_webhook(webhook_id):
        return success_response(message='Webhook deleted')
    return error_response('Webhook not found', status_code=404)

@webhooks_bp.route('/api/webhooks/<int:webhook_id>', methods=['PATCH'])
@require_admin
def update_webhook(webhook_id):
    """Update a webhook configuration."""
    data = request.json
    db = current_app.config.get('db')
    if db.update_webhook(webhook_id, data):
        return success_response(message='Webhook updated')
    return error_response('Webhook not found or no changes made', status_code=404)

@webhooks_bp.route('/api/webhooks/test', methods=['POST'])
@require_admin
def test_webhook():
    """Send a test payload to a URL."""
    data = request.json
    url = data.get('url')
    secret = data.get('secret')
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400

    service = get_webhook_service()
    
    test_webhook_cfg = {
        'url': url,
        'secret': secret
    }
    
    # Send mock event
    import json
    import hmac
    import hashlib
    import requests
    
    payload = {
        "event": "WEBHOOK_TEST",
        "timestamp": "2026-02-14T22:00:00Z",
        "data": {"message": "This is a test notification from FossilSafe"},
        "source": "FossilSafe"
    }
    
    payload_bytes = json.dumps(payload).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if secret:
        signature = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
        headers['X-FossilSafe-Signature'] = f"sha256={signature}"

    try:
        resp = requests.post(url, data=payload_bytes, headers=headers, timeout=5)
        return jsonify({
            'status': resp.status_code,
            'success': resp.status_code < 400,
            'response': resp.text[:200]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
