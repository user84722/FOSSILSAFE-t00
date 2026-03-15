"""
Webhook notification provider.
Supports Discord, Slack, and generic JSON webhooks.
"""
import json
import urllib.request
import urllib.error
from typing import Dict, Any, Tuple, Optional
from backend.notifications.base import NotificationProvider


class WebhookNotifier(NotificationProvider):
    """Webhook notification provider."""
    
    def send(self, event_type: str, data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Send webhook notification."""
        if not self.is_enabled():
            return False, "Webhook notifier is disabled"
        
        try:
            webhook_url = self.config.get('webhook_url')
            webhook_type = self.config.get('webhook_type', 'generic')  # discord, slack, generic
            
            if not webhook_url:
                return False, "Webhook URL not configured"
            
            # Build payload based on webhook type
            if webhook_type == 'discord':
                payload = self._build_discord_payload(event_type, data)
            elif webhook_type == 'slack':
                payload = self._build_slack_payload(event_type, data)
            else:
                payload = self._build_generic_payload(event_type, data)
            
            # Send webhook
            req = urllib.request.Request(
                webhook_url,
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status in [200, 204]:
                    return True, None
                else:
                    return False, f"Webhook returned status {response.status}"
            
        except urllib.error.HTTPError as e:
            return False, f"HTTP error {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            return False, f"URL error: {str(e.reason)}"
        except Exception as e:
            return False, f"Failed to send webhook: {str(e)}"
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test webhook by sending a test message."""
        test_data = {
            'job_name': 'Test Notification',
            'status': 'Test',
            'timestamp': 'Now'
        }
        
        success, error = self.send('job_completed', test_data)
        if success:
            return True, "Webhook test successful"
        else:
            return False, error or "Webhook test failed"
    
    def _build_discord_payload(self, event_type: str, data: Dict[str, Any]) -> Dict:
        """Build Discord webhook payload with rich embed."""
        title = self.format_event_title(event_type, data)
        
        # Determine color based on event type
        if 'failed' in event_type:
            color = 0xdc2626  # red
        elif 'completed' in event_type:
            color = 0x16a34a  # green
        elif 'started' in event_type:
            color = 0x2563eb  # blue
        else:
            color = 0x6b7280  # gray
        
        # Build embed fields
        fields = []
        if 'job_name' in data:
            fields.append({'name': 'Job', 'value': data['job_name'], 'inline': True})
        if 'status' in data:
            fields.append({'name': 'Status', 'value': data['status'], 'inline': True})
        if 'duration' in data:
            fields.append({'name': 'Duration', 'value': data['duration'], 'inline': True})
        if 'tape_barcode' in data:
            fields.append({'name': 'Tape', 'value': data['tape_barcode'], 'inline': True})
        if 'error' in data:
            fields.append({'name': 'Error', 'value': data['error'], 'inline': False})
        
        embed = {
            'title': title,
            'color': color,
            'fields': fields,
            'footer': {'text': 'FossilSafe LTO Backup System'},
        }
        
        if 'timestamp' in data:
            embed['timestamp'] = data['timestamp']
        
        return {'embeds': [embed]}
    
    def _build_slack_payload(self, event_type: str, data: Dict[str, Any]) -> Dict:
        """Build Slack webhook payload."""
        title = self.format_event_title(event_type, data)
        message = self.format_event_message(event_type, data)
        
        # Determine color based on event type
        if 'failed' in event_type:
            color = 'danger'
        elif 'completed' in event_type:
            color = 'good'
        else:
            color = '#2563eb'
        
        return {
            'attachments': [{
                'color': color,
                'title': title,
                'text': message,
                'footer': 'FossilSafe LTO Backup System',
            }]
        }
    
    def _build_generic_payload(self, event_type: str, data: Dict[str, Any]) -> Dict:
        """Build generic JSON webhook payload."""
        return {
            'event_type': event_type,
            'title': self.format_event_title(event_type, data),
            'message': self.format_event_message(event_type, data),
            'data': data,
            'source': 'FossilSafe'
        }
