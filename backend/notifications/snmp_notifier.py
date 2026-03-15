"""
SNMP Trap notification provider.
"""
from typing import Dict, Any, Tuple, Optional
from pysnmp.hlapi import (
    SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
    NotificationType, ObjectIdentity, OctetString
)
from backend.notifications.base import NotificationProvider

class SnmpNotifier(NotificationProvider):
    """
    SNMP Trap notifier.
    Sends SNMP v2c traps to a configured manager.
    """
    
    # OID Definitions based on FOSSILSAFE-MIB
    ENTERPRISE_OID = '1.3.6.1.4.1.99999'
    EVENTS_OID = f'{ENTERPRISE_OID}.1'
    OBJECTS_OID = f'{ENTERPRISE_OID}.2'
    
    TRAP_OIDS = {
        'job_completed': f'{EVENTS_OID}.1',
        'job_failed': f'{EVENTS_OID}.2',
        'tape_needed': f'{EVENTS_OID}.3',
        'default': f'{EVENTS_OID}.99'
    }
    
    VAR_OIDS = {
        'message': f'{OBJECTS_OID}.1',
        'job_id': f'{OBJECTS_OID}.2'
    }

    def send(self, event_type: str, data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Send SNMP trap."""
        if not self.is_enabled():
            return False, "SNMP notifier is disabled"
            
        try:
            target_ip = self.config.get('target_ip')
            target_port = int(self.config.get('target_port', 162))
            community = self.config.get('community', 'public')
            
            if not target_ip:
                return False, "Target IP not configured"

            # Determine Trap OID
            trap_oid = self.TRAP_OIDS.get(event_type, self.TRAP_OIDS['default'])
            
            # Prepare Variable Bindings
            message = self.format_event_message(event_type, data)
            job_id = str(data.get('job_id', ''))
            
            var_binds = [
                (ObjectIdentity(self.VAR_OIDS['message']), OctetString(message)),
            ]
            
            if job_id:
                var_binds.append((ObjectIdentity(self.VAR_OIDS['job_id']), OctetString(job_id)))

            # Send Trap
            iterator = getattr(NotificationType(ObjectIdentity(trap_oid)), 'addVarBinds')(*var_binds)
            
            errorIndication, errorStatus, errorIndex, varBinds = next(
                NotificationType(
                    ObjectIdentity(trap_oid)
                ).addVarBinds(
                    *var_binds
                ).sendNotification(
                    SnmpEngine(),
                    CommunityData(community),
                    UdpTransportTarget((target_ip, target_port)),
                    ContextData()
                )
            )
            
            if errorIndication:
                return False, str(errorIndication)
            
            return True, None

        except Exception as e:
            return False, f"Failed to send SNMP trap: {str(e)}"

    def test_connection(self) -> Tuple[bool, str]:
        """Test SNMP configuration by sending a generic test trap."""
        try:
            target_ip = self.config.get('target_ip')
            if not target_ip:
                return False, "Target IP not configured"
                
            success, error = self.send('default', {'status': 'Test Trap', 'message': 'FossilSafe SNMP Test'})
            
            if success:
                return True, "Test trap sent successfully"
            else:
                return False, f"Failed to send test trap: {error}"
                
        except Exception as e:
            return False, f"Test failed: {str(e)}"
