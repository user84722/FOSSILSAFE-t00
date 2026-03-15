#!/usr/bin/env python3
"""
FossilSafe Backup Scheduler
Handles scheduled backup jobs using APScheduler.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.base import JobLookupError
import json
import json

logger = logging.getLogger(__name__)


class BackupScheduler:
    """
    Manages scheduled backup jobs.
    Supports cron-style schedules and interval-based schedules.
    """
    
    def __init__(self, db, backup_engine, verification_service=None):
        self.db = db
        self.backup_engine = backup_engine
        self.verification_service = verification_service
        
        self.scheduler = BackgroundScheduler(
            timezone=timezone.utc,
            job_defaults={
                'coalesce': True,  # Combine missed runs
                'max_instances': 1,  # Only one instance per job
                'misfire_grace_time': 3600  # 1 hour grace period
            }
        )
        
        self._lock = threading.Lock()
        self._execution_lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._stopped = False
        self._loaded_schedules: Dict[int, str] = {}  # schedule_id -> apscheduler_job_id

    @staticmethod
    def validate_cron_expression(expression: str) -> tuple:
        """
        Validate a cron expression.
        Expects 6 fields: second minute hour day month day_of_week
        Returns (valid, error_message).
        """
        parts = expression.strip().split()
        if len(parts) != 6:
            return False, f"Cron expression must have 6 fields, got {len(parts)}"
        
        field_names = ['second', 'minute', 'hour', 'day', 'month', 'day_of_week']
        field_ranges = [
            (0, 59),  # second
            (0, 59),  # minute
            (0, 23),  # hour
            (1, 31),  # day
            (1, 12),  # month
            (0, 6),   # day_of_week (0=Sunday)
        ]
        
        for i, (part, name, (min_val, max_val)) in enumerate(zip(parts, field_names, field_ranges)):
            if part == '*':
                continue
            # Handle ranges like "1-5" or lists like "1,3,5"
            for segment in part.replace('-', ',').split(','):
                if not segment:
                    continue
                try:
                    val = int(segment)
                    if val < min_val or val > max_val:
                        return False, f"Invalid {name}: {val} (must be {min_val}-{max_val})"
                except ValueError:
                    # Could be a valid expression like "MON" for day_of_week, skip for now
                    pass
        
        # Final validation with APScheduler
        try:
            CronTrigger.from_crontab(' '.join(parts[1:]))  # APScheduler uses 5-field format (no seconds)
        except Exception as e:
            # Try the full 6-field version
            try:
                CronTrigger(
                    second=parts[0],
                    minute=parts[1],
                    hour=parts[2],
                    day=parts[3],
                    month=parts[4],
                    day_of_week=parts[5]
                )
            except Exception as e2:
                return False, f"Invalid cron expression: {e2}"
        
        return True, None

    def start(self):
        """Start the scheduler if not already running."""
        with self._stop_lock:
            if self._stopped:
                self._stopped = False
            if not self.scheduler.running:
                self.scheduler.start()
                logger.info("BackupScheduler started")
                self._load_schedules()
                self._schedule_system_tasks()

    def stop(self):
        """Stop the scheduler gracefully. Idempotent."""
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
            if self.scheduler.running:
                try:
                    self.scheduler.shutdown(wait=False)
                    logger.info("BackupScheduler stopped")
                except Exception as e:
                    logger.warning(f"Error during scheduler shutdown: {e}")

    def is_running(self) -> bool:
        """Check if the scheduler is running."""
        with self._stop_lock:
            return not self._stopped and self.scheduler.running

    def _load_schedules(self):
        """Load schedules from database into APScheduler."""
        with self._lock:
            schedules = self.db.get_schedules()
            for schedule in schedules:
                if schedule.get('enabled'):
                    self._add_schedule_job(schedule)

    def _add_schedule_job(self, schedule):
        """Add a single schedule to APScheduler."""
        schedule_id = schedule.get('id')
        cron_expr = schedule.get('cron_expression')
        if not cron_expr:
            return
        
        valid, error = self.validate_cron_expression(cron_expr)
        if not valid:
            logger.warning(f"Invalid cron expression for schedule {schedule_id}: {error}")
            return
        
        parts = cron_expr.strip().split()
        try:
            trigger = CronTrigger(
                second=parts[0],
                minute=parts[1],
                hour=parts[2],
                day=parts[3],
                month=parts[4],
                day_of_week=parts[5]
            )
            job_id = f"schedule_{schedule_id}"
            self.scheduler.add_job(
                self._execute_scheduled_backup,
                trigger,
                args=[schedule_id],
                id=job_id,
                replace_existing=True
            )
            self._loaded_schedules[schedule_id] = job_id
            logger.info(f"Loaded schedule {schedule_id} with job {job_id}")
        except Exception as e:
            logger.error(f"Failed to add schedule {schedule_id}: {e}")

    def _execute_scheduled_backup(self, schedule_id: int):
        """Execute a scheduled job (backup or verification)"""
        logger.info(f"Executing scheduled job {schedule_id}")
        
        try:
            # Get schedule details
            schedule = self.db.get_schedule(schedule_id)
            if not schedule:
                logger.error(f"Schedule {schedule_id} not found")
                return
            
            if not schedule.get('enabled', False):
                logger.info(f"Schedule {schedule_id} is disabled, skipping")
                return

            schedule_type = schedule.get('schedule_type', 'backup')

            with self._execution_lock:
                # Update last run time
                self.db.update_schedule_last_run(schedule_id)

                # Check if there's already a running job for this schedule
                active_jobs = self.db.get_active_jobs()
                for job in active_jobs:
                    if job.get('schedule_id') == schedule_id:
                        logger.warning(f"Job for schedule {schedule_id} already running, skipping")
                        return

            # Parse tapes list
            tapes = schedule.get('tapes', [])
            if isinstance(tapes, str):
                tapes = json.loads(tapes)

            if schedule_type == 'verification':
                if not self.verification_service:
                    logger.error("Verification service not available")
                    return
                
                job_name = f"Verify: {schedule.get('name', schedule_id)}"
                job_id = self.db.create_job(
                    name=job_name,
                    job_type='verification',
                    tapes=tapes,
                    schedule_id=schedule_id,
                    verify=True # Explicitly verifying
                )
                logger.info(f"Starting scheduled verification job {job_id}")
                self.verification_service.start_verification_job(tapes, job_id)
                return

            # Default: Backup Job
            job_name = f"Scheduled: {schedule.get('name', schedule_id)}"
            source_id = schedule.get('source_id')
            if not source_id:
                logger.error(f"Schedule {schedule_id} missing source_id")
                return
            
            # ... (rest of backup logic) ...
            
            drive = schedule.get('drive', 0)
            target_drive = drive

            # Auto-drive selection
            if drive == -1:
                target_drive = -1
            else:
                target_drive = drive
            
            job_id = self.db.create_job(
                name=job_name,
                source_id=source_id,
                tapes=tapes,
                verify=self.db.get_bool_setting('verification_enabled', True),
                duplicate=schedule.get('duplicate', False),
                schedule_id=schedule_id,
                drive=target_drive,
                compression=schedule.get('compression', 'none'),
                backup_mode=schedule.get('backup_mode', 'full')
            )
            
            # Run preflight check
            job = self.db.get_job(job_id)
            success, issues = self.backup_engine.preflight_check(job)
            if not success:
                logger.error(f"Preflight check failed for schedule {schedule_id}: {issues}")
                self.db.log_entry('error', 'schedule', 
                    f"Scheduled backup {schedule_id} failed preflight: {', '.join(issues)}")
                self.db.update_job_status(job_id, 'failed')
                return
            
            logger.info(f"Starting scheduled job {job_id} for schedule {schedule_id}")
            
            # Start in separate thread
            import threading
            threading.Thread(
                target=self.backup_engine.start_backup_job, 
                args=(job_id,),
                daemon=True
            ).start()
            
        except Exception as e:
            logger.exception(f"Failed to execute scheduled backup {schedule_id}")
            self.db.log_entry('error', 'schedule', 
                f"Scheduled backup {schedule_id} failed: {e}")
    
    def get_next_run(self, schedule_id: int) -> Optional[datetime]:
        """Get the next scheduled run time for a schedule"""
        with self._lock:
            if schedule_id not in self._loaded_schedules:
                return None
            
            try:
                job = self.scheduler.get_job(self._loaded_schedules[schedule_id])
                if job and job.next_run_time:
                    return job.next_run_time
                return None
                
            except Exception:
                return None

    def get_next_scheduled(self) -> Optional[dict]:
        """Get the next scheduled job across all schedules."""
        next_job = None
        next_time = None

        with self._lock:
            for schedule_id, job_id in self._loaded_schedules.items():
                try:
                    job = self.scheduler.get_job(job_id)
                    if not job or not job.next_run_time:
                        continue
                    if next_time is None or job.next_run_time < next_time:
                        next_time = job.next_run_time
                        next_job = self.db.get_schedule(schedule_id)
                except Exception:
                    continue

        if not next_job:
            return None

        return {
            'id': next_job.get('id'),
            'name': next_job.get('name'),
            'next_run': next_time.isoformat() if next_time else None
        }
    
    def get_all_schedules_status(self) -> List[dict]:
        """Get status of all schedules"""
        schedules = self.db.get_schedules()
        result = []
        
        for schedule in schedules:
            schedule_id = schedule.get('id')
            status = {
                **schedule,
                'loaded': schedule_id in self._loaded_schedules,
                'next_run': None
            }
            
            next_run = self.get_next_run(schedule_id)
            if next_run:
                status['next_run'] = next_run.isoformat()
            
            result.append(status)
        
        return result
    
    def run_now(self, schedule_id: int) -> bool:
        """Run a schedule immediately"""
        try:
            self._execute_scheduled_backup(schedule_id)
            return True
        except Exception as e:
            logger.error(f"Failed to run schedule {schedule_id}: {e}")
            return False

    def _schedule_system_tasks(self):
        """Schedule periodic system tasks like catalog sync."""
        interval_hours = int(self.db.get_setting('catalog_sync_interval_hours', 24))
        if interval_hours > 0:
            logger.info(f"Scheduling periodic catalog sync every {interval_hours} hours")
            self.scheduler.add_job(
                self._execute_catalog_sync,
                'interval',
                hours=interval_hours,
                id='system_catalog_sync',
                replace_existing=True
            )

    def _execute_catalog_sync(self):
        """Execute periodic catalog sync to cloud."""
        from backend.external_catalog_backup import ExternalCatalogBackup
        from backend.tape_controller import TapeLibraryController
        
        logger.info("Starting periodic catalog sync")
        try:
            # We don't need a tape for this sync
            # Tape controller is still required for instance initialization usually
            # but sync_without_tape doesn't use it.
            # Passing None or a mock if needed, but ExternalCatalogBackup uses it in __init__
            
            # Find a tape controller if available, otherwise use a dummy
            tape_controller = self.backup_engine.tape_controller
            
            backup_service = ExternalCatalogBackup(self.db, tape_controller)
            success, message, results = backup_service.sync_without_tape()
            
            if success:
                logger.info(f"Periodic catalog sync complete: {message} ({', '.join(results)})")
                self.db.log_entry('info', 'system', f"Periodic catalog sync complete: {', '.join(results)}")
            else:
                logger.error(f"Periodic catalog sync failed: {message}")
                self.db.log_entry('error', 'system', f"Periodic catalog sync failed: {message}")
                
        except Exception as e:
            logger.exception("Failed in periodic catalog sync")
            self.db.log_entry('error', 'system', f"Periodic catalog sync error: {e}")
