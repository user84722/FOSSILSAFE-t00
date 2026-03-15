"""
Base notification provider interface.
All notification providers must inherit from NotificationProvider.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple, Optional


class NotificationProvider(ABC):
    """Base class for all notification providers."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the notification provider with configuration.
        
        Args:
            config: Provider-specific configuration dictionary
        """
        self.config = config
        self.enabled = config.get('enabled', False)
    
    @abstractmethod
    def send(self, event_type: str, data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Send a notification.
        
        Args:
            event_type: Type of event (job_started, job_completed, etc.)
            data: Event data dictionary
        
        Returns:
            Tuple of (success, error_message)
        """
        pass
    
    @abstractmethod
    def test_connection(self) -> Tuple[bool, str]:
        """
        Test if the provider is configured correctly.
        
        Returns:
            Tuple of (success, message)
        """
        pass
    
    def is_enabled(self) -> bool:
        """Check if this provider is enabled."""
        return self.enabled
    
    def format_event_title(self, event_type: str, data: Dict[str, Any]) -> str:
        """Format event title for notifications."""
        job_name = data.get('job_name', 'Unknown Job')
        
        titles = {
            'job_started': f'🚀 Backup Started: {job_name}',
            'job_completed': f'✅ Backup Completed: {job_name}',
            'job_failed': f'❌ Backup Failed: {job_name}',
            'tape_needed': f'📼 Tape Needed: {job_name}',
            'restore_started': f'🔄 Restore Started: {job_name}',
            'restore_completed': f'✅ Restore Completed: {job_name}',
            'restore_failed': f'❌ Restore Failed: {job_name}',
        }
        
        return titles.get(event_type, f'Backup Event: {job_name}')
    
    def format_event_message(self, event_type: str, data: Dict[str, Any]) -> str:
        """Format event message for notifications."""
        lines = []
        
        # Job info
        if 'job_name' in data:
            lines.append(f"Job: {data['job_name']}")
        if 'job_id' in data:
            lines.append(f"Job ID: {data['job_id']}")
        
        # Status
        if 'status' in data:
            lines.append(f"Status: {data['status']}")
        
        # Error details
        if 'error' in data:
            lines.append(f"Error: {data['error']}")
        
        # Duration
        if 'duration' in data:
            lines.append(f"Duration: {data['duration']}")
        
        # Tape info
        if 'tape_barcode' in data:
            lines.append(f"Tape: {data['tape_barcode']}")
        
        # Timestamp
        if 'timestamp' in data:
            lines.append(f"Time: {data['timestamp']}")
        
        return '\n'.join(lines)
