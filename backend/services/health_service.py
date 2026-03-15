
import logging
from typing import Dict, List
from backend.database import Database

logger = logging.getLogger(__name__)

class HealthService:
    """
    Service for calculating predictive health scores for appliance hardware. (FS-02)
    """
    
    def __init__(self, db: Database):
        self.db = db
        # Severity weights for scoring
        self.weights = {
            'critical': 25,
            'warning': 10,
            'info': 2
        }

    def calculate_drive_health_score(self, drive_id: str, days: int = 30) -> Dict:
        """
        Calculate a health score (0-100) based on historical TapeAlerts.
        """
        try:
            alerts = self.db.get_drive_alert_history(drive_id, days=days)
            
            base_score = 100.0
            deductions = 0.0
            
            # Count alerts by severity
            stats = {'critical': 0, 'warning': 0, 'info': 0, 'total': len(alerts)}
            
            for alert in alerts:
                severity = alert.get('severity', 'info').lower()
                stats[severity if severity in stats else 'info'] += 1
                
                # Apply weight
                deductions += self.weights.get(severity, self.weights['info'])
            
            # Cap deductions to 100
            final_score = max(0.0, base_score - deductions)
            
            # Determine health status
            status = 'healthy'
            if final_score < 50:
                status = 'critical'
            elif final_score < 85:
                status = 'degraded'
                
            return {
                'drive_id': drive_id,
                'score': round(final_score, 1),
                'status': status,
                'alert_stats': stats,
                'period_days': days
            }
            
        except Exception as e:
            logger.error(f"Failed to calculate drive health for {drive_id}: {e}")
            return {
                'drive_id': drive_id,
                'score': 0,
                'status': 'unknown',
                'error': str(e)
            }

    def get_all_drives_health(self, drive_ids: List[str]) -> List[Dict]:
        """Get health scores for a list of drives."""
        return [self.calculate_drive_health_score(d) for d in drive_ids]
