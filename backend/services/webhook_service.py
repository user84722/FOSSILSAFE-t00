import json
import threading
import hmac
import hashlib
import requests
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class WebhookService:
    """Service for managing and dispatching asynchronous webhooks."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, db=None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, db=None):
        if self._initialized:
            return
        self.db = db
        self._initialized = True
        logger.info("WebhookService initialized")

    def trigger_event(self, event_type: str, payload: Dict[str, Any]):
        """Trigger a webhook event asynchronously."""
        if not self.db:
            logger.warning("WebhookService: No database connection")
            return

        thread = threading.Thread(target=self._dispatch_event, args=(event_type, payload))
        thread.daemon = True
        thread.start()

    def _dispatch_event(self, event_type: str, payload: Dict[str, Any]):
        """Internal dispatcher that runs in a background thread."""
        try:
            webhooks = self.db.get_webhooks(active_only=True)
            if not webhooks:
                return

            # Enrich payload
            enriched_payload = {
                "event": event_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": payload,
                "source": "FossilSafe"
            }
            payload_bytes = json.dumps(enriched_payload, sort_keys=True).encode('utf-8')

            for webhook in webhooks:
                # Filter by event type if restricted
                allowed_events = webhook.get('event_types', [])
                if allowed_events and event_type not in allowed_events:
                    continue

                self._send_to_webhook(webhook, payload_bytes)

        except Exception as e:
            logger.error(f"Webhook dispatch error: {e}")

    def _send_to_webhook(self, webhook: Dict[str, Any], payload_bytes: bytes):
        """Send the payload to a specific webhook endpoint."""
        url = webhook.get('url')
        secret = webhook.get('secret')
        
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'FossilSafe-Webhook-Service/1.0',
            'X-FossilSafe-Event': 'true'
        }

        # Add HMAC signature if secret is provided
        if secret:
            signature = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
            headers['X-FossilSafe-Signature'] = f"sha256={signature}"

        try:
            response = requests.post(url, data=payload_bytes, headers=headers, timeout=10)
            if response.status_code >= 400:
                logger.warning(f"Webhook delivery failed for {url}: Status {response.status_code}")
            else:
                logger.debug(f"Webhook delivered to {url}")
        except Exception as e:
            logger.error(f"Failed to deliver webhook to {url}: {e}")

# Global instance initialization helper
def init_webhook_service(db):
    return WebhookService(db)
