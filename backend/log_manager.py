import threading
import queue
import time
import json
import sys
import os
import sqlite3
from typing import Optional, Dict, Callable
from backend.utils.datetime import now_utc_iso

try:
    from flask import has_request_context, g, request
except ImportError:
    has_request_context = lambda: False
    g = None
    request = None

from backend.utils.hashing import HashingManager

class LogManager:
    """
    Centralized log manager with:
    - In-memory buffering (deduplicated)
    - Database persistence
    - WebSocket emission
    - Structured JSON output to stdout
    """
    
    def __init__(self, db, max_logs=10000, socket_emit_func: Optional[Callable] = None):
        # Database persistence via background worker to avoid connection leaks
        self.db = db
        self._db_queue = queue.Queue()
        self._worker_thread = threading.Thread(target=self._db_worker, daemon=True)
        self._worker_thread.start()
        
        self.logs = []
        self.max_logs = max_logs
        self.lock = threading.Lock()
        self._seq = 0
        self._dedupe_window_s = 30
        self._recent_messages = {}
        self.socket_emit_func = socket_emit_func
        self._load_from_db()

    def _db_worker(self):
        """Persistent worker thread to handle database persistence."""
        while True:
            try:
                entry = self._db_queue.get()
                if entry is None: # Shutdown signal
                    break
                
                try:
                    self.db.add_log(entry)
                except Exception as e:
                    # Log to stderr directly since log_manager is failing
                    import json
                    import sys
                    print(json.dumps({"level": "error", "message": f"Failed to persist log: {e}"}), file=sys.stderr)
                finally:
                    # Crucially release connection after each write
                    if hasattr(self.db, "release_connection"):
                        self.db.release_connection()
                
                self._db_queue.task_done()
            except Exception:
                time.sleep(1) # Prevent tight loop on error

    def _persist_log(self, entry):
        """Queue a log entry for database persistence."""
        if self._db_queue:
            self._db_queue.put(entry)
    
    def _load_from_db(self):
        """Load recent logs from database on startup."""
        if not self.db:
            return
        try:
            logs = self.db.get_logs(limit=1000)
            with self.lock:
                self.logs = []
                for entry in logs:
                    self._seq += 1
                    hydrated = dict(entry)
                    hydrated["seq"] = self._seq
                    self.logs.append(hydrated)
        except (sqlite3.Error, OSError) as e:
            print(json.dumps({
                "level": "error",
                "component": "log_manager",
                "message": f"Failed to load logs from database: {e}",
                "timestamp": now_utc_iso()
            }), file=sys.stderr)
    
    def set_socket_emitter(self, func: Callable):
        """Set the function to emit WebSocket events."""
        self.socket_emit_func = func

    def _get_request_id(self) -> Optional[str]:
        """Try to retrieve request_id from Flask context."""
        if has_request_context() and g:
            return getattr(g, 'request_id', None)
        return None

    def get(self, level='all', category=None, limit=100, offset=0, since_id=None, since_seq=None):
        """
        Retrieve logs with filtering and pagination.
        Checks in-memory logs first, then falls back to DB if needed for history.
        """
        with self.lock:
            # Filter in-memory logs
            filtered = self.logs
            
            if level and level != 'all':
                filtered = [l for l in filtered if l.get('level') == level]
            
            if category:
                filtered = [l for l in filtered if l.get('category') == category]
            
            if since_id:
                try:
                    since_id_int = int(since_id)
                    filtered = [l for l in filtered if l.get('id') > since_id_int]
                except (ValueError, TypeError):
                    pass
            
            if since_seq:
                try:
                    since_seq_int = int(since_seq)
                    filtered = [l for l in filtered if l.get('seq') > since_seq_int]
                except (ValueError, TypeError):
                    pass

            # Sort by sequence descending (most recent first)
            filtered.sort(key=lambda x: x.get('seq', 0), reverse=True)
            
            total = len(filtered)
            paged = filtered[offset : offset + limit]
            
            return {
                'logs': paged,
                'total': total,
                'limit': limit,
                'offset': offset,
                'last_seq': self._seq
            }

    def _redact_secrets(self, data: object) -> object:
        """Recursively redact secrets from dicts, lists, or strings."""
        if isinstance(data, str):
            # Redact common credential patterns in strings (simple version)
            # This handles case-insensitive matches for password, api_key, etc.
            lower_data = data.lower()
            if any(k in lower_data for k in ("password=", "api_key=", "token=", "secret=")):
                # If it looks like a key-value string, try to redact values
                import re
                return re.sub(r'(password|api_key|token|secret|key)=([^&\s,]+)', r'\1=<redacted>', data, flags=re.IGNORECASE)
            return data
            
        if isinstance(data, dict):
            redacted = {}
            for k, v in data.items():
                k_lower = k.lower()
                if any(secret_key in k_lower for secret_key in ("password", "api_key", "token", "secret", "key", "credential")):
                    redacted[k] = "<redacted>"
                else:
                    redacted[k] = self._redact_secrets(v)
            return redacted
            
        if isinstance(data, list):
            return [self._redact_secrets(item) for item in data]
            
        return data

    def add(self, level: str, message: str, category: str = 'system', details: Optional[Dict] = None):
        """
        Add a log entry.
        
        Args:
            level: 'info', 'warning', 'error'
            message: Human readable message
            category: Component/Category name
            details: Optional dictionary or string of details
        """
        # Redact secrets before any processing or storage
        message = str(self._redact_secrets(message))
        details = self._redact_secrets(details)
        
        now = time.time()
        
        # Deduplication to prevent log storms
        # details can be dict, convert to string for hashing
        details_str = json.dumps(details) if isinstance(details, dict) else str(details) if details else None
        
        dedupe_key = (level, category, message, details_str)
        with self.lock:
            last_seen = self._recent_messages.get(dedupe_key)
            if last_seen is not None and now - last_seen < self._dedupe_window_s:
                return None
            
            # Prune recent messages if too large
            if len(self._recent_messages) > 2000:
                cutoff = now - self._dedupe_window_s
                self._recent_messages = {
                    key: timestamp
                    for key, timestamp in self._recent_messages.items()
                    if timestamp >= cutoff
                }
            self._recent_messages[dedupe_key] = now
            self._seq += 1
            
            request_id = self._get_request_id()
            
            log_entry = {
                'id': int(time.time() * 1000000),
                'seq': self._seq,
                'timestamp': now_utc_iso(),
                'level': level,
                'message': message,
                'category': category,
                'details': details, # Keep as is (dict or str) for internal storage
                'request_id': request_id
            }
            
            # Compute Hash
            # Get previous hash from last in-memory log or DB (we only check in-memory for speed/simplicity of chain)
            # If in-memory is empty, we should technically query DB for last hash, but for now allow "restart point"
            previous_hash = None
            if self.logs:
                previous_hash = self.logs[-1].get('hash')
            
            # WORM Compliance Check (Enterprise)
            # We don't block ADDING logs, but we ensure they are hashed.
            # Deletion is where WORM matters.
            
            log_hash = HashingManager.compute_log_hash(log_entry, previous_hash)
            log_entry['hash'] = log_hash
            log_entry['previous_hash'] = previous_hash
            
            self.logs.append(log_entry)
            if len(self.logs) > self.max_logs:
                self.logs = self.logs[-self.max_logs:]
        
        # Persist to database — fire-and-forget to avoid blocking the request thread
        if self.db:
            self._persist_log(log_entry.copy())
        
        # Emit via WebSocket
        if self.socket_emit_func:
            try:
                self.socket_emit_func('log_entry', log_entry)
            except Exception:
                pass
        
        # Structured Output to Stdout
        # Convert details to serializable if needed
        stdout_entry = log_entry.copy()
        # Remove internal ID/Seq if cluttering, but keeping them is fine.
        print(json.dumps(stdout_entry, default=str))
        
        return log_entry
    
    def cleanup_old_logs(self, retention_days: int = 30) -> int:
        """
        Clean up logs older than the specified retention period.
        
        Args:
            retention_days: Number of days to retain logs (0 = No Logs/Delete All)
        
        Returns:
            Number of logs deleted
        """
        if not self.db or retention_days < 0:
            return 0
        
        try:

            # Calculate cutoff date
            cutoff_timestamp = time.time() - (retention_days * 24 * 60 * 60)
            cutoff_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(cutoff_timestamp))
            
            # Delete from database
            result = self.db.execute(
                "DELETE FROM logs WHERE timestamp < ?",
                (cutoff_iso,)
            )
            deleted_count = result.rowcount if hasattr(result, 'rowcount') else 0
            self.db.commit()
            
            # Also clean in-memory logs
            with self.lock:
                original_count = len(self.logs)
                self.logs = [
                    log for log in self.logs 
                    if log.get('timestamp', '') >= cutoff_iso
                ]
                deleted_count = max(deleted_count, original_count - len(self.logs))
            
            if deleted_count > 0:
                self.add('info', f'Cleaned up {deleted_count} logs older than {retention_days} days', 'log_manager')
            
            return deleted_count
            
        except (sqlite3.Error, OSError) as e:
            print(json.dumps({
                "level": "error",
                "component": "log_manager",
                "message": f"Failed to cleanup logs: {e}",
                "timestamp": now_utc_iso()
            }), file=sys.stderr)
            return 0
    
    def get_log_stats(self) -> Dict:
        """Get log statistics for monitoring."""
        with self.lock:
            level_counts = {}
            for log in self.logs:
                level = log.get('level', 'unknown')
                level_counts[level] = level_counts.get(level, 0) + 1
            
            return {
                'total_in_memory': len(self.logs),
                'by_level': level_counts,
                'max_logs': self.max_logs
            }

