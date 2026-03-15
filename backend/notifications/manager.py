"""
Notification manager to coordinate notification providers.
"""
import threading
from typing import Dict, Any, List, Tuple
from backend.notifications.base import NotificationProvider
from backend.notifications.smtp_notifier import SMTPNotifier
from backend.notifications.webhook_notifier import WebhookNotifier
from backend.notifications.snmp_notifier import SnmpNotifier


class NotificationManager:
    """Singleton manager for notification providers."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self.providers: List[NotificationProvider] = []
        self.config: Dict[str, Any] = {}
        self._initialized = True
    
    def load_config(self, config: Dict[str, Any]):
        """Load notification configuration and initialize providers."""
        self.config = config
        self.providers = []
        
        # Initialize SMTP provider if configured
        smtp_config = config.get('smtp', {})
        if smtp_config.get('enabled'):
            self.providers.append(SMTPNotifier(smtp_config))
        
        # Initialize Webhook provider if configured
        webhook_config = config.get('webhook', {})
        if webhook_config.get('enabled'):
            self.providers.append(WebhookNotifier(webhook_config))

        # Initialize SNMP provider if configured
        snmp_config = config.get('snmp', {})
        if snmp_config.get('enabled'):
            self.providers.append(SnmpNotifier(snmp_config))
    
    def send_notification(self, event_type: str, data: Dict[str, Any]) -> List[Tuple[str, bool, str]]:
        """
        Send notification to all enabled providers.
        
        Args:
            event_type: Type of event
            data: Event data
        
        Returns:
            List of (provider_name, success, error_message) tuples
        """
        results = []
        
        for provider in self.providers:
            if not provider.is_enabled():
                continue
            
            provider_name = provider.__class__.__name__
            success, error = provider.send(event_type, data)
            results.append((provider_name, success, error or ''))
        
        return results
    
    def test_provider(self, provider_type: str) -> Tuple[bool, str]:
        """
        Test a specific provider.
        
        Args:
            provider_type: 'smtp' or 'webhook' or 'snmp'
        
        Returns:
            Tuple of (success, message)
        """
        if provider_type == 'smtp':
            smtp_config = self.config.get('smtp', {})
            if not smtp_config:
                return False, "SMTP not configured"
            provider = SMTPNotifier(smtp_config)
            return provider.test_connection()
        
        elif provider_type == 'webhook':
            webhook_config = self.config.get('webhook', {})
            if not webhook_config:
                return False, "Webhook not configured"
            provider = WebhookNotifier(webhook_config)
            return provider.test_connection()

        elif provider_type == 'snmp':
            snmp_config = self.config.get('snmp', {})
            if not snmp_config:
                return False, "SNMP not configured"
            provider = SnmpNotifier(snmp_config)
            return provider.test_connection()
        
        else:
            return False, f"Unknown provider type: {provider_type}"
    
    def get_enabled_providers(self) -> List[str]:
        """Get list of enabled provider names."""
        return [p.__class__.__name__ for p in self.providers if p.is_enabled()]


# Global instance
notification_manager = NotificationManager()
