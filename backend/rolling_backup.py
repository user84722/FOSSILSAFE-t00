#!/usr/bin/env python3
"""
Rolling Backup Engine (GFS-style retention).
Currently provides status reporting for UI/diagnostics.
"""

from typing import Dict


class RollingBackupEngine:
    def __init__(self, db, backup_engine=None):
        self.db = db
        self.backup_engine = backup_engine

    def get_rotation_status(self) -> Dict:
        """
        Return the current rotation status.

        This is a conservative implementation that reports configured settings
        and last completed job without performing destructive actions.
        """
        return {
            'enabled': self.db.get_bool_setting('rolling_backup_enabled', False),
            'daily_retention': self.db.get_setting('rolling_daily_retention', 7),
            'weekly_retention': self.db.get_setting('rolling_weekly_retention', 4),
            'monthly_retention': self.db.get_setting('rolling_monthly_retention', 12),
            'last_completed_job': self.db.get_last_completed_job(),
            'status': 'ready'
        }
