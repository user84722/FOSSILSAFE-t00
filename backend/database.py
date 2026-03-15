#!/usr/bin/env python3
"""
Database Module - SQLite database for tracking jobs, tapes, and archived files
"""

import sqlite3
import json
import os
import re
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional
import threading
import queue
import time

logger = logging.getLogger(__name__)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json_loads(data, default=None):
    """Safely load JSON with fallback."""
    if default is None:
        default = []
    if not data:
        return default
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return default


class ConnectionPool:
    """
    Thread-safe connection pool for SQLite.
    Each thread gets its own connection to avoid threading issues.
    """
    
    def __init__(self, db_path: str, pool_size: int = 30, timeout: int = 5):
        self.db_path = db_path
        self.pool_size = pool_size
        self.timeout = timeout
        self._pool = queue.Queue(maxsize=pool_size)
        self._all_connections = []
        self._lock = threading.Lock()
        self._local = threading.local()
        self._closed = False
        
        logger.info(f"Initialized connection pool: size={pool_size}, timeout={timeout}s")
    
    def _create_connection(self) -> sqlite3.Connection:
        """Create a new database connection with optimal settings."""
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30.0
        )
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrency
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA busy_timeout=30000')
        
        # Track connection (no lock needed for append to list)
        self._all_connections.append(conn)
        
        return conn
    
    def get_connection(self) -> sqlite3.Connection:
        """
        Get a connection for the current thread.
        Uses thread-local storage to cache one connection per thread/request.
        MUST call return_connection() to clear this cache and return to pool.
        """
        if self._closed:
            raise RuntimeError("Connection pool is closed")

        # Reuse existing connection if this thread already has one
        if hasattr(self._local, 'conn') and self._local.conn is not None:
            try:
                self._local.conn.execute('SELECT 1')
                return self._local.conn
            except sqlite3.Error:
                # Dead — remove from pool tracking and clear local
                conn = self._local.conn
                with self._lock:
                    if conn in self._all_connections:
                        self._all_connections.remove(conn)
                self._local.conn = None

        # Check out from pool queue
        conn = None
        try:
            conn = self._pool.get(block=False)
            try:
                conn.execute('SELECT 1')
            except sqlite3.Error:
                # Dead
                with self._lock:
                    if conn in self._all_connections:
                        self._all_connections.remove(conn)
                conn = None
        except queue.Empty:
            pass

        # Create new if needed and possible
        if conn is None:
            with self._lock:
                if len(self._all_connections) < self.pool_size:
                    conn = self._create_connection()

        # Wait if still no connection
        if conn is None:
            try:
                conn = self._pool.get(timeout=self.timeout)
                try:
                    conn.execute('SELECT 1')
                except sqlite3.Error:
                    conn = self._create_connection()
            except queue.Empty:
                raise TimeoutError(f"Could not get database connection within {self.timeout}s")

        self._local.conn = conn
        return conn
    
    def _migrate_logs_schema(self, conn):
        """Ensure logs table has hash columns."""
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(logs)")
            columns = [info[1] for info in cursor.fetchall()]
            
            if 'hash' not in columns:
                logger.info("Migrating logs table: adding hash column")
                cursor.execute("ALTER TABLE logs ADD COLUMN hash TEXT")
                
            if 'previous_hash' not in columns:
                logger.info("Migrating logs table: adding previous_hash column")
                cursor.execute("ALTER TABLE logs ADD COLUMN previous_hash TEXT")
                
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to migrate logs schema: {e}")

    def return_connection(self, conn: sqlite3.Connection):
        """Return a connection to the pool."""
        if self._closed or conn is None:
            return
        # Clear any stale thread-local reference
        if hasattr(self._local, 'conn') and self._local.conn is conn:
            self._local.conn = None
        # Return to the queue; if somehow full, close the excess connection
        try:
            self._pool.put(conn, block=False)
        except queue.Full:
            try:
                conn.close()
                with self._lock:
                    if conn in self._all_connections:
                        self._all_connections.remove(conn)
            except Exception:
                pass
    
    def close_all(self):
        """Close all connections in the pool."""
        self._closed = True
        
        with self._lock:
            for conn in self._all_connections:
                try:
                    conn.close()
                except Exception as e:
                    logger.warning(f"Error closing connection: {e}")
            self._all_connections.clear()
        
        # Clear the queue
        while not self._pool.empty():
            try:
                self._pool.get(block=False)
            except queue.Empty:
                break
        
        logger.info("Connection pool closed")


class Database:
    """SQLite database manager for LTO backup system"""
    
    def __init__(self, db_path: str = 'lto_backup.db', pool_size: int = 30, pool_timeout: int = 5):
        self.db_path = db_path
        self.pool = ConnectionPool(db_path, pool_size, pool_timeout)
        self._lock = threading.Lock()
        self._initialize_database()
    
    def _get_conn(self):
        """Get a database connection from the pool."""
        return self.pool.get_connection()
    
    def _create_connection(self):
        """Create a new database connection (for backward compatibility)."""
        return self.pool._create_connection()
    
    def close(self):
        """Close all database connections in the pool."""
        self.pool.close_all()

    def release_connection(self):
        """
        Return the current thread's connection to the pool.
        Should be called at the end of a request or task.
        """
        # Get current connection from thread local storage without creating new one
        if hasattr(self.pool._local, 'conn') and self.pool._local.conn:
            self.pool.return_connection(self.pool._local.conn)

    def execute(self, sql: str, parameters: tuple = ()):
        """Execute a SQL query and return the cursor."""
        conn = self._get_conn()
        return conn.execute(sql, parameters)

    def commit(self):
        """Commit the current transaction."""
        conn = self._get_conn()
        conn.commit()
    
    def _initialize_database(self):
        """Create database tables if they don't exist"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # Tape inventory table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tapes (
                barcode TEXT PRIMARY KEY,
                generation TEXT,
                status TEXT,
                slot INTEGER,
                capacity_bytes INTEGER,
                used_bytes INTEGER,
                write_count INTEGER DEFAULT 0,
                read_count INTEGER DEFAULT 0,
                mount_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                trust_status TEXT DEFAULT 'unknown',
                alias TEXT,
                reserved_by_job INTEGER,
                last_write TIMESTAMP,
                worm_lock BOOLEAN DEFAULT 0,
                retention_expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self._ensure_tape_columns(cursor)
        
        # Audit log table (Immutable append-only trail)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                username TEXT,
                action TEXT NOT NULL,
                message TEXT,
                detail TEXT,
                level TEXT DEFAULT 'info',
                category TEXT DEFAULT 'system',
                prev_hash TEXT,
                hash TEXT
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp DESC)')
        
        # Audit verification history
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_verification_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                valid BOOLEAN,
                total_entries INTEGER,
                verified_until_id INTEGER,
                error_message TEXT
            )
        ''')
        
        # Backup jobs table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                job_type TEXT DEFAULT 'backup',
                smb_path TEXT,
                schedule_id INTEGER,
                source_job_id INTEGER,
                username TEXT,
                credential_name TEXT,
                tapes TEXT,
                tapes_needed INTEGER,
                verify BOOLEAN DEFAULT 1,
                duplicate BOOLEAN DEFAULT 0,
                drive INTEGER DEFAULT 0,
                compression TEXT DEFAULT 'none',
                encryption TEXT DEFAULT 'none',
                backup_mode TEXT DEFAULT 'full',
                backup_set_id TEXT,
                backup_type TEXT,
                status TEXT DEFAULT 'pending',
                status_message TEXT,
                progress_state TEXT,
                progress_message TEXT,
                error TEXT,
                total_files INTEGER,
                total_size INTEGER,
                plan_total_files INTEGER,
                plan_total_size INTEGER,
                plan_skipped_files INTEGER,
                plan_skipped_size INTEGER,
                files_written INTEGER DEFAULT 0,
                bytes_written INTEGER DEFAULT 0,
                scheduled_time TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                updated_at TIMESTAMP,
                archival_policy TEXT DEFAULT 'none',
                source_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC)')

        # Job logs table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS job_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_job_logs_job_id ON job_logs(job_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_job_logs_timestamp ON job_logs(timestamp DESC)')

        # Sources table
        self._ensure_sources_table()

        # Migrate existing jobs table (add new columns if they don't exist)
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN status_message TEXT")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN progress_state TEXT")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN progress_message TEXT")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN updated_at TIMESTAMP")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN schedule_id INTEGER")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN drive INTEGER DEFAULT 0")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN compression TEXT DEFAULT 'none'")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN backup_mode TEXT DEFAULT 'full'")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN backup_set_id TEXT")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN source_id TEXT")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN archival_policy TEXT DEFAULT 'none'")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN plan_total_files INTEGER")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN plan_total_size INTEGER")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN plan_skipped_files INTEGER")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN plan_skipped_size INTEGER")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE tapes ADD COLUMN alias TEXT")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE tapes ADD COLUMN is_cleaning_tape BOOLEAN DEFAULT 0")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE tapes ADD COLUMN cleaning_remaining_uses INTEGER")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE tapes ADD COLUMN worm_lock BOOLEAN DEFAULT 0")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE tapes ADD COLUMN retention_expires_at TIMESTAMP")
        except sqlite3.Error:
            pass
        
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN encryption TEXT DEFAULT 'none'")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN feeder_rate_bps REAL")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN ingest_rate_bps REAL")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN buffer_health REAL")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN pre_job_hook TEXT")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN post_job_hook TEXT")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE schedules ADD COLUMN encryption TEXT DEFAULT 'none'")
        except sqlite3.Error:
            pass
        
        # Health check results table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS health_check_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                total_files INTEGER,
                checked_files INTEGER,
                failed_files INTEGER,
                failures TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            )
        ''')
        
        # Archived files table with full indexing
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS archived_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                tape_barcode TEXT,
                file_path TEXT NOT NULL,
                file_name TEXT,
                file_extension TEXT,
                file_size INTEGER,
                file_mtime TIMESTAMP,
                checksum TEXT,
                file_path_on_tape TEXT,
                tape_position INTEGER,
                copy_set_id TEXT,
                copy_type TEXT,
                backup_set_id TEXT,
                is_encrypted BOOLEAN DEFAULT 0,
                archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES jobs(id),
                FOREIGN KEY (tape_barcode) REFERENCES tapes(barcode)
            )
        ''')
        
        # Webhooks table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                name TEXT,
                event_types TEXT,
                secret TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Add columns if migrating
        try:
            cursor.execute('ALTER TABLE webhooks ADD COLUMN name TEXT')
        except Exception:
            pass
        
        # Migrate existing tables (add new columns if they don't exist)
        try:
            cursor.execute("ALTER TABLE archived_files ADD COLUMN file_path_on_tape TEXT")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE archived_files ADD COLUMN tape_position INTEGER")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE archived_files ADD COLUMN copy_set_id TEXT")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE archived_files ADD COLUMN copy_type TEXT")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE archived_files ADD COLUMN file_mtime TIMESTAMP")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE archived_files ADD COLUMN backup_set_id TEXT")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE archived_files ADD COLUMN is_encrypted BOOLEAN DEFAULT 0")
        except sqlite3.Error:
            pass
        
        # Create comprehensive indexes for fast searching
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_archived_files_path 
            ON archived_files(file_path)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_archived_files_name
            ON archived_files(file_name)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_archived_files_extension
            ON archived_files(file_extension)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_archived_files_tape 
            ON archived_files(tape_barcode)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_archived_files_job
            ON archived_files(job_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_archived_files_copy_set
            ON archived_files(copy_set_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_archived_files_position
            ON archived_files(tape_position)
        ''')
        
        cursor.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS archived_files_fts 
            USING fts5(file_path, file_name, content=archived_files, content_rowid=id)
        ''')
        
        # Triggers to keep FTS index updated
        cursor.execute('''
            CREATE TRIGGER IF NOT EXISTS archived_files_ai AFTER INSERT ON archived_files BEGIN
              INSERT INTO archived_files_fts(rowid, file_path, file_name)
              VALUES (new.id, new.file_path, new.file_name);
            END
        ''')
        
        cursor.execute('''
            CREATE TRIGGER IF NOT EXISTS archived_files_ad AFTER DELETE ON archived_files BEGIN
              INSERT INTO archived_files_fts(archived_files_fts, rowid, file_path, file_name)
              VALUES('delete', old.id, old.file_path, old.file_name);
            END
        ''')
        
        cursor.execute('''
            CREATE TRIGGER IF NOT EXISTS archived_files_au AFTER UPDATE ON archived_files BEGIN
              INSERT INTO archived_files_fts(archived_files_fts, rowid, file_path, file_name)
              VALUES('delete', old.id, old.file_path, old.file_name);
              INSERT INTO archived_files_fts(rowid, file_path, file_name)
              VALUES (new.id, new.file_path, new.file_name);
            END
        ''')

        # Backup set tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backup_sets (
                id TEXT PRIMARY KEY,
                sources TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Diagnostics reports table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS diagnostics_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                overall_status TEXT,
                report_json_path TEXT,
                report_text_path TEXT,
                summary TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backup_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                backup_set_id TEXT NOT NULL,
                job_id INTEGER NOT NULL,
                manifest_path TEXT NOT NULL,
                total_files INTEGER,
                total_bytes INTEGER,
                tape_map TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (backup_set_id) REFERENCES backup_sets(id),
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_backup_snapshots_set
            ON backup_snapshots(backup_set_id, created_at)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_backup_snapshots_job
            ON backup_snapshots(job_id)
        ''')
        
        # Schedules table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                smb_path TEXT NOT NULL,
                credential_name TEXT,
                cron TEXT NOT NULL,
                tapes TEXT,
                verify BOOLEAN DEFAULT 1,
                compression TEXT DEFAULT 'zstd',
                duplicate BOOLEAN DEFAULT 0,
                drive INTEGER DEFAULT 0,
                backup_mode TEXT DEFAULT 'full',
                enabled BOOLEAN DEFAULT 1,
                source_config TEXT,
                last_run TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        try:
            cursor.execute("ALTER TABLE schedules ADD COLUMN source_config TEXT")
        except sqlite3.Error:
            pass

        try:
            cursor.execute("ALTER TABLE schedules ADD COLUMN schedule_type TEXT DEFAULT 'backup'")
        except sqlite3.Error:
            pass
            
        # Verification Reports table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS verification_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                tape_barcode TEXT,
                files_checked INTEGER DEFAULT 0,
                files_failed INTEGER DEFAULT 0,
                bytes_checked INTEGER DEFAULT 0,
                duration_seconds INTEGER DEFAULT 0,
                failure_details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_verification_reports_job ON verification_reports(job_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_verification_reports_tape ON verification_reports(tape_barcode)')
        
        # User Preferences table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                preference_key TEXT NOT NULL,
                preference_value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, preference_key)
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_preferences_user ON user_preferences(user_id)')
        
        try:
            cursor.execute("ALTER TABLE schedules ADD COLUMN drive INTEGER DEFAULT 0")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE schedules ADD COLUMN backup_mode TEXT DEFAULT 'full'")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE schedules ADD COLUMN source_id TEXT")
        except sqlite3.Error:
            pass
        
        # Restore jobs table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS restore_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                files TEXT NOT NULL,
                file_ids TEXT,
                destination TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                error TEXT,
                metadata TEXT,
                files_restored INTEGER DEFAULT 0,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Migrate existing restore_jobs table (add new columns if they don't exist)
        try:
            cursor.execute("ALTER TABLE restore_jobs ADD COLUMN file_ids TEXT")
        except sqlite3.Error:
            pass
        try:
            cursor.execute("ALTER TABLE restore_jobs ADD COLUMN metadata TEXT")
        except sqlite3.Error:
            pass
        
        # Settings table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Diagnostics reports table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS diagnostics_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                summary TEXT,
                report_json_path TEXT,
                report_text_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            )
        ''')
        
        # External catalog backups table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS external_catalog_backups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tape_barcode TEXT NOT NULL,
                created_at TIMESTAMP,
                backup_sets_count INTEGER,
                files_count INTEGER,
                bytes_total INTEGER
            )
        ''')
        
        # General logs table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                category TEXT,
                details TEXT,
                request_id TEXT,
                hash TEXT,
                previous_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)')
        
        # Audit verification history table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_verification_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                valid BOOLEAN NOT NULL,
                total_entries INTEGER,
                verified_count INTEGER,
                first_invalid_id INTEGER,
                error_message TEXT
            )
        ''')
        
        # Tape Alerts table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tape_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barcode TEXT NOT NULL,
                drive_id TEXT,
                alert_code INTEGER NOT NULL,
                alert_name TEXT,
                severity TEXT,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (barcode) REFERENCES tapes(barcode)
            )
        ''')
        # Migration: Add drive_id if it doesn't exist
        try:
            cursor.execute("ALTER TABLE tape_alerts ADD COLUMN drive_id TEXT")
        except sqlite3.Error:
            pass
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tape_alerts_barcode ON tape_alerts(barcode)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tape_alerts_drive ON tape_alerts(drive_id)')
        
        self._migrate_logs_schema(conn)
        
        self._ensure_search_indexes(cursor)
        
        conn.commit()

    def get_diagnostics_reports(self, limit: int = 20) -> List[Dict]:
        """Get historical diagnostic reports."""
        conn = self._get_conn()
        cursor = conn.execute('''
            SELECT * FROM diagnostics_reports
            ORDER BY created_at DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def delete_diagnostics_report(self, report_id: int):
        """Delete a diagnostics report record."""
        conn = self._get_conn()
        conn.execute('DELETE FROM diagnostics_reports WHERE id = ?', (report_id,))
        conn.commit()

    def save_audit_verification_result(self, result: Dict):
        """Save the result of an audit integrity check."""
        conn = self._get_conn()
        conn.execute('''
            INSERT INTO audit_verification_history 
            (valid, total_entries, verified_count, first_invalid_id, error_message)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            1 if result.get('valid') else 0,
            result.get('total_entries', 0),
            result.get('verified', 0),
            result.get('first_invalid_id'),
            result.get('error')
        ))
        conn.commit()

    def get_audit_verification_history(self, limit: int = 50) -> List[Dict]:
        """Get historical audit verification results."""
        conn = self._get_conn()
        cursor = conn.execute('''
            SELECT * FROM audit_verification_history
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def add_tape_alert(self, barcode: str, alert: Dict, drive_id: str = None):
        """Persist a TapeAlert for a specific tape and drive."""
        conn = self._get_conn()
        try:
            conn.execute('''
                INSERT INTO tape_alerts (barcode, drive_id, alert_code, alert_name, severity, message)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                barcode,
                drive_id,
                alert.get('code'),
                alert.get('name'),
                alert.get('severity'),
                alert.get('desc') or alert.get('message')
            ))
            conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to add tape alert: {e}")
            return False

    def get_drive_alert_history(self, drive_id: str, days: int = 30) -> List[Dict]:
        """Retrieve recent alerts for a specific drive to calculate health."""
        conn = self._get_conn()
        cursor = conn.execute('''
            SELECT * FROM tape_alerts 
            WHERE drive_id = ? 
            AND created_at >= datetime('now', ?)
            ORDER BY created_at DESC
        ''', (drive_id, f'-{days} days'))
        return [dict(row) for row in cursor.fetchall()]

    def get_tape_alert_history(self, barcode: str, days: int = 30) -> List[Dict]:
        """Retrieve recent alerts for a specific tape."""
        conn = self._get_conn()
        cursor = conn.execute('''
            SELECT * FROM tape_alerts 
            WHERE barcode = ? 
            AND created_at >= datetime('now', ?)
            ORDER BY created_at DESC
        ''', (barcode, f'-{days} days'))
        return [dict(row) for row in cursor.fetchall()]

    def get_last_tape_alerts(self, barcode: str, limit: int = 10) -> List[Dict]:
        """Retrieve recent alerts for a tape."""
        conn = self._get_conn()
        cursor = conn.execute('''
            SELECT * FROM tape_alerts 
            WHERE barcode = ? 
            ORDER BY created_at DESC 
            LIMIT ?
        ''', (barcode, limit))
        return [dict(row) for row in cursor.fetchall()]

    def add_log(self, entry: Dict):
        """Add a log entry to the database."""
        conn = self._get_conn()
        try:
            details = entry.get('details')
            if isinstance(details, (dict, list)):
                details = json.dumps(details)
            elif details and not isinstance(details, str):
                details = str(details)
                
            conn.execute('''
                INSERT INTO logs (timestamp, level, message, category, details, request_id, hash, previous_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                entry.get('timestamp', now_utc_iso()),
                entry.get('level', 'info'),
                entry.get('message', ''),
                entry.get('category', 'system'),
                details,
                entry.get('request_id'),
                entry.get('hash'),
                entry.get('previous_hash')
            ))
            conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to add log: {e}")
            return False

    def get_logs(self, limit: int = 100, offset: int = 0, level: Optional[str] = None) -> List[Dict]:
        """Retrieve logs from the database."""
        conn = self._get_conn()
        query = "SELECT * FROM logs"
        params = []
        
        if level:
            query += " WHERE level = ?"
            params.append(level)
            
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cursor = conn.execute(query, tuple(params))
        return [dict(row) for row in cursor.fetchall()]

    def log_entry(self, level: str, category: str, message: str, details: Optional[Dict] = None):
        """Helper to add log entry directly (compatibility for AuthManager)."""
        return self.add_log({
            'level': level,
            'category': category,
            'message': message,
            'details': details,
            'timestamp': now_utc_iso()
        })

        self._ensure_sources_table()
        self._migrate_credential_sources()
        self._migrate_job_source_ids()

    def _migrate_logs_schema(self, conn):
        """Ensure logs table has new columns."""
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(logs)")
        existing = {row['name'] for row in cursor.fetchall()}
        
        new_columns = {
            'category': 'TEXT',
            'details': 'TEXT',
            'request_id': 'TEXT',
            'hash': 'TEXT',
            'previous_hash': 'TEXT'
        }
        
        for col, dtype in new_columns.items():
            if col not in existing:
                try:
                    cursor.execute(f"ALTER TABLE logs ADD COLUMN {col} {dtype}")
                except sqlite3.OperationalError:
                    pass

    def _ensure_tape_columns(self, cursor):
        """Ensure newer tape metadata columns exist."""
        cursor.execute("PRAGMA table_info(tapes)")
        existing = {row[1] for row in cursor.fetchall()}
        columns = {
            "ltfs_formatted": "INTEGER DEFAULT 0",
            "ltfs_verified_at": "TEXT",
            "volume_name": "TEXT",
            "ltfs_present": "INTEGER",
            "initialized": "INTEGER DEFAULT 0",
            "read_count": "INTEGER DEFAULT 0",
            "mount_count": "INTEGER DEFAULT 0",
            "error_count": "INTEGER DEFAULT 0",
            "trust_status": "TEXT DEFAULT 'unknown'",
            "library_id": "TEXT",
            "worm_lock": "BOOLEAN DEFAULT 0",
            "retention_expires_at": "TIMESTAMP",
            "drive_index": "INTEGER",
            "location_type": "TEXT"
        }
        for column, definition in columns.items():
            if column not in existing:
                cursor.execute(f"ALTER TABLE tapes ADD COLUMN {column} {definition}")

    def _ensure_sources_table(self):
        """Ensure sources table exists."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_path TEXT,
                display_name TEXT,
                username TEXT,
                password_encrypted TEXT,
                domain TEXT,
                rsync_user TEXT,
                rsync_host TEXT,
                rsync_port INTEGER,
                rsync_key_ref TEXT,
                nfs_server TEXT,
                nfs_export TEXT,
                s3_bucket TEXT,
                s3_region TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('PRAGMA table_info(sources)')
        existing_columns = {row['name'] for row in cursor.fetchall()}
        optional_columns = {
            'display_name': 'TEXT',
            'username': 'TEXT',
            'password_encrypted': 'TEXT',
            'domain': 'TEXT',
            'rsync_user': 'TEXT',
            'rsync_host': 'TEXT',
            'rsync_port': 'INTEGER',
            'rsync_key_ref': 'TEXT',
            'nfs_server': 'TEXT',
            'nfs_export': 'TEXT',
            's3_bucket': 'TEXT',
            's3_region': 'TEXT',
        }
        for column, definition in optional_columns.items():
            if column not in existing_columns:
                cursor.execute(f'ALTER TABLE sources ADD COLUMN {column} {definition}')
        conn.commit()

    def _table_exists(self, table_name: str) -> bool:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        )
        return cursor.fetchone() is not None

    def _migrate_credential_sources(self):
        """Migrate legacy credentials into sources (idempotent)."""
        if self.get_setting('sources_migrated_v1'):
            return
        if not self._table_exists('credentials'):
            self.set_setting('sources_migrated_v1', True)
            return
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM sources')
        count_row = cursor.fetchone()
        if count_row and count_row['count']:
            self.set_setting('sources_migrated_v1', True)
            return
        cursor.execute('SELECT * FROM credentials')
        rows = cursor.fetchall()
        for row in rows:
            cred = dict(row)
            source_id = cred.get('name')
            if not source_id:
                continue
            cursor.execute('''
                INSERT OR IGNORE INTO sources (
                    id,
                    source_type,
                    source_path,
                    display_name,
                    username,
                    password_encrypted,
                    domain,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                source_id,
                cred.get('type') or 'smb',
                cred.get('smb_path') or '',
                cred.get('display_name') or '',
                cred.get('username') or '',
                cred.get('password_encrypted') or '',
                cred.get('domain') or '',
                cred.get('created_at') or now_utc_iso(),
                cred.get('updated_at') or now_utc_iso(),
            ))
        conn.commit()
        self.set_setting('sources_migrated_v1', True)

    def _migrate_job_source_ids(self):
        """Populate source_id from legacy credential_name."""
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE jobs SET source_id = credential_name "
                "WHERE source_id IS NULL AND credential_name IS NOT NULL AND credential_name != ''"
            )
        except sqlite3.Error:
            pass
        try:
            cursor.execute(
                "UPDATE schedules SET source_id = credential_name "
                "WHERE source_id IS NULL AND credential_name IS NOT NULL AND credential_name != ''"
            )
        except sqlite3.Error:
            pass
        conn.commit()
    
    # Tape operations
    def update_tape_inventory(self, tapes: List[Dict]):
        """Update tape inventory from library scan"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # Capacity by LTO generation (native, uncompressed)
        generation_capacities = {
            'LTO-5': 1500000000000,    # 1.5 TB
            'LTO-6': 2500000000000,    # 2.5 TB
            'LTO-7': 6000000000000,    # 6.0 TB
            'LTO-8': 12000000000000,   # 12.0 TB
            'LTO-9': 18000000000000,   # 18.0 TB
            'LTO-10': 36000000000000,  # 36.0 TB
        }
        
        for tape in tapes:
            if not tape.get('barcode'):
                continue
            generation = tape.get('generation', 'Unknown')
            capacity = generation_capacities.get(generation, 2500000000000)  # Default to LTO-6 size
            ltfs_formatted = 1 if tape.get("ltfs_formatted") else 0
            ltfs_verified_at = tape.get("ltfs_verified_at")
            volume_name = tape.get("volume_name")

            is_cleaning = 1 if tape.get('type') == 'cleaning' else 0
            # Default to 50 uses for new cleaning tapes if not specified
            initial_cleaning_uses = 50 if is_cleaning else None

            cursor.execute('''
                INSERT INTO tapes
                (barcode, generation, status, slot, capacity_bytes, used_bytes, write_count, alias, reserved_by_job, last_write,
                 ltfs_formatted, ltfs_verified_at, volume_name, ltfs_present, initialized, is_cleaning_tape, cleaning_remaining_uses, library_id,
                 drive_index, location_type, trust_status)
                VALUES (?, ?, ?, ?, ?, 0, 0, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(barcode) DO UPDATE SET
                    generation = excluded.generation,
                    status = excluded.status,
                    slot = excluded.slot,
                    capacity_bytes = COALESCE(tapes.capacity_bytes, excluded.capacity_bytes),
                    alias = COALESCE(excluded.alias, tapes.alias),
                    ltfs_formatted = COALESCE(tapes.ltfs_formatted, excluded.ltfs_formatted),
                    ltfs_verified_at = COALESCE(tapes.ltfs_verified_at, excluded.ltfs_verified_at),
                    volume_name = COALESCE(tapes.volume_name, excluded.volume_name),
                    ltfs_present = COALESCE(tapes.ltfs_present, excluded.ltfs_present),
                    initialized = COALESCE(tapes.initialized, excluded.initialized),
                    is_cleaning_tape = excluded.is_cleaning_tape,
                    cleaning_remaining_uses = COALESCE(tapes.cleaning_remaining_uses, excluded.cleaning_remaining_uses),
                    library_id = excluded.library_id,
                    drive_index = excluded.drive_index,
                    location_type = excluded.location_type,
                    trust_status = COALESCE(tapes.trust_status, excluded.trust_status)
            ''', (
                tape['barcode'],
                generation,
                tape.get('status', 'available'),
                tape.get('slot'),
                capacity,
                tape.get('alias'),
                ltfs_formatted,
                ltfs_verified_at,
                tape.get('volume_name', ''),
                tape.get('ltfs_present', 0),
                tape.get('initialized', 0),
                is_cleaning,
                tape.get('cleaning_remaining_uses', initial_cleaning_uses),
                tape.get('library_id'),
                tape.get('drive_index'),
                tape.get('location_type'),
                tape.get('trust_status', 'unknown')
            ))
        
        conn.commit()

    def decrement_cleaning_uses(self, barcode: str):
        """Decrement remaining uses for a cleaning tape."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE tapes 
            SET cleaning_remaining_uses = MAX(0, cleaning_remaining_uses - 1)
            WHERE barcode = ? AND is_cleaning_tape = 1
        ''', (barcode,))
        conn.commit()
    
    def get_tape_inventory(self) -> List[Dict]:
        """Get all tapes in inventory"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM tapes ORDER BY barcode')
        rows = cursor.fetchall()
        
        return [dict(row) for row in rows]
    
    def increment_tape_writes(self, barcode: str):
        """Increment write count for a tape"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE tapes 
            SET write_count = write_count + 1,
                last_write = CURRENT_TIMESTAMP
            WHERE barcode = ?
        ''', (barcode,))
        
        conn.commit()
    
    # Job operations
    def create_job(self, name: str, source_id: str = None,
                   tapes: List[str] = None, verify: bool = True, duplicate: bool = False,
                   scheduled_time: str = None, schedule_id: int = None, drive: int = 0,
                   job_type: str = 'backup', source_job_id: int = None,
                    compression: str = 'none', encryption: str = 'none',
                    backup_mode: str = 'full',
                    backup_set_id: Optional[str] = None,
                   archival_policy: str = 'none',
                   pre_job_hook: Optional[str] = None,
                   post_job_hook: Optional[str] = None,
                   source_path: str = None) -> int:
        """
        Create a new backup job.
        
        Sources are referenced by source_id OR provided directly via source_path.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO jobs
            (name, job_type, smb_path, tapes, verify, duplicate, scheduled_time, credential_name, schedule_id,
             drive, source_job_id, compression, encryption, backup_mode, backup_set_id, archival_policy, source_id,
             pre_job_hook, post_job_hook)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            name,
            job_type,
            source_path,
            json.dumps(tapes or []),
            verify,
            duplicate,
            scheduled_time,
            None,
            schedule_id,
            drive,
            source_job_id,
            compression,
            encryption,
            backup_mode,
            backup_set_id,
            archival_policy,
            source_id,
            pre_job_hook,
            post_job_hook,
        ))
        job_id = cursor.lastrowid
        conn.commit()
        return job_id
    
    def get_job(self, job_id: int) -> Optional[Dict]:
        """Get job by ID"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM jobs WHERE id = ?', (job_id,))
        row = cursor.fetchone()
        
        if row:
            job = dict(row)
            job['tapes'] = _safe_json_loads(job['tapes'])
            return job
        return None
    
    def get_all_jobs(self, limit: int = 100) -> List[Dict]:
        """Get all jobs"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM jobs 
            ORDER BY created_at DESC 
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        
        jobs = []
        for row in rows:
            job = dict(row)
            job['tapes'] = _safe_json_loads(job['tapes'])
            jobs.append(job)
        
        return jobs
    
    def get_jobs_by_name(self, name: str, limit: int = 10) -> List[Dict]:
        """Get jobs by name (e.g., to check for first-run)"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM jobs 
            WHERE name = ?
            ORDER BY created_at DESC 
            LIMIT ?
        ''', (name, limit))
        rows = cursor.fetchall()
        
        jobs = []
        for row in rows:
            job = dict(row)
            try:
                job['tapes'] = json.loads(job['tapes']) if job['tapes'] else []
            except (TypeError, ValueError, json.JSONDecodeError):
                job['tapes'] = []
            jobs.append(job)
        
        return jobs

    def get_active_jobs(self) -> List[Dict]:
        """Get all active jobs (running, pending, queued, paused)"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM jobs 
            WHERE status IN ('running', 'pending', 'queued', 'paused', 'blocked', 'cancel_requested')
            ORDER BY started_at DESC
        ''')
        rows = cursor.fetchall()
        
        jobs = []
        for row in rows:
            job = dict(row)
            try:
                job['tapes'] = json.loads(job['tapes']) if job['tapes'] else []
            except (TypeError, ValueError, json.JSONDecodeError):
                job['tapes'] = []
            jobs.append(job)
        
        return jobs
    
    def update_job_info(self, job_id: int, info: Dict):
        """Update job information"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        fields = ', '.join(f"{k} = ?" for k in info.keys())
        values = list(info.values()) + [job_id]
        
        cursor.execute(f'UPDATE jobs SET {fields} WHERE id = ?', values)
        conn.commit()
    
    def update_job_error(self, job_id: int, error: str):
        """Update job error message"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('UPDATE jobs SET error = ? WHERE id = ?', (error, job_id))
        conn.commit()
    
    # Archived files operations
    def add_archived_file(
        self,
        job_id: int,
        tape_barcode: str,
        file_path: str,
        file_size: int,
        checksum: str = None,
        file_path_on_tape: str = None,
        tape_position: int = None,
        copy_set_id: str = None,
        copy_type: str = None,
        archived_at: str = None,
    ):
        """Add an archived file record with full indexing"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # Extract file name and extension
        file_name = os.path.basename(file_path)
        file_extension = os.path.splitext(file_name)[1].lower() if '.' in file_name else ''
        
        base_fields = [
            job_id,
            tape_barcode,
            file_path,
            file_name,
            file_extension,
            file_size,
            checksum,
            file_path_on_tape,
            tape_position,
            copy_set_id,
            copy_type,
        ]

        if archived_at:
            cursor.execute('''
                INSERT INTO archived_files 
                (job_id, tape_barcode, file_path, file_name, file_extension, file_size, checksum,
                 file_path_on_tape, tape_position, copy_set_id, copy_type, archived_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (*base_fields, archived_at))
        else:
            cursor.execute('''
                INSERT INTO archived_files 
                (job_id, tape_barcode, file_path, file_name, file_extension, file_size, checksum,
                 file_path_on_tape, tape_position, copy_set_id, copy_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', base_fields)

        conn.commit()

    def batch_add_archived_files(self, records: List[Dict]):
        """
        Add multiple archived file records in a single transaction.
        Significantly faster than adding one by one.
        
        Args:
            records: List of dictionaries. Each dict must contain keys matching add_archived_file args:
                     job_id, tape_barcode, file_path, file_size, etc.
        """
        if not records:
            return

        conn = self._get_conn()
        cursor = conn.cursor()
        
        processed_records = []
        
        for r in records:
            file_path = r['file_path']
            file_name = os.path.basename(file_path)
            file_extension = os.path.splitext(file_name)[1].lower() if '.' in file_name else ''
            
            processed_records.append((
                r['job_id'],
                r['tape_barcode'],
                file_path,
                file_name,
                file_extension,
                r['file_size'],
                r.get('checksum'),
                r.get('file_path_on_tape'),
                r.get('tape_position'),
                r.get('copy_set_id'),
                r.get('copy_type'),
                r.get('archived_at') or now_utc_iso()
            ))

        try:
            cursor.executemany('''
                INSERT INTO archived_files 
                (job_id, tape_barcode, file_path, file_name, file_extension, file_size, checksum,
                 file_path_on_tape, tape_position, copy_set_id, copy_type, archived_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', processed_records)
            conn.commit()
        except Exception as e:
            logger.error(f"Batch insert failed: {e}")
            raise

    def delete_archived_files_for_tape(self, tape_barcode: str):
        """Delete catalog entries for a tape."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM archived_files WHERE tape_barcode = ?', (tape_barcode,))
        conn.commit()

    def rebuild_archived_files_fts(self):
        """Rebuild the archived_files FTS index."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM archived_files_fts')
        cursor.execute('''
            INSERT INTO archived_files_fts(rowid, file_path, file_name)
            SELECT id, file_path, file_name FROM archived_files
        ''')
        conn.commit()
    
    def search_archived_files(self, query: str, job_id: int = None, 
                              tape_barcode: str = None, extension: str = None) -> List[Dict]:
        """
        Advanced search for archived files with multiple filters
        Uses FTS (Full Text Search) for fast searching
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        params = []
        
        if query:
            # Sanitize query for FTS - escape special characters
            safe_query = re.sub(r'[^\w\s\-\.]', '', query)
            if not safe_query.strip():
                return []
            
            # Use FTS for text search with additional filters
            sql = '''
                SELECT af.* FROM archived_files af
                JOIN archived_files_fts fts ON af.id = fts.rowid
                WHERE archived_files_fts MATCH ?
            '''
            params.append(f'"{safe_query}"*')  # Prefix matching
            
            # Apply additional filters
            if job_id:
                sql += ' AND af.job_id = ?'
                params.append(job_id)
            
            if tape_barcode:
                sql += ' AND af.tape_barcode = ?'
                params.append(tape_barcode)
            
            if extension:
                sql += ' AND af.file_extension = ?'
                params.append(extension.lower())
            
            sql += ' ORDER BY rank LIMIT 1000'
            cursor.execute(sql, params)
        else:
            # Standard search with filters only
            sql = 'SELECT af.* FROM archived_files af WHERE 1=1'
            
            if job_id:
                sql += ' AND af.job_id = ?'
                params.append(job_id)
            
            if tape_barcode:
                sql += ' AND af.tape_barcode = ?'
                params.append(tape_barcode)
            
            if extension:
                sql += ' AND af.file_extension = ?'
                params.append(extension.lower())
            
            sql += ' ORDER BY af.archived_at DESC LIMIT 1000'
            cursor.execute(sql, params)
        
        rows = cursor.fetchall()
        
        # Pre-fetch job names and tape info to avoid N+1 queries
        job_ids = set(row['job_id'] for row in rows if row['job_id'])
        tape_barcodes = set(row['tape_barcode'] for row in rows if row['tape_barcode'])
        
        # Cache job names
        job_names = {}
        if job_ids:
            placeholders = ','.join('?' * len(job_ids))
            cursor.execute(f'SELECT id, name FROM jobs WHERE id IN ({placeholders})', list(job_ids))
            job_names = {row['id']: row['name'] for row in cursor.fetchall()}
        
        # Enrich results
        results = []
        for row in rows:
            file_info = dict(row)
            file_info['job_name'] = job_names.get(file_info['job_id'], 'Unknown')
            file_info['tape_alias'] = self._generate_tape_alias(file_info['tape_barcode']) if file_info['tape_barcode'] else 'Unknown'
            results.append(file_info)
        
        return results
    
    def search_archived_files_by_copy_set(self, copy_set_id: str) -> List[Dict]:
        """
        Find all files in a duplication copy set.
        
        Args:
            copy_set_id: UUID of the copy set
            
        Returns:
            List of all files (primary + duplicate)
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM archived_files
            WHERE copy_set_id = ?
            ORDER BY tape_barcode, file_path
        ''', (copy_set_id,))
        
        return [dict(row) for row in cursor.fetchall()]
    
    def get_archived_file_by_id(self, file_id: int) -> Optional[Dict]:
        """
        Get a single archived file by ID.
        
        Args:
            file_id: File record ID
            
        Returns:
            File record dict or None
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM archived_files WHERE id = ?', (file_id,))
        row = cursor.fetchone()
        
        return dict(row) if row else None
    
    def update_restore_job(self, restore_id: int, **kwargs):
        """
        Update restore job fields.
        
        Args:
            restore_id: Restore job ID
            **kwargs: Fields to update (metadata, files_restored, etc.)
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # Build update query
        fields = []
        values = []
        
        for key, value in kwargs.items():
            if key == 'metadata' and isinstance(value, dict):
                fields.append('metadata = ?')
                values.append(json.dumps(value))
            else:
                fields.append(f'{key} = ?')
                values.append(value)
        
        if not fields:
            return
        
        values.append(restore_id)
        
        sql = f"UPDATE restore_jobs SET {', '.join(fields)} WHERE id = ?"
        cursor.execute(sql, values)
        conn.commit()
    
    def update_job_progress(self, job_id: int, files_written: int = None, 
                           bytes_written: int = None, feeder_rate_bps: float = None,
                           ingest_rate_bps: float = None, buffer_health: float = None):
        """
        Update job progress counters.
        
        Args:
            job_id: Job ID
            files_written: Number of files written
            bytes_written: Number of bytes written
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        fields = []
        values = []
        
        if files_written is not None:
            fields.append('files_written = ?')
            values.append(files_written)
        
        if bytes_written is not None:
            fields.append('bytes_written = ?')
            values.append(bytes_written)
        
        if feeder_rate_bps is not None:
            fields.append('feeder_rate_bps = ?')
            values.append(feeder_rate_bps)
            
        if ingest_rate_bps is not None:
            fields.append('ingest_rate_bps = ?')
            values.append(ingest_rate_bps)
            
        if buffer_health is not None:
            fields.append('buffer_health = ?')
            values.append(buffer_health)
        
        if fields:
            values.append(job_id)
            sql = f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?"
            cursor.execute(sql, values)
            conn.commit()
    
    def batch_insert_archived_files(self, files: List[Dict]) -> int:
        """
        Insert multiple archived files in a single transaction.
        
        This is significantly faster than individual inserts for large batches.
        
        Args:
            files: List of file dictionaries with keys:
                - job_id (int)
                - tape_barcode (str)
                - file_path (str)
                - file_name (str)
                - file_extension (str)
                - file_size (int)
                - checksum (str)
                - file_path_on_tape (str)
                - tape_position (int, optional)
                - copy_set_id (str, optional)
                - copy_type (str, optional)
                - is_encrypted (bool, optional)
                - archived_at (str, optional)
        
        Returns:
            Number of files inserted
        """
        if not files:
            return 0
        
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # Separate files with and without archived_at
        files_with_time = []
        files_without_time = []
        
        for f in files:
            row = (
                f['job_id'],
                f['tape_barcode'],
                f['file_path'],
                f['file_name'],
                f.get('file_extension', ''),
                f['file_size'],
                f.get('checksum', ''),
                f.get('file_path_on_tape', ''),
                f.get('tape_position'),
                f.get('copy_set_id'),
                f.get('copy_type', 'primary'),
                1 if f.get('is_encrypted') else 0,
            )
            
            if 'archived_at' in f and f['archived_at']:
                files_with_time.append((*row, f['archived_at']))
            else:
                files_without_time.append(row)
        
        # Batch insert files with archived_at
        if files_with_time:
            cursor.executemany('''
                INSERT INTO archived_files 
                (job_id, tape_barcode, file_path, file_name, file_extension, file_size, checksum,
                 file_path_on_tape, tape_position, copy_set_id, copy_type, is_encrypted, archived_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', files_with_time)
        
        # Batch insert files without archived_at
        if files_without_time:
            cursor.executemany('''
                INSERT INTO archived_files 
                (job_id, tape_barcode, file_path, file_name, file_extension, file_size, checksum,
                 file_path_on_tape, tape_position, copy_set_id, copy_type, is_encrypted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', files_without_time)
        
        conn.commit()
        return len(files)
    
    def batch_update_job_progress(self, updates: List[Dict]) -> int:
        """
        Batch update multiple job progress records in a single transaction.
        
        Args:
            updates: List of update dictionaries with keys:
                - job_id (int)
                - files_written (int, optional)
                - bytes_written (int, optional)
        
        Returns:
            Number of jobs updated
        """
        if not updates:
            return 0
        
        conn = self._get_conn()
        cursor = conn.cursor()
        
        for update in updates:
            fields = []
            values = []
            
            if 'files_written' in update:
                fields.append('files_written = ?')
                values.append(update['files_written'])
            
            if 'bytes_written' in update:
                fields.append('bytes_written = ?')
                values.append(update['bytes_written'])
            
            if fields:
                values.append(update['job_id'])
                sql = f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?"
                cursor.execute(sql, values)
        
        conn.commit()
        return len(updates)
    
    def batch_transaction(self):
        """
        Context manager for batched database operations.
        
        Usage:
            with db.batch_transaction() as conn:
                # Perform multiple operations
                conn.execute(...)
                conn.execute(...)
                # Automatic commit on success, rollback on error
        """
        from contextlib import contextmanager
        
        @contextmanager
        def _transaction():
            conn = self._get_conn()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        
        return _transaction()

    
    def get_files_by_job(self, job_id: int) -> List[Dict]:
        """Get all files from a specific backup job"""
        return self.search_archived_files(None, job_id=job_id)
    
    def get_files_by_tape(self, tape_barcode: str) -> List[Dict]:
        """Get all files on a specific tape"""
        return self.search_archived_files(None, tape_barcode=tape_barcode)
    
    def get_files_by_extension(self, extension: str) -> List[Dict]:
        """Get all files of a specific type"""
        return self.search_archived_files(None, extension=extension)
    
    def _generate_tape_alias(self, barcode: str) -> str:
        """Generate friendly alias from barcode (match frontend logic)"""
        adjectives = [
            'Swift', 'Mighty', 'Noble', 'Brave', 'Silver', 'Golden', 'Arctic', 'Crimson', 'Azure', 'Emerald',
            'Fierce', 'Stalwart', 'Radiant', 'Ancient', 'Bold', 'Thunder', 'Shadow', 'Iron', 'Solar', 'Lunar',
            'Rugged', 'Valiant', 'Stoic', 'Prime', 'Vivid', 'Obsidian', 'Saffron', 'Jade', 'Cobalt', 'Onyx'
        ]
        dinosaurs = [
            'Tyrannosaurus', 'Triceratops', 'Velociraptor', 'Brachiosaurus', 'Stegosaurus', 'Ankylosaurus',
            'Spinosaurus', 'Apatosaurus', 'Allosaurus', 'Dilophosaurus', 'Carnotaurus', 'Giganotosaurus',
            'Parasaurolophus', 'Iguanodon', 'Pachycephalosaurus', 'Therizinosaurus', 'Corythosaurus',
            'Suchomimus', 'Albertosaurus', 'Ceratosaurus', 'Deinonychus', 'Gallimimus',
            'Maiasaura', 'Metriacanthosaurus', 'Monolophosaurus', 'Oviraptor', 'Protoceratops',
            'Quetzalcoatlus', 'Utahraptor', 'Yangchuanosaurus', 'Edmontosaurus', 'Euoplocephalus',
            'Styracosaurus', 'Herrerasaurus', 'Megalosaurus', 'Troodon', 'Coelophysis', 'Plateosaurus'
        ]
        
        hash_val = sum(ord(c) for c in barcode)
        adj_idx = hash_val % len(adjectives)
        dino_idx = (hash_val * 7) % len(dinosaurs)
        
        return f"{adjectives[adj_idx]} {dinosaurs[dino_idx]}"
    
    def get_files_on_tape(self, barcode: str) -> List[Dict]:
        """Get all files on a specific tape"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM archived_files 
            WHERE tape_barcode = ?
            ORDER BY file_path
        ''', (barcode,))
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    def get_total_archived_size(self) -> int:
        """Get total size of all archived data"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('SELECT COALESCE(SUM(file_size), 0) FROM archived_files')
        result = cursor.fetchone()
        return result[0] if result else 0

    def get_archived_size_for_job_tape(self, job_id: int, tape_barcode: str) -> int:
        """Get total size archived for a job on a specific tape."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT COALESCE(SUM(file_size), 0) FROM archived_files WHERE job_id = ? AND tape_barcode = ?',
            (job_id, tape_barcode)
        )
        result = cursor.fetchone()
        return result[0] if result else 0
    
    # Rolling backup support methods
    def tag_job_type(self, job_id: int, backup_type: str):
        """Tag a job with backup type for rolling backup rotation"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('UPDATE jobs SET backup_type = ? WHERE id = ?', (backup_type, job_id))
        conn.commit()
    
    def get_jobs_by_type(self, backup_type: str) -> List[Dict]:
        """Get all jobs of a specific backup type"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM jobs 
            WHERE backup_type = ? AND status = 'completed'
            ORDER BY completed_at DESC
        ''', (backup_type,))
        
        rows = cursor.fetchall()
        jobs = []
        for row in rows:
            job = dict(row)
            job['tapes'] = json.loads(job['tapes']) if job['tapes'] else []
            jobs.append(job)
        
        return jobs
    
    def get_jobs_before_date(self, backup_type: str, cutoff_date) -> List[Dict]:
        """Get jobs of a specific type older than cutoff date"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM jobs 
            WHERE backup_type = ? AND completed_at < ? AND status = 'completed'
        ''', (backup_type, cutoff_date.isoformat()))
        
        rows = cursor.fetchall()
        jobs = []
        for row in rows:
            job = dict(row)
            job['tapes'] = json.loads(job['tapes']) if job['tapes'] else []
            jobs.append(job)
        
        return jobs
    
    def mark_tape_available(self, barcode: str):
        """Mark a tape as available for reuse"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('UPDATE tapes SET status = ? WHERE barcode = ?', ('available', barcode))
        conn.commit()
    
    def lock_tape(self, barcode: str, expires_at: str) -> bool:
        """Set WORM lock and retention expiration for a tape."""
        conn = self._get_conn()
        try:
            conn.execute('''
                UPDATE tapes SET 
                    worm_lock = 1, 
                    retention_expires_at = ? 
                WHERE barcode = ?
            ''', (expires_at, barcode))
            conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to lock tape {barcode}: {e}")
            return False

    def is_tape_locked(self, barcode: str) -> bool:
        """Check if tape is under WORM retention lock."""
        conn = self._get_conn()
        try:
            cursor = conn.execute('''
                SELECT worm_lock, retention_expires_at 
                FROM tapes WHERE barcode = ?
            ''', (barcode,))
            row = cursor.fetchone()
            if not row or not row['worm_lock']:
                return False
            
            expires_at = row['retention_expires_at']
            if not expires_at:
                return True # Permanent lock if no date set
            
            # Check if retention has expired
            now = datetime.now(timezone.utc).isoformat()
            return expires_at > now
        except sqlite3.Error:
            return False

    def update_tape_status(self, barcode: str, status: str):
        """Update tape status"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('UPDATE tapes SET status = ? WHERE barcode = ?', (status, barcode))
        conn.commit()
    
    def archive_job(self, job_id: int):
        """Archive a job (mark as archived but keep metadata)"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('UPDATE jobs SET status = ? WHERE id = ?', ('archived', job_id))
        conn.commit()
    
    # Tape reservation methods
    def reserve_tape(self, barcode: str, job_id: int):
        """Reserve a tape for a specific job"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE tapes 
            SET status = ?, reserved_by_job = ?
            WHERE barcode = ?
        ''', ('reserved', job_id, barcode))
        conn.commit()
    
    def release_tape(self, barcode: str):
        """Release a tape from reservation"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE tapes 
            SET status = ?, reserved_by_job = NULL
            WHERE barcode = ?
        ''', ('available', barcode))
        conn.commit()
    
    def update_tape_usage(self, barcode: str, used_bytes: int):
        """Update tape usage information"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE tapes 
            SET used_bytes = ?, status = ?
            WHERE barcode = ?
        ''', (used_bytes, 'used', barcode))
        conn.commit()

    def update_tape_ltfs_info(
        self,
        barcode: str,
        *,
        formatted: bool,
        ltfs_present: Optional[bool] = None,
        initialized: Optional[bool] = None,
        capacity_bytes: Optional[int] = None,
        used_bytes: Optional[int] = None,
        volume_name: Optional[str] = None,
        verified_at: Optional[str] = None,
    ):
        """Update LTFS formatting metadata for a tape."""
        conn = self._get_conn()
        cursor = conn.cursor()
        verified_at = verified_at or now_utc_iso()
        cursor.execute(
            '''
            UPDATE tapes
            SET ltfs_formatted = ?,
                ltfs_verified_at = ?,
                volume_name = COALESCE(?, volume_name),
                capacity_bytes = COALESCE(?, capacity_bytes),
                used_bytes = COALESCE(?, used_bytes),
                ltfs_present = COALESCE(?, ltfs_present),
                initialized = COALESCE(?, initialized)
            WHERE barcode = ?
            ''',
            (
                1 if formatted else 0,
                verified_at,
                volume_name,
                capacity_bytes,
                used_bytes,
                None if ltfs_present is None else (1 if ltfs_present else 0),
                None if initialized is None else (1 if initialized else 0),
                barcode,
            ),
        )
        conn.commit()

    def get_tapes_by_utilization(self, threshold_percent: float = 50.0, limit: int = 100) -> List[Dict]:
        """
        Get tapes with utilization below a certain threshold.
        Excludes tapes that are empty (used_bytes=0) or reserved.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT *, 
                   (CAST(used_bytes AS FLOAT) / CASE WHEN capacity_bytes > 0 THEN capacity_bytes ELSE 1 END) * 100 as utilization
            FROM tapes
            WHERE used_bytes > 0 
              AND capacity_bytes > 0
              AND status != 'reserved'
              AND (CAST(used_bytes AS FLOAT) / capacity_bytes) * 100 < ?
            ORDER BY utilization ASC
            LIMIT ?
        ''', (threshold_percent, limit))
        
        return [dict(row) for row in cursor.fetchall()]

    
    def update_job_tapes(self, job_id: int, tapes: List[str]):
        """Update list of tapes for a job"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE jobs 
            SET tapes = ?
            WHERE id = ?
        ''', (json.dumps(tapes), job_id))
        conn.commit()
    
    # Health check methods
    def get_archived_files_for_health_check(self, source_job_id: int) -> List[Dict]:
        """Get all archived files from a specific job for health checking"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM archived_files
            WHERE job_id = ?
            ORDER BY tape_barcode, file_path
        ''', (source_job_id,))
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    def store_health_check_results(self, job_id: int, results: Dict):
        """Store health check results"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO health_check_results 
            (job_id, total_files, checked_files, failed_files, failures)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            job_id,
            results['total_files'],
            results['checked_files'],
            results['failed_files'],
            json.dumps(results['failures'])
        ))
        conn.commit()
    
    def get_health_check_results(self, job_id: int) -> Optional[Dict]:
        """Get health check results for a job"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM health_check_results
            WHERE job_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        ''', (job_id,))
        
        row = cursor.fetchone()
        if row:
            result = dict(row)
            result['failures'] = json.loads(result['failures']) if result['failures'] else []
            return result
        return None
    
    # Schedule operations
    def create_schedule(self, name: str, source_id: str,
                       cron: str, tapes: List[str], verify: bool = True,
                       compression: str = 'zstd', duplicate: bool = False,
                       enabled: bool = True, source_config: Dict = None,
                       drive: int = 0, backup_mode: str = 'full') -> int:
        """
        Create a new backup schedule.
        
        Args:
            name: Schedule name
            source_id: Source identifier to use
            cron: Cron expression (5-6 fields: [sec] min hour day month day_of_week)
            tapes: List of tape barcodes to use
            verify: Whether to verify after backup
            compression: Compression type (zstd, gzip, lz4, none)
            duplicate: Whether to create duplicate copy
            enabled: Whether schedule is active
            
        Returns:
            Schedule ID
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO schedules
            (name, smb_path, credential_name, cron, tapes, verify, compression, duplicate, enabled, source_config,
             drive, backup_mode, source_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            name,
            source_config.get('source_path') if source_config else None,
            source_config.get('credential_name') if source_config else None,
            cron,
            json.dumps(tapes),
            verify,
            compression,
            duplicate,
            enabled,
            json.dumps(source_config or {}),
            drive,
            backup_mode,
            source_id,
        ))
        
        conn.commit()
        return cursor.lastrowid
    
    def get_schedules(self) -> List[Dict]:
        """Get all schedules"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM schedules ORDER BY created_at DESC')
        rows = cursor.fetchall()
        
        schedules = []
        for row in rows:
            schedule = dict(row)
            schedule['tapes'] = json.loads(schedule['tapes']) if schedule['tapes'] else []
            if schedule.get('source_config'):
                try:
                    schedule['source_config'] = json.loads(schedule['source_config'])
                except (TypeError, ValueError, json.JSONDecodeError):
                    schedule['source_config'] = {}
            schedules.append(schedule)
        
        return schedules
    
    def toggle_schedule(self, schedule_id: int):
        """Toggle schedule enabled state"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE schedules 
            SET enabled = NOT enabled 
            WHERE id = ?
        ''', (schedule_id,))
        
        conn.commit()
    
    def update_schedule_last_run(self, schedule_id: int):
        """Update last run time for a schedule"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE schedules 
            SET last_run = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (schedule_id,))
        
        conn.commit()
    
    # Restore operations
    def create_restore_job(self, files: List[Dict], destination: str,
                           file_ids: Optional[List[int]] = None,
                           metadata: Optional[Dict] = None) -> int:
        """Create a restore job"""
        conn = self._get_conn()
        cursor = conn.cursor()

        if file_ids is None:
            file_ids = [f.get('id') for f in files if f.get('id') is not None]
        
        cursor.execute('''
            INSERT INTO restore_jobs (files, file_ids, destination, metadata)
            VALUES (?, ?, ?, ?)
        ''', (json.dumps(files), json.dumps(file_ids), destination, json.dumps(metadata or {})))
        
        conn.commit()
        return cursor.lastrowid
    
    def get_restore_job(self, restore_id: int) -> Optional[Dict]:
        """Get restore job by ID"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM restore_jobs WHERE id = ?', (restore_id,))
        row = cursor.fetchone()
        
        if row:
            restore = dict(row)
            restore['files'] = json.loads(restore['files'])
            if restore.get('file_ids'):
                try:
                    restore['file_ids'] = json.loads(restore['file_ids'])
                except (TypeError, ValueError, json.JSONDecodeError):
                    restore['file_ids'] = []
            else:
                restore['file_ids'] = []
            if restore.get('metadata'):
                try:
                    restore['metadata'] = json.loads(restore['metadata'])
                except (TypeError, ValueError, json.JSONDecodeError):
                    restore['metadata'] = {}
            else:
                restore['metadata'] = {}
            return restore
        return None

    def list_restore_jobs(self, limit: int = 50) -> List[Dict]:
        """List recent restore jobs"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM restore_jobs ORDER BY created_at DESC LIMIT ?',
            (limit,)
        )
        rows = cursor.fetchall()
        jobs = []
        for row in rows:
            restore = dict(row)
            restore['files'] = json.loads(restore['files'])
            if restore.get('file_ids'):
                try:
                    restore['file_ids'] = json.loads(restore['file_ids'])
                except (TypeError, ValueError, json.JSONDecodeError):
                    restore['file_ids'] = []
            else:
                restore['file_ids'] = []
            if restore.get('metadata'):
                try:
                    restore['metadata'] = json.loads(restore['metadata'])
                except (TypeError, ValueError, json.JSONDecodeError):
                    restore['metadata'] = {}
            else:
                restore['metadata'] = {}
            jobs.append(restore)
        return jobs
    
    def update_restore_status(self, restore_id: int, status: str,
                              error: str = None, metadata: Dict = None):
        """Update restore job status"""
        self.update_restore_job_status(restore_id, status, error=error, metadata=metadata)

    def update_restore_job_status(self, restore_id: int, status: str,
                                  error: str = None, metadata: Dict = None):
        """Update restore job status with timestamps and optional metadata."""
        conn = self._get_conn()
        cursor = conn.cursor()

        fields = ['status = ?']
        values = [status]

        if error:
            fields.append('error = ?')
            values.append(error)

        if metadata is not None:
            fields.append('metadata = ?')
            values.append(json.dumps(metadata))

        timestamp_field = None
        if status == 'running':
            timestamp_field = 'started_at'
        elif status in ('completed', 'failed', 'cancelled'):
            timestamp_field = 'completed_at'

        if timestamp_field:
            fields.append(f'{timestamp_field} = CURRENT_TIMESTAMP')

        values.append(restore_id)
        sql = f"UPDATE restore_jobs SET {', '.join(fields)} WHERE id = ?"
        cursor.execute(sql, values)
        conn.commit()
    
    # Settings operations
    def get_settings(self) -> Dict:
        """Get all settings"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('SELECT key, value FROM settings')
        rows = cursor.fetchall()
        
        settings = {}
        for row in rows:
            try:
                settings[row['key']] = json.loads(row['value'])
            except (TypeError, ValueError, json.JSONDecodeError):
                settings[row['key']] = row['value']
        
        return settings
    
    def get_setting(self, key: str, default=None):
        """Get a single setting value"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
        row = cursor.fetchone()
        
        if row:
            try:
                return json.loads(row['value'])
            except (TypeError, ValueError, json.JSONDecodeError):
                return row['value']
        return default

    def get_bool_setting(self, key: str, default: bool = False) -> bool:
        """Return a boolean setting with common string/int parsing."""
        value = self.get_setting(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {'true', '1', 'yes', 'on'}
        return bool(value)
    
    def update_settings(self, settings: Dict):
        """Update settings"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        for key, value in settings.items():
            value_str = json.dumps(value) if not isinstance(value, str) else value
            
            cursor.execute('''
                INSERT OR REPLACE INTO settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (key, value_str))
        
        conn.commit()
    
    # ==========================================================================
    # Logging operations
    # ==========================================================================
    
    def _ensure_logs_table(self):
        """Create logs table if it doesn't exist"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                category TEXT DEFAULT 'system',
                details TEXT,
                hash TEXT,
                previous_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_category ON logs(category)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp DESC)')
        
        self._migrate_logs_schema(conn)
        conn.commit()
    
    def add_log(self, log_entry: Dict = None, level: str = None, category: str = None,
                message: str = None, details: str = None, timestamp: str = None):
        """Add a log entry to the database"""
        self._ensure_logs_table()
        conn = self._get_conn()
        cursor = conn.cursor()

        if isinstance(log_entry, str):
            # If a string is passed, treat it as the message
            message = log_entry
            log_entry = None

        if log_entry is None:
            log_entry = {
                'timestamp': timestamp or now_utc_iso(),
                'level': level or 'info',
                'message': message or '',
                'category': category or 'system',
                'details': details
            }
        
        details_val = log_entry.get('details')
        if isinstance(details_val, dict):
            details_val = json.dumps(details_val)

        cursor.execute('''
            INSERT INTO logs (timestamp, level, message, category, details, hash, previous_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            log_entry.get('timestamp', now_utc_iso()),
            log_entry.get('level', 'info'),
            log_entry.get('message', ''),
            log_entry.get('category', 'system'),
            details_val,
            log_entry.get('hash'),
            log_entry.get('previous_hash')
        ))
        conn.commit()
        return cursor.lastrowid
    
    def log_entry(self, level: str, category: str, message: str, details: str = None):
        """
        Convenience method to add a log entry with positional arguments.
        This is the preferred method for backup_engine and other internal callers.
        """
        return self.add_log(level=level, category=category, message=message, details=details)

    # ==========================================================================
    # Job log operations
    # ==========================================================================

    def add_job_log(self, job_id: int, level: str, message: str, details: str = None,
                    timestamp: str = None):
        """Add a job-specific log entry."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO job_logs (job_id, timestamp, level, message, details)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            job_id,
            timestamp or now_utc_iso(),
            level,
            message,
            details
        ))
        conn.commit()
        return cursor.lastrowid

    def get_job_logs(self, job_id: int, limit: int = 200) -> List[Dict]:
        """Get recent logs for a job."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM job_logs
            WHERE job_id = ?
            ORDER BY timestamp ASC
            LIMIT ?
        ''', (job_id, limit))
        return [dict(row) for row in cursor.fetchall()]

    # ==========================================================================
    # File Search (using archived_files)
    # ==========================================================================

    def _ensure_search_indexes(self, cursor):
        """Ensure indexes exist for performance."""
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_archived_path ON archived_files(file_path)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_archived_name ON archived_files(file_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_archived_tape ON archived_files(tape_barcode)')

    def search_files(self, query: str, limit: int = 100) -> List[Dict]:
        """Search for files in the archive."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # Optimize: search by name first if short path
        search_pattern = f"%{query}%"
        cursor.execute('''
            SELECT 
                af.*, 
                j.name as job_name,
                j.backup_set_id,
                t.alias as tape_alias
            FROM archived_files af
            JOIN jobs j ON af.job_id = j.id
            LEFT JOIN tapes t ON af.tape_barcode = t.barcode
            WHERE af.file_path LIKE ? OR af.file_name LIKE ?
            ORDER BY af.archived_at DESC
            LIMIT ?
        ''', (search_pattern, search_pattern, limit))
        
        return [dict(row) for row in cursor.fetchall()]

    
    def get_logs(self, level: str = None, category: str = None, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Get logs with optional filtering"""
        self._ensure_logs_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        
        sql = 'SELECT * FROM logs WHERE 1=1'
        params = []
        
        if level and level != 'all':
            if level == 'errors':
                sql += ' AND level = ?'
                params.append('error')
            elif level == 'warnings':
                sql += ' AND level = ?'
                params.append('warning')
            else:
                sql += ' AND level = ?'
                params.append(level)
        
        if category:
            sql += ' AND category = ?'
            params.append(category)
        
        sql = f'SELECT * FROM ({sql} ORDER BY timestamp DESC LIMIT ? OFFSET ?) ORDER BY timestamp ASC'
        params.extend([limit, offset])

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    def get_logs_count(self, level: str = None, category: str = None) -> int:
        """Get total count of logs matching filters"""
        self._ensure_logs_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        
        sql = 'SELECT COUNT(*) FROM logs WHERE 1=1'
        params = []
        
        if level and level != 'all':
            sql += ' AND level = ?'
            params.append(level)
        
        if category:
            sql += ' AND category = ?'
            params.append(category)
        
        cursor.execute(sql, params)
        return cursor.fetchone()[0]
    
    
    # ==========================================================================
    # Analytics & Stats
    # ==========================================================================

    def get_dashboard_stats(self) -> Dict:
        """Get aggregated statistics for the dashboard."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # 1. Storage Growth (Daily bytes written in last 30 days)
        try:
            cursor.execute('''
                SELECT date(started_at) as day, sum(bytes_written) as total_bytes
                FROM jobs 
                WHERE status = 'completed' 
                  AND started_at > date('now', '-30 days')
                GROUP BY date(started_at)
                ORDER BY day ASC
            ''')
            growth_data = [dict(row) for row in cursor.fetchall()]
        except Exception:
            growth_data = []
        
        # 2. Tape Utilization
        try:
            cursor.execute('''
                SELECT 
                    count(*) as total_tapes,
                    sum(capacity_bytes) as total_capacity,
                    sum(used_bytes) as total_used
                FROM tapes
                WHERE status != 'offline'
            ''')
            tape_stats = dict(cursor.fetchone() or {})
        except Exception:
            tape_stats = {}
        
        # 3. Job Status Distribution (Last 100 jobs)
        try:
            cursor.execute('''
                SELECT status, count(*) as count
                FROM (SELECT status FROM jobs ORDER BY id DESC LIMIT 100)
                GROUP BY status
            ''')
            job_stats = [dict(row) for row in cursor.fetchall()]
        except Exception:
            job_stats = []
        
        # 4. Compression Efficiency (Global avg)
        # Using completed jobs where we have both source size (total_size) and written size
        try:
            cursor.execute('''
                SELECT sum(total_size) as source_bytes, sum(bytes_written) as tape_bytes
                FROM jobs
                WHERE status='completed' AND bytes_written > 0
            ''')
            comp_row = cursor.fetchone()
            compression = 1.0
            if comp_row and comp_row['tape_bytes'] and comp_row['source_bytes']:
                compression = comp_row['source_bytes'] / comp_row['tape_bytes']
        except Exception:
            compression = 1.0

        return {
            'storage_growth': growth_data,
            'tape_stats': {
                'total_tapes': tape_stats.get('total_tapes', 0),
                'total_capacity': tape_stats.get('total_capacity', 0) or 0,
                'total_used': tape_stats.get('total_used', 0) or 0,
            },
            'job_distribution': job_stats,
            'compression_ratio': round(compression, 2)
        }

    # =========================================================================
    # Audit Log Methods (Section F) - Immutable Hash Chain
    # =========================================================================
    
    def _ensure_audit_table(self):
        """Ensure audit_log table exists with comprehensive columns for compliance."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                username TEXT,
                user TEXT,
                action TEXT NOT NULL,
                message TEXT,
                details TEXT,
                detail TEXT,
                level TEXT DEFAULT 'info',
                category TEXT DEFAULT 'system',
                ip TEXT,
                previous_hash TEXT,
                entry_hash TEXT,
                signature TEXT
            )
        ''')
        
        # Migrations for existing tables
        columns = [
            ('username', 'TEXT'),
            ('user', 'TEXT'),
            ('action', 'TEXT'),
            ('message', 'TEXT'),
            ('details', 'TEXT'),
            ('detail', 'TEXT'),
            ('level', "TEXT DEFAULT 'info'"),
            ('category', "TEXT DEFAULT 'system'"),
            ('ip', 'TEXT'),
            ('previous_hash', 'TEXT'),
            ('entry_hash', 'TEXT'),
            ('signature', 'TEXT')
        ]
        for col_name, col_type in columns:
            try:
                cursor.execute(f'ALTER TABLE audit_log ADD COLUMN {col_name} {col_type}')
            except Exception:
                pass # Already exists
            
        conn.commit()
    
    def _compute_audit_hash(self, entry: Dict, previous_hash: str = None) -> str:
        """Compute SHA-256 hash of an audit entry."""
        import hashlib
        # Create deterministic string from entry fields
        hash_input = json.dumps({
            'action': entry.get('action'),
            'message': entry.get('message'),
            'username': entry.get('username') or entry.get('user'),
            'details': entry.get('details'),
            'level': entry.get('level', 'info'),
            'category': entry.get('category', 'system'),
            'timestamp': entry.get('timestamp'),
            'previous_hash': previous_hash or ''
        }, sort_keys=True)
        return hashlib.sha256(hash_input.encode()).hexdigest()
    
    def _get_last_audit_hash(self) -> str:
        """Get the hash of the last audit log entry."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1')
        row = cursor.fetchone()
        return row['entry_hash'] if row and row['entry_hash'] else ''
    
    def add_audit_log(self, action: str, username: str = 'system', message: str = '', 
                      level: str = 'info', category: str = 'system', detail: Dict = None) -> str:
        """Add an audit log entry with hash chain integrity."""
        self._ensure_audit_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # Support both argument-based and dict-based (for backward compatibility with lto_backend_main)
        if isinstance(action, dict):
            entry = action
            action_str = entry.get('action')
            details = entry.get('details')
            user = entry.get('user') or entry.get('username') or 'system'
            msg = entry.get('message') or ''
            lvl = entry.get('level') or 'info'
            cat = entry.get('category') or 'system'
            ip = entry.get('ip')
        else:
            action_str = action
            details = detail
            user = username
            msg = message
            lvl = level
            cat = category
            ip = None
            entry = {
                'action': action_str,
                'details': details,
                'username': user,
                'message': msg,
                'level': lvl,
                'category': cat,
                'ip': ip
            }

        # Get previous hash for chain
        previous_hash = self._get_last_audit_hash()
        
        # Set timestamp
        timestamp = entry.get('timestamp', now_utc_iso())
        entry['timestamp'] = timestamp
        
        details_json = json.dumps(details) if isinstance(details, dict) else details
        entry['details'] = details_json

        # Compute entry hash
        entry_hash = self._compute_audit_hash(entry, previous_hash)
        
        # Sign for Enterprise WORM compliance if feasible
        signature = None
        try:
            if True: # Fully enabled in AGPL-3.0
                from .utils.hashing import AuditSigner
                signer = AuditSigner()
                signature = signer.sign(entry_hash)
        except (ImportError, AttributeError):
            pass

        cursor.execute('''
            INSERT INTO audit_log (
                action, details, username, message, level, category, ip, 
                timestamp, previous_hash, entry_hash, signature
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            action_str, details_json, user, msg, lvl, cat, ip,
            timestamp, previous_hash, entry_hash, signature
        ))
        conn.commit()
        return entry_hash
    
    def get_audit_log(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Get audit log entries with pagination."""
        self._ensure_audit_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ? OFFSET ?',
            (limit, offset)
        )
        return [dict(row) for row in cursor.fetchall()]
    
    def verify_audit_chain(self) -> Dict:
        """
        Verify the integrity of the audit log hash chain.
        
        Returns:
            Dict with 'valid', 'total_entries', 'first_invalid_id', 'error'
        """
        self._ensure_audit_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM audit_log ORDER BY id ASC')
        
        entries = [dict(row) for row in cursor.fetchall()]
        
        if not entries:
            return {'valid': True, 'total_entries': 0, 'verified': 0}
        
        previous_hash = ''
        verified_count = 0
        
        for entry in entries:
            # Reconstruct the entry dict for hashing to match _compute_audit_hash
            entry_data = {
                'action': entry.get('action'),
                'message': entry.get('message'),
                'username': entry.get('username') or entry.get('user'),
                'details': entry.get('details'),
                'level': entry.get('level', 'info'),
                'category': entry.get('category', 'system'),
                'timestamp': entry.get('timestamp')
            }
            
            # Verify previous hash matches
            stored_previous = entry.get('previous_hash') or ''
            if stored_previous != previous_hash:
                return {
                    'valid': False,
                    'total_entries': len(entries),
                    'verified': verified_count,
                    'first_invalid_id': entry.get('id'),
                    'error': f"Previous hash mismatch at ID {entry.get('id')}: expected {previous_hash[:8]}, got {stored_previous[:8]}"
                }
            
            # Verify entry hash
            expected_hash = self._compute_audit_hash(entry_data, previous_hash)
            stored_hash = entry.get('entry_hash') or ''
            
            if stored_hash and stored_hash != expected_hash:
                return {
                    'valid': False,
                    'total_entries': len(entries),
                    'verified': verified_count,
                    'first_invalid_id': entry.get('id'),
                    'error': f"Entry hash mismatch at ID {entry.get('id')}: data tampered"
                }

            # Verify signature if present (Enterprise)
            signature_hex = entry.get('signature')
            if signature_hex:
                from .utils.hashing import AuditSigner
                signer = AuditSigner()
                if not signer.verify(stored_hash, signature_hex):
                    return {
                        'valid': False,
                        'total_entries': len(entries),
                        'verified': verified_count,
                        'first_invalid_id': entry.get('id'),
                        'error': f'Signature verification failed for entry {entry.get("id")}'
                    }
            
            previous_hash = stored_hash or expected_hash
            verified_count += 1
        
        return {
            'valid': True,
            'total_entries': len(entries),
            'verified': verified_count
        }
    
    def export_audit_log(self, start_date: str = None, end_date: str = None) -> Dict:
        """
        Export audit log with verification signature.
        
        Returns:
            Dict with entries and verification data
        """
        self._ensure_audit_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        
        query = 'SELECT * FROM audit_log'
        params = []
        
        if start_date or end_date:
            conditions = []
            if start_date:
                conditions.append('timestamp >= ?')
                params.append(start_date)
            if end_date:
                conditions.append('timestamp <= ?')
                params.append(end_date)
            query += ' WHERE ' + ' AND '.join(conditions)
        
        query += ' ORDER BY id ASC'
        cursor.execute(query, params)
        entries = [dict(row) for row in cursor.fetchall()]
        
        # Compute overall hash for export verification
        import hashlib
        export_content = json.dumps(entries, sort_keys=True, default=str)
        export_hash = hashlib.sha256(export_content.encode()).hexdigest()
        
        return {
            'entries': entries,
            'entry_count': len(entries),
            'export_timestamp': now_utc_iso(),
            'export_hash': export_hash,
            'chain_verified': self.verify_audit_chain()
        }

    def get_compliance_stats(self) -> Dict:
        """Retrieve summarized compliance and security statistics."""
        self._ensure_audit_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # Count WORM locked tapes
        cursor.execute('SELECT COUNT(*) as count FROM tapes WHERE worm_lock = 1')
        worm_tapes = cursor.fetchone()['count']
        
        # Count total audit entries
        cursor.execute('SELECT COUNT(*) as count FROM audit_log')
        audit_count = cursor.fetchone()['count']
        
        # Security levels and events from audit_log
        cursor.execute("SELECT level, COUNT(*) as count FROM audit_log GROUP BY level")
        level_counts = {row['level']: row['count'] for row in cursor.fetchall()}
        
        # Specific alerts
        cursor.execute("SELECT COUNT(*) as count FROM audit_log WHERE action = 'LOGIN_FAILURE'")
        login_failures = cursor.fetchone()['count']

        return {
            'worm_locked_tapes': worm_tapes,
            'total_audit_entries': audit_count,
            'security_events_summary': level_counts,
            'specific_alerts': {
                'login_failures': login_failures
            },
            'system_timestamp': now_utc_iso()
        }

    def generate_compliance_report(self) -> Dict:
        """Generate a comprehensive, signed compliance report."""
        stats = self.get_compliance_stats()
        verification = self.verify_audit_chain()
        
        report = {
            'report_id': f"RPT-COMP-{os.urandom(4).hex().upper()}",
            'generated_at': now_utc_iso(),
            'system_info': {
                'version': self.get_setting('version', 'unknown'),
                'node': os.uname().nodename
            },
            'compliance_stats': stats,
            'audit_verification': verification,
            'status': 'COMPLIANT' if verification.get('valid') and stats['worm_locked_tapes'] > 0 else 'WARNING'
        }
        
        # Sign the report (Enterprise)
        try:
            from .utils.hashing import AuditSigner
            signer = AuditSigner()
            report_str = json.dumps(report, sort_keys=True, default=str)
            report['signature'] = signer.sign(report_str)
        except (ImportError, AttributeError):
            import hmac
            secret = os.getenv('FOSSIL_SAFE_SECRET', 'SYSTEM_INTERNAL_KEY_ROOT_TRUST')
            content = json.dumps(report, sort_keys=True, default=str)
            signature = hmac.new(secret.encode(), content.encode(), hashlib.sha256).hexdigest()
            report['signature'] = f"v1:{signature}"
        
        return report

    
    # =========================================================================
    # Tape Wipe Methods (Section F)
    # =========================================================================
    
    def clear_tape_files(self, barcode: str):
        """Remove all file records for a wiped tape"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM archived_files WHERE tape_barcode = ?', (barcode,))
        cursor.execute('UPDATE tapes SET used_bytes = 0, write_count = write_count + 1 WHERE barcode = ?', (barcode,))
        conn.commit()
    
    # =========================================================================
    # Maintenance Windows (Section J3)
    # =========================================================================
    
    def _ensure_maintenance_table(self):
        """Ensure maintenance_windows table exists"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS maintenance_windows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                recurring BOOLEAN DEFAULT 0,
                days TEXT,
                enabled BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
    
    def add_maintenance_window(self, name: str, start_time: str, end_time: str, 
                               recurring: bool = False, days: List[str] = None) -> int:
        """Add a maintenance window"""
        self._ensure_maintenance_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO maintenance_windows (name, start_time, end_time, recurring, days)
            VALUES (?, ?, ?, ?, ?)
        ''', (name, start_time, end_time, recurring, json.dumps(days or [])))
        conn.commit()
        return cursor.lastrowid
    
    def get_maintenance_windows(self) -> List[Dict]:
        """Get all maintenance windows"""
        self._ensure_maintenance_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM maintenance_windows ORDER BY start_time')
        windows = []
        for row in cursor.fetchall():
            w = dict(row)
            w['days'] = json.loads(w.get('days', '[]'))
            windows.append(w)
        return windows
    
    def is_in_maintenance_window(self) -> bool:
        """Check if currently in a maintenance window"""
        self._ensure_maintenance_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        
        now = datetime.now()
        current_time = now.strftime('%H:%M')
        current_day = now.strftime('%A').lower()
        
        cursor.execute('''
            SELECT * FROM maintenance_windows 
            WHERE enabled = 1 AND start_time <= ? AND end_time >= ?
        ''', (current_time, current_time))
        
        for row in cursor.fetchall():
            window = dict(row)
            if window['recurring']:
                days = json.loads(window.get('days', '[]'))
                if current_day in [d.lower() for d in days]:
                    return True
            else:
                return True
        
        return False
    
    # =========================================================================
    # Catalog Backup (Section J5)
    # =========================================================================
    
    def _ensure_catalog_backup_table(self):
        """Ensure catalog_backups table exists"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS catalog_backups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                size INTEGER,
                checksum TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
    
    def add_catalog_backup(self, backup: Dict):
        """Record a catalog backup"""
        self._ensure_catalog_backup_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO catalog_backups (path, timestamp, size, checksum)
            VALUES (?, ?, ?, ?)
        ''', (backup['path'], backup['timestamp'], backup['size'], backup['checksum']))
        conn.commit()
    
    def get_catalog_backups(self, limit: int = 20) -> List[Dict]:
        """Get catalog backup history"""
        self._ensure_catalog_backup_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM catalog_backups ORDER BY created_at DESC LIMIT ?', (limit,))
        return [dict(row) for row in cursor.fetchall()]
    
    # =========================================================================
    # Tape Management (Section J8)
    # =========================================================================
    
    def add_tape(self, barcode: str, slot: int, alias: Optional[str] = None):
        """Add a new tape to inventory, detecting generation from barcode"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # Parse generation from barcode suffix
        generation = 'Unknown'
        capacity = 2500000000000  # Default to LTO-6
        
        if barcode and len(barcode) >= 2:
            suffix = barcode[-2:].upper()
            gen_map = {
                'L5': ('LTO-5', 1500000000000),
                'L6': ('LTO-6', 2500000000000),
                'L7': ('LTO-7', 6000000000000),
                'M8': ('LTO-8', 12000000000000),
                'L8': ('LTO-8', 12000000000000),
                'L9': ('LTO-9', 18000000000000),
                'LA': ('LTO-10', 36000000000000),
            }
            if suffix in gen_map:
                generation, capacity = gen_map[suffix]
        
        cursor.execute('''
            INSERT OR REPLACE INTO tapes (barcode, generation, slot, status, capacity_bytes, used_bytes, alias)
            VALUES (?, ?, ?, 'available', ?, 0, ?)
        ''', (barcode, generation, slot, capacity, alias))
        conn.commit()
    
    def remove_tape(self, barcode: str):
        """Remove a tape from inventory"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM tapes WHERE barcode = ?', (barcode,))
        conn.commit()

    def increment_tape_mount_count(self, barcode: str):
        """Increment the mount count for a tape."""
        conn = self._get_conn()
        conn.execute("UPDATE tapes SET mount_count = mount_count + 1 WHERE barcode = ?", (barcode,))
        conn.commit()

    def increment_tape_error_count(self, barcode: str):
        """Increment the error count for a tape."""
        conn = self._get_conn()
        conn.execute("UPDATE tapes SET error_count = error_count + 1 WHERE barcode = ?", (barcode,))
        conn.commit()

    def update_tape_trust_status(self, barcode: str, status: str):
        """Update the trust status of a tape."""
        conn = self._get_conn()
        conn.execute("UPDATE tapes SET trust_status = ? WHERE barcode = ?", (status, barcode))
        conn.commit()
    
    def update_tape_slot(self, barcode: str, slot: int):
        """Update tape slot number"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('UPDATE tapes SET slot = ? WHERE barcode = ?', (slot, barcode))
        conn.commit()

    def update_tape_alias(self, barcode: str, alias: Optional[str]):
        """Update the user-facing alias for a tape."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('UPDATE tapes SET alias = ? WHERE barcode = ?', (alias, barcode))
        conn.commit()
    
    def get_available_tapes(self) -> List[Dict]:
        """Get available tapes for backup"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM tapes 
            WHERE status = 'available' 
            AND barcode NOT LIKE '%CLN%' 
            AND barcode NOT LIKE '%CU%'
            ORDER BY used_bytes ASC
        ''')
        return [dict(row) for row in cursor.fetchall()]
    
    def get_tapes_below_threshold(self, percent_free: int) -> List[Dict]:
        """Get tapes with less than X% free space"""
        conn = self._get_conn()
        cursor = conn.cursor()
        threshold = (100 - percent_free) / 100.0
        cursor.execute('''
            SELECT * FROM tapes 
            WHERE CAST(used_bytes AS REAL) / CAST(capacity_bytes AS REAL) > ?
            AND barcode NOT LIKE '%CLN%'
        ''', (threshold,))
        return [dict(row) for row in cursor.fetchall()]
    
    # =========================================================================
    # Job Timeline (Section K1.3)
    # =========================================================================
    
    def _ensure_timeline_table(self):
        """Ensure job_timeline table exists"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS job_timeline (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                step TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            )
        ''')
        conn.commit()
    
    def add_timeline_event(self, job_id: int, step: str, status: str, message: str = None):
        """Add a timeline event for a job"""
        self._ensure_timeline_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO job_timeline (job_id, step, status, message, started_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (job_id, step, status, message, now_utc_iso()))
        conn.commit()
    
    def complete_timeline_event(self, job_id: int, step: str, status: str, message: str = None):
        """Mark a timeline event as complete"""
        self._ensure_timeline_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE job_timeline 
            SET status = ?, message = ?, completed_at = ?
            WHERE job_id = ? AND step = ? AND completed_at IS NULL
        ''', (status, message, now_utc_iso(), job_id, step))
        conn.commit()
    
    def get_job_timeline(self, job_id: int) -> List[Dict]:
        """Get timeline for a job"""
        self._ensure_timeline_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM job_timeline 
            WHERE job_id = ? 
            ORDER BY started_at ASC
        ''', (job_id,))
        return [dict(row) for row in cursor.fetchall()]
    
    # =========================================================================
    # Last Completed Job (Section K1)
    # =========================================================================
    
    def get_last_completed_job(self) -> Optional[Dict]:
        """Get the most recently completed job"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM jobs 
            WHERE status = 'completed' 
            ORDER BY completed_at DESC 
            LIMIT 1
        ''')
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_last_job_by_types(self, job_types: List[str]) -> Optional[Dict]:
        """Get the most recent job matching any of the provided job types."""
        if not job_types:
            return None
        conn = self._get_conn()
        cursor = conn.cursor()
        placeholders = ", ".join("?" for _ in job_types)
        cursor.execute(
            f'''
            SELECT * FROM jobs
            WHERE job_type IN ({placeholders})
            ORDER BY created_at DESC
            LIMIT 1
            ''',
            list(job_types),
        )
        row = cursor.fetchone()
        if not row:
            return None
        job = dict(row)
        if job.get("tapes"):
            try:
                job["tapes"] = json.loads(job["tapes"])
            except Exception:
                job["tapes"] = []
        return job
    
    # Note: get_files_on_tape is defined earlier in the file (line ~537)
    
    # =========================================================================
    # Autopilot System (Phase 2)
    # =========================================================================
    
    def _ensure_autopilot_tables(self):
        """Ensure all autopilot tables exist"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # Autopilot state table (for retry tracking, etc.)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS autopilot_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Autopilot alerts table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS autopilot_alerts (
                id TEXT PRIMARY KEY,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                job_id INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                acknowledged BOOLEAN DEFAULT 0,
                acknowledged_at TIMESTAMP,
                acknowledged_by TEXT
            )
        ''')
        
        # Autopilot actions log
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS autopilot_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT NOT NULL,
                target TEXT,
                status TEXT NOT NULL,
                message TEXT,
                correlation_id TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
    
    def get_autopilot_state(self, key: str, default=None):
        """Get autopilot state value"""
        self._ensure_autopilot_tables()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM autopilot_state WHERE key = ?', (key,))
        row = cursor.fetchone()
        if row:
            try:
                return json.loads(row['value'])
            except (TypeError, ValueError, json.JSONDecodeError):
                return row['value']
        return default
    
    def set_autopilot_state(self, key: str, value):
        """Set autopilot state value"""
        self._ensure_autopilot_tables()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO autopilot_state (key, value, updated_at)
            VALUES (?, ?, ?)
        ''', (key, json.dumps(value), now_utc_iso()))
        conn.commit()
    
    def add_autopilot_alert(self, alert: Dict):
        """Add an autopilot alert"""
        self._ensure_autopilot_tables()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO autopilot_alerts (id, level, message, job_id, timestamp, acknowledged)
            VALUES (?, ?, ?, ?, ?, 0)
        ''', (
            alert['id'],
            alert['level'],
            alert['message'],
            alert.get('job_id'),
            alert.get('timestamp', now_utc_iso())
        ))
        conn.commit()
    
    def get_autopilot_alerts(self, acknowledged: bool = False, level: str = None, limit: int = 50) -> List[Dict]:
        """Get autopilot alerts"""
        self._ensure_autopilot_tables()
        conn = self._get_conn()
        cursor = conn.cursor()
        
        sql = 'SELECT * FROM autopilot_alerts WHERE acknowledged = ?'
        params = [1 if acknowledged else 0]
        
        if level:
            sql += ' AND level = ?'
            params.append(level)
        
        sql += ' ORDER BY timestamp DESC LIMIT ?'
        params.append(limit)
        
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
    
    def acknowledge_autopilot_alert(self, alert_id: str, acknowledged_by: str = 'operator'):
        """Acknowledge an alert"""
        self._ensure_autopilot_tables()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE autopilot_alerts 
            SET acknowledged = 1, acknowledged_at = ?, acknowledged_by = ?
            WHERE id = ?
        ''', (now_utc_iso(), acknowledged_by, alert_id))
        conn.commit()
    
    def add_autopilot_action(self, action_type: str, target: str, status: str, 
                             message: str = None, correlation_id: str = None):
        """Log an autopilot action"""
        self._ensure_autopilot_tables()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO autopilot_actions (action_type, target, status, message, correlation_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (action_type, target, status, message, correlation_id))
        conn.commit()
    
    def get_recent_autopilot_actions(self, limit: int = 50) -> List[Dict]:
        """Get recent autopilot actions"""
        self._ensure_autopilot_tables()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM autopilot_actions 
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]
    
    def get_jobs_by_status(self, status: str) -> List[Dict]:
        """Get jobs by status"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC', (status,))
        return [dict(row) for row in cursor.fetchall()]
    
    def update_job_status(self, job_id: int, status: str, message: str = None):
        """Update job status with timestamps and optional message."""
        conn = self._get_conn()
        cursor = conn.cursor()

        fields = ['status = ?', 'updated_at = ?']
        values = [status, now_utc_iso()]

        if message:
            fields.append('status_message = ?')
            values.append(message)

            if status in ('error', 'failed'):
                fields.append('error = ?')
                values.append(message)

        timestamp_field = None
        if status == 'running':
            timestamp_field = 'started_at'
        elif status in ('completed', 'error', 'failed', 'cancelled'):
            timestamp_field = 'completed_at'

        if timestamp_field:
            fields.append(f'{timestamp_field} = CURRENT_TIMESTAMP')

        values.append(job_id)
        sql = f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?"
        cursor.execute(sql, values)
        conn.commit()
    
    def set_setting(self, key: str, value):
        """Set a system setting"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO settings (key, value)
            VALUES (?, ?)
        ''', (key, json.dumps(value) if not isinstance(value, str) else value))
        conn.commit()
    
    # Note: get_setting is defined earlier in the file (line ~852)
    
    # =========================================================================
    # Credential Storage
    # =========================================================================
    
    def _ensure_credentials_table(self):
        """Ensure credentials table exists"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS credentials (
                name TEXT PRIMARY KEY,
                username TEXT,
                password_encrypted TEXT,
                smb_path TEXT,
                display_name TEXT,
                domain TEXT,
                type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('PRAGMA table_info(credentials)')
        existing_columns = {row['name'] for row in cursor.fetchall()}
        if 'display_name' not in existing_columns:
            cursor.execute('ALTER TABLE credentials ADD COLUMN display_name TEXT')
        if 'domain' not in existing_columns:
            cursor.execute('ALTER TABLE credentials ADD COLUMN domain TEXT')
        if 'type' not in existing_columns:
            cursor.execute('ALTER TABLE credentials ADD COLUMN type TEXT')
        conn.commit()
    
    def store_credential(self, credential: Dict):
        """Store or update a credential"""
        self._ensure_credentials_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO credentials (
                name,
                username,
                password_encrypted,
                smb_path,
                display_name,
                domain,
                type,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            credential['name'],
            credential.get('username', ''),
            credential.get('password_encrypted', ''),
            credential.get('smb_path', ''),
            credential.get('display_name', ''),
            credential.get('domain', ''),
            credential.get('type', ''),
            now_utc_iso()
        ))
        conn.commit()
    
    def get_credential(self, name: str) -> Optional[Dict]:
        """Get a credential by name"""
        self._ensure_credentials_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM credentials WHERE name = ?', (name,))
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def list_credentials(self) -> List[Dict]:
        """List all credentials"""
        self._ensure_credentials_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM credentials ORDER BY name')
        return [dict(row) for row in cursor.fetchall()]
    
    def delete_credential(self, name: str):
        """Delete a credential"""
        self._ensure_credentials_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM credentials WHERE name = ?', (name,))
        conn.commit()

    # =========================================================================
    # Source Storage
    # =========================================================================

    def store_source(self, source: Dict):
        """Store or update a source."""
        self._ensure_sources_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO sources (
                id,
                source_type,
                source_path,
                display_name,
                username,
                password_encrypted,
                domain,
                rsync_user,
                rsync_host,
                rsync_port,
                rsync_key_ref,
                nfs_server,
                nfs_export,
                s3_bucket,
                s3_region,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            source['id'],
            source.get('source_type', ''),
            source.get('source_path', ''),
            source.get('display_name', ''),
            source.get('username', ''),
            source.get('password_encrypted', ''),
            source.get('domain', ''),
            source.get('rsync_user', ''),
            source.get('rsync_host', ''),
            source.get('rsync_port'),
            source.get('rsync_key_ref', ''),
            source.get('nfs_server', ''),
            source.get('nfs_export', ''),
            source.get('s3_bucket', ''),
            source.get('s3_region', ''),
            now_utc_iso(),
        ))
        conn.commit()

    def get_source(self, source_id: str) -> Optional[Dict]:
        """Get a source by id."""
        self._ensure_sources_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM sources WHERE id = ?', (source_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_sources(self) -> List[Dict]:
        """List all sources."""
        self._ensure_sources_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM sources ORDER BY id')
        return [dict(row) for row in cursor.fetchall()]

    def delete_source(self, source_id: str):
        """Delete a source."""
        self._ensure_sources_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM sources WHERE id = ?', (source_id,))
        conn.commit()
    
    # =========================================================================
    # Job Checkpoints
    # =========================================================================
    
    def _ensure_checkpoints_table(self):
        """Ensure checkpoints table exists"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS job_checkpoints (
                job_id INTEGER PRIMARY KEY,
                timestamp TIMESTAMP,
                last_file_index INTEGER,
                last_file_path TEXT,
                files_completed INTEGER,
                bytes_written INTEGER,
                current_tape TEXT,
                tape_position INTEGER,
                state TEXT,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            )
        ''')
        conn.commit()
    
    def save_job_checkpoint(self, job_id: int, checkpoint: Dict):
        """Save or update a job checkpoint"""
        self._ensure_checkpoints_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO job_checkpoints 
            (job_id, timestamp, last_file_index, last_file_path, files_completed, 
             bytes_written, current_tape, tape_position, state)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            job_id,
            checkpoint.get('timestamp'),
            checkpoint.get('last_file_index', 0),
            checkpoint.get('last_file_path', ''),
            checkpoint.get('files_completed', 0),
            checkpoint.get('bytes_written', 0),
            checkpoint.get('current_tape', ''),
            checkpoint.get('tape_position', 0),
            checkpoint.get('state', '{}')
        ))
        conn.commit()
    
    def get_job_checkpoint(self, job_id: int) -> Optional[Dict]:
        """Get checkpoint for a job"""
        self._ensure_checkpoints_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM job_checkpoints WHERE job_id = ?', (job_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def clear_job_checkpoint(self, job_id: int):
        """Clear checkpoint after job completes"""
        self._ensure_checkpoints_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM job_checkpoints WHERE job_id = ?', (job_id,))
        conn.commit()
    
    def get_interrupted_jobs(self) -> List[Dict]:
        """Get jobs that were interrupted and have checkpoints"""
        self._ensure_checkpoints_table()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT j.*, c.files_completed, c.bytes_written, c.timestamp as checkpoint_time
            FROM jobs j
            INNER JOIN job_checkpoints c ON j.id = c.job_id
            WHERE j.status IN ('interrupted', 'paused', 'error')
            ORDER BY j.created_at DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]
    
    # Note: get_job is defined earlier in the file (line ~315)
    
    # =========================================================================
    # Tape Quick Access (consolidated)
    # =========================================================================
    
    def get_tape(self, barcode: str) -> Optional[Dict]:
        """Get a single tape by barcode"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM tapes WHERE barcode = ?', (barcode,))
        row = cursor.fetchone()
        return dict(row) if row else None
    
    # =========================================================================
    # Configuration Export Support
    # =========================================================================
    
    # Note: get_settings is defined earlier in the file (line ~835)
    # Note: get_schedules is defined earlier in the file (line ~768)
    # Note: create_schedule is defined earlier in the file (line ~734)
    
    def get_tape_aliases(self) -> Dict:
        """Get tape aliases (barcode -> alias mapping)"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT barcode, alias FROM tapes WHERE alias IS NOT NULL')
        aliases = {}
        for row in cursor.fetchall():
            aliases[row['barcode']] = row['alias']
        return aliases
    
    # Note: get_active_jobs is defined earlier in the file (line ~349)
    
    def get_schedule(self, schedule_id: int) -> Optional[Dict]:
        """Get a single schedule by ID"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM schedules WHERE id = ?', (schedule_id,))
        row = cursor.fetchone()
        if not row:
            return None
        schedule = dict(row)
        schedule['tapes'] = json.loads(schedule['tapes']) if schedule['tapes'] else []
        if schedule.get('source_config'):
            try:
                schedule['source_config'] = json.loads(schedule['source_config'])
            except (TypeError, ValueError, json.JSONDecodeError):
                schedule['source_config'] = {}
        return schedule

    def add_schedule(self, schedule: Dict) -> int:
        """Insert a schedule record from a dict (used for imports)."""
        conn = self._get_conn()
        cursor = conn.cursor()

        source_config = schedule.get('source_config')
        if isinstance(source_config, str):
            try:
                source_config = json.loads(source_config)
            except (TypeError, ValueError, json.JSONDecodeError):
                source_config = {}

        cursor.execute('''
            INSERT INTO schedules 
            (name, smb_path, credential_name, cron, tapes, verify, compression, duplicate, enabled, source_config, last_run, created_at, backup_mode, source_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            schedule.get('name'),
            None,
            None,
            schedule.get('cron', ''),
            json.dumps(schedule.get('tapes', [])),
            schedule.get('verify', True),
            schedule.get('compression', 'zstd'),
            schedule.get('duplicate', False),
            schedule.get('enabled', True),
            json.dumps(source_config or {}),
            schedule.get('last_run'),
            schedule.get('created_at'),
            schedule.get('backup_mode', 'full'),
            schedule.get('source_id'),
        ))
        conn.commit()
        return cursor.lastrowid
    
    def update_schedule_enabled(self, schedule_id: int, enabled: bool):
        """Enable or disable a schedule"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE schedules SET enabled = ? WHERE id = ?',
            (enabled, schedule_id)
        )
        conn.commit()

    def delete_schedule(self, schedule_id: int):
        """Delete a backup schedule."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM schedules WHERE id = ?', (schedule_id,))
        conn.commit()

    def update_schedule(self, schedule_id: int, **kwargs) -> bool:
        """Update a backup schedule."""
        if not kwargs:
            return False
            
        fields = []
        values = []
        valid_fields = (
            'name', 'cron', 'tapes', 'verify', 'compression', 
            'duplicate', 'drive', 'backup_mode', 'enabled', 
            'source_id', 'source_config'
        )
        
        for key, value in kwargs.items():
            if key in valid_fields:
                fields.append(f"{key} = ?")
                if key in ('tapes', 'source_config'):
                    values.append(json.dumps(value))
                else:
                    values.append(value)
        
        if not fields:
            return False
            
        values.append(schedule_id)
        sql = f"UPDATE schedules SET {', '.join(fields)} WHERE id = ?"
        
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(sql, tuple(values))
        conn.commit()
        return cursor.rowcount > 0

    # =========================================================================
    # Backup Set/Snapshot Tracking
    # =========================================================================

    def get_backup_set(self, backup_set_id: str) -> Optional[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM backup_sets WHERE id = ?', (backup_set_id,))
        row = cursor.fetchone()
        if not row:
            return None
        result = dict(row)
        if result.get('sources'):
            try:
                result['sources'] = json.loads(result['sources'])
            except Exception:
                result['sources'] = []
        return result

    def add_backup_set(self, backup_set_id: str, sources: List[str]) -> None:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR IGNORE INTO backup_sets (id, sources) VALUES (?, ?)',
            (backup_set_id, json.dumps(sources))
        )
        conn.commit()

    def add_backup_snapshot(
        self,
        backup_set_id: str,
        job_id: int,
        manifest_path: str,
        total_files: int,
        total_bytes: int,
        tape_map: Dict,
    ) -> int:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO backup_snapshots
            (backup_set_id, job_id, manifest_path, total_files, total_bytes, tape_map)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            backup_set_id,
            job_id,
            manifest_path,
            total_files,
            total_bytes,
            json.dumps(tape_map or {})
        ))
        conn.commit()
        return cursor.lastrowid

    def get_latest_backup_snapshot(self, backup_set_id: str) -> Optional[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT * FROM backup_snapshots
            WHERE backup_set_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            ''',
            (backup_set_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        result = dict(row)
        if result.get('tape_map'):
            try:
                result['tape_map'] = json.loads(result['tape_map'])
            except Exception:
                result['tape_map'] = {}
        return result

    def get_backup_sets(self) -> List[Dict]:
        """List all unique backup sets."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM backup_sets ORDER BY id ASC')
        rows = cursor.fetchall()
        sets = []
        for row in rows:
            d = dict(row)
            if d.get('sources'):
                try:
                    d['sources'] = json.loads(d['sources'])
                except Exception:
                    d['sources'] = []
            sets.append(d)
        return sets

    def get_backup_snapshots(self, backup_set_id: str) -> List[Dict]:
        """Get all snapshots for a backup set."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT * FROM backup_snapshots
            WHERE backup_set_id = ?
            ORDER BY created_at DESC
            ''',
            (backup_set_id,)
        )
        rows = cursor.fetchall()
        snapshots = []
        for row in rows:
            d = dict(row)
            if d.get('tape_map'):
                try:
                    d['tape_map'] = json.loads(d['tape_map'])
                except Exception:
                    d['tape_map'] = {}
            snapshots.append(d)
        return snapshots

    def get_backup_snapshot(self, snapshot_id: int) -> Optional[Dict]:
        """Get a specific snapshot by ID."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM backup_snapshots WHERE id = ?', (snapshot_id,))
        row = cursor.fetchone()
        if not row:
            return None
        result = dict(row)
        if result.get('tape_map'):
            try:
                result['tape_map'] = json.loads(result['tape_map'])
            except Exception:
                result['tape_map'] = {}
        return result

    def get_checksum_catalog(self) -> Dict[str, List[str]]:
        """Return mapping of checksum -> list of tape barcodes."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT checksum, tape_barcode
            FROM archived_files
            WHERE checksum IS NOT NULL AND checksum != ''
        ''')
        catalog: Dict[str, List[str]] = {}
        for row in cursor.fetchall():
            checksum = row['checksum']
            catalog.setdefault(checksum, []).append(row['tape_barcode'])
        return catalog

    def get_archived_files_for_job(self, job_id: int) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT file_path, file_size, checksum, tape_barcode, tape_position
            FROM archived_files
            WHERE job_id = ?
            ORDER BY file_path
        ''', (job_id,))
        return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # Diagnostics Reports
    # =========================================================================

    def add_diagnostics_report(
        self,
        job_id: int,
        status: str,
        summary: str,
        report_json_path: str,
        report_text_path: str,
    ) -> int:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO diagnostics_reports
            (job_id, status, summary, report_json_path, report_text_path)
            VALUES (?, ?, ?, ?, ?)
        ''', (job_id, status, summary, report_json_path, report_text_path))
        conn.commit()
        return cursor.lastrowid

    def get_latest_diagnostics_report(self) -> Optional[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM diagnostics_reports ORDER BY created_at DESC LIMIT 1'
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_diagnostics_reports(self, limit: int = 10) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM diagnostics_reports ORDER BY created_at DESC LIMIT ?',
            (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_diagnostics_report(self, report_id: int) -> Optional[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM diagnostics_reports WHERE id = ?',
            (report_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    # =========================================================================
    # Verification Reports
    # =========================================================================

    def add_verification_report(self, report: Dict) -> int:
        """Add a verification report."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO verification_reports 
            (job_id, tape_barcode, files_checked, files_failed, bytes_checked, 
             duration_seconds, failure_details, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            report.get('job_id'),
            report.get('tape_barcode'),
            report.get('files_checked', 0),
            report.get('files_failed', 0),
            report.get('bytes_checked', 0),
            report.get('duration_seconds', 0),
            json.dumps(report.get('failure_details', [])),
            datetime.now(timezone.utc).isoformat()
        ))
        
        report_id = cursor.lastrowid
        conn.commit()
        return report_id

    def get_verification_reports(self, limit: int = 50) -> List[Dict]:
        """Get recent verification reports."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM verification_reports
            ORDER BY created_at DESC
            LIMIT ?
        ''', (limit,))
        
        reports = []
        for row in cursor.fetchall():
            r = dict(row)
            if r.get('failure_details'):
                try:
                    r['failure_details'] = json.loads(r['failure_details'])
                except:
                    r['failure_details'] = []
            reports.append(r)
        return reports

    def get_verification_report(self, report_id: int) -> Optional[Dict]:
        """Get a specific verification report."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM verification_reports WHERE id = ?', (report_id,))
        row = cursor.fetchone()
        
        if row:
            r = dict(row)
            if r.get('failure_details'):
                try:
                    r['failure_details'] = json.loads(r['failure_details'])
                except:
                    r['failure_details'] = []
            return r
        return None

    def get_files_by_tape(self, tape_barcode: str) -> List[Dict]:
        """Get all archived files for a specific tape."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT file_path, file_path_on_tape, file_size, checksum
            FROM archived_files
            WHERE tape_barcode = ?
        ''', (tape_barcode,))
        
        return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # User Preferences
    # =========================================================================

    def get_user_preference(self, user_id: str, key: str) -> Optional[str]:
        """Get a specific user preference."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT preference_value FROM user_preferences
            WHERE user_id = ? AND preference_key = ?
        ''', (user_id, key))
        
        row = cursor.fetchone()
        return row['preference_value'] if row else None

    def set_user_preference(self, user_id: str, key: str, value: str):
        """Set a user preference (upsert)."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO user_preferences (user_id, preference_key, preference_value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, preference_key) DO UPDATE SET
                preference_value = excluded.preference_value,
                updated_at = excluded.updated_at
        ''', (user_id, key, value, datetime.now(timezone.utc).isoformat()))
        
        conn.commit()

    # =========================================================================
    # Webhook Management
    # =========================================================================

    def add_webhook(self, url: str, name: str = None, event_types: List[str] = None, secret: str = None) -> int:
        """Add a new webhook configuration."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        events_str = ",".join(event_types) if event_types else "*"
        
        cursor.execute('''
            INSERT INTO webhooks (url, name, event_types, secret)
            VALUES (?, ?, ?, ?)
        ''', (url, name, events_str, secret))
        
        webhook_id = cursor.lastrowid
        conn.commit()
        return webhook_id

    def get_webhooks(self, active_only: bool = False) -> List[Dict]:
        """Retrieve all defined webhooks."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        query = "SELECT * FROM webhooks"
        if active_only:
            query += " WHERE is_active = 1"
            
        cursor.execute(query)
        results = []
        for row in cursor.fetchall():
            d = dict(row)
            if d['event_types'] == '*':
                d['event_types'] = [] # All events
            else:
                d['event_types'] = d['event_types'].split(',')
            results.append(d)
        return results

    def delete_webhook(self, webhook_id: int) -> bool:
        """Remove a webhook configuration."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))
        conn.commit()
        return cursor.rowcount > 0

    def update_webhook(self, webhook_id: int, updates: Dict) -> bool:
        """Update an existing webhook."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        fields = []
        values = []
        for k, v in updates.items():
            if k == 'event_types' and isinstance(v, list):
                v = ",".join(v)
            fields.append(f"{k} = ?")
            values.append(v)
            
        if not fields:
            return False
            
        values.append(webhook_id)
        cursor.execute(f"UPDATE webhooks SET {', '.join(fields)} WHERE id = ?", tuple(values))
        conn.commit()
        return cursor.rowcount > 0

    def get_all_user_preferences(self, user_id: str) -> Dict[str, str]:
        """Get all preferences for a user as a dictionary."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT preference_key, preference_value
            FROM user_preferences
            WHERE user_id = ?
        ''', (user_id,))
        
        return {row['preference_key']: row['preference_value'] for row in cursor.fetchall()}
    # =========================================================================
    # Diagnostics Reports
    # =========================================================================

    def add_diagnostics_report(self, status: str, json_path: str, text_path: str, summary: str = None) -> int:
        """Add a new diagnostic report entry."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO diagnostics_reports (overall_status, report_json_path, report_text_path, summary)
            VALUES (?, ?, ?, ?)
        ''', (status, json_path, text_path, summary))
        conn.commit()
        return cursor.lastrowid

    def get_diagnostics_reports(self, limit: int = 20) -> List[Dict]:
        """Get list of historical diagnostic reports."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM diagnostics_reports ORDER BY timestamp DESC LIMIT ?', (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def get_diagnostics_report(self, report_id: int) -> Optional[Dict]:
        """Get a specific diagnostic report."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM diagnostics_reports WHERE id = ?', (report_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
