import pytest
import json
import hmac
import hashlib
import base64
from unittest.mock import patch, MagicMock
from backend.services.webhook_service import WebhookService
from backend.database import Database
import os
import time

@pytest.fixture
def db():
    db_path = "test_webhooks.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    db = Database(db_path)
    yield db
    if os.path.exists(db_path):
        os.remove(db_path)

@pytest.fixture
def webhook_service(db):
    return WebhookService(db)

def test_webhook_dispatch(db, webhook_service):
    # Setup mock webhook
    webhook_url = "https://example.com/webhook"
    secret = "test_secret"
    db.add_webhook(webhook_url, "Test Hook", ["JOB_COMPLETED"], secret)
    
    with patch('requests.post') as mock_post:
        mock_post.return_value.status_code = 200
        
        # Trigger event
        payload = {"job_id": 123, "status": "success"}
        webhook_service.trigger_event("JOB_COMPLETED", payload)
        
        # Wait for async delivery (it's threaded)
        time.sleep(1)
        
        assert mock_post.called
        args, kwargs = mock_post.call_args
        assert args[0] == webhook_url
        
        # Verify body
        body = json.loads(kwargs['data'])
        assert body['event'] == "JOB_COMPLETED"
        assert body['data'] == payload
        
        # Verify signature
        signature = kwargs['headers'].get('X-FossilSafe-Signature')
        assert signature is not None
        
        expected_sig_hex = hmac.new(
            secret.encode(),
            kwargs['data'],
            hashlib.sha256
        ).hexdigest()
        
        assert signature == f"sha256={expected_sig_hex}"

def test_webhook_filtering(db, webhook_service):
    webhook_url = "https://example.com/webhook"
    db.add_webhook(webhook_url, "Test Hook", ["JOB_FAILED"], None)
    
    with patch('requests.post') as mock_post:
        mock_post.return_value.status_code = 200
        
        # Trigger mismatched event
        webhook_service.trigger_event("JOB_COMPLETED", {"foo": "bar"})
        time.sleep(0.5)
        assert not mock_post.called
        
        # Trigger matching event
        webhook_service.trigger_event("JOB_FAILED", {"error": "boom"})
        time.sleep(1)
        assert mock_post.called
