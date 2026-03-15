import time
import threading
from typing import Dict, List, Optional, Any
from backend.utils.datetime import now_utc_iso

class MetricsService:
    """
    Service for collecting and exposing internal system metrics.
    Supports both JSON and Prometheus (OpenMetrics) formats.
    """
    
    # Prometheus histogram buckets for API latency
    LATENCY_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
    
    def __init__(self, db):
        self.db = db
        self._api_timings: List[float] = []
        self._lock = threading.Lock()
        self._histogram_counts = {b: 0 for b in self.LATENCY_BUCKETS}
        self._histogram_counts[float('inf')] = 0
        self._total_requests = 0
        self._total_latency_sum = 0.0
        
    def record_api_timing(self, duration_s: float):
        """Record the duration of an API request."""
        with self._lock:
            self._api_timings.append(duration_s)
            self._total_requests += 1
            self._total_latency_sum += duration_s
            
            # Update histogram buckets
            for bucket in self.LATENCY_BUCKETS:
                if duration_s <= bucket:
                    self._histogram_counts[bucket] += 1
            self._histogram_counts[float('inf')] += 1  # +Inf always incremented
            
            # Keep last 1000 requests for average calculation
            if len(self._api_timings) > 1000:
                self._api_timings = self._api_timings[-1000:]
    
    def _get_tape_metrics(self) -> Dict[str, int]:
        """Get tape counts by status."""
        metrics = {'online': 0, 'offline': 0, 'error': 0, 'total': 0}
        try:
            tapes = self.db.get_tape_inventory()
            for tape in tapes:
                status = tape.get('status', 'offline').lower()
                if status in metrics:
                    metrics[status] += 1
                metrics['total'] += 1
        except Exception:
            pass
        return metrics
    
    def _get_job_metrics(self) -> Dict[str, int]:
        """Get job counts by status."""
        metrics = {'completed': 0, 'failed': 0, 'running': 0, 'pending': 0, 'total': 0}
        try:
            # Query job statistics
            result = self.db.execute("""
                SELECT status, COUNT(*) as count FROM jobs GROUP BY status
            """).fetchall()
            for row in result:
                status = row['status'].lower() if row['status'] else 'pending'
                if status in metrics:
                    metrics[status] = row['count']
                metrics['total'] += row['count']
        except Exception:
            pass
        return metrics
    
    def _get_data_written_bytes(self) -> int:
        """Get total bytes written to tape."""
        try:
            result = self.db.execute("""
                SELECT COALESCE(SUM(file_size), 0) as total_bytes FROM archived_files WHERE tape_barcode IS NOT NULL
            """).fetchone()
            return result['total_bytes'] if result else 0
        except Exception:
            return 0
                
    def get_metrics(self) -> Dict[str, Any]:
        """Aggregate and return current system metrics (JSON format)."""
        tape_metrics = self._get_tape_metrics()
        job_metrics = self._get_job_metrics()
        
        with self._lock:
            timings = self._api_timings
            if timings:
                avg_time = sum(timings) / len(timings)
                max_time = max(timings)
                count = len(timings)
            else:
                avg_time = 0
                max_time = 0
                count = 0
        
        return {
            "timestamp": now_utc_iso(),
            "resources": {
                "tapes_total": tape_metrics['total'],
                "tapes_online": tape_metrics['online'],
                "tapes_offline": tape_metrics['offline'],
                "tapes_error": tape_metrics['error'],
                "jobs_total": job_metrics['total'],
                "jobs_completed": job_metrics['completed'],
                "jobs_failed": job_metrics['failed'],
                "jobs_running": job_metrics['running'],
                "data_written_bytes": self._get_data_written_bytes(),
            },
            "performance": {
                "api_requests_tracked": count,
                "api_avg_latency_seconds": round(avg_time, 4),
                "api_max_latency_seconds": round(max_time, 4),
                "api_total_requests": self._total_requests,
            }
        }
    
    def get_prometheus_metrics(self) -> str:
        """
        Return metrics in Prometheus/OpenMetrics text format.
        Compatible with Prometheus scraping and Grafana visualization.
        """
        lines = []
        
        # Header
        lines.append("# HELP fossilsafe_info FossilSafe appliance information")
        lines.append("# TYPE fossilsafe_info gauge")
        lines.append('fossilsafe_info{version="1.0"} 1')
        lines.append("")
        
        # Tape metrics
        tape_metrics = self._get_tape_metrics()
        lines.append("# HELP fossilsafe_tapes_total Number of tapes by status")
        lines.append("# TYPE fossilsafe_tapes_total gauge")
        lines.append(f'fossilsafe_tapes_total{{status="online"}} {tape_metrics["online"]}')
        lines.append(f'fossilsafe_tapes_total{{status="offline"}} {tape_metrics["offline"]}')
        lines.append(f'fossilsafe_tapes_total{{status="error"}} {tape_metrics["error"]}')
        lines.append("")
        
        # Job metrics
        job_metrics = self._get_job_metrics()
        lines.append("# HELP fossilsafe_jobs_total Number of jobs by status")
        lines.append("# TYPE fossilsafe_jobs_total gauge")
        lines.append(f'fossilsafe_jobs_total{{status="completed"}} {job_metrics["completed"]}')
        lines.append(f'fossilsafe_jobs_total{{status="failed"}} {job_metrics["failed"]}')
        lines.append(f'fossilsafe_jobs_total{{status="running"}} {job_metrics["running"]}')
        lines.append(f'fossilsafe_jobs_total{{status="pending"}} {job_metrics["pending"]}')
        lines.append("")
        
        # Data written
        data_bytes = self._get_data_written_bytes()
        lines.append("# HELP fossilsafe_data_written_bytes Total bytes written to tape")
        lines.append("# TYPE fossilsafe_data_written_bytes counter")
        lines.append(f"fossilsafe_data_written_bytes {data_bytes}")
        lines.append("")
        
        # API latency histogram
        with self._lock:
            lines.append("# HELP fossilsafe_api_latency_seconds API request latency histogram")
            lines.append("# TYPE fossilsafe_api_latency_seconds histogram")
            cumulative = 0
            for bucket in self.LATENCY_BUCKETS:
                cumulative += self._histogram_counts.get(bucket, 0) - cumulative
                # Actually need cumulative count
                pass
            
            # Rebuild cumulative properly
            cumulative = 0
            for bucket in self.LATENCY_BUCKETS:
                bucket_count = sum(1 for t in self._api_timings if t <= bucket)
                lines.append(f'fossilsafe_api_latency_seconds_bucket{{le="{bucket}"}} {bucket_count}')
            lines.append(f'fossilsafe_api_latency_seconds_bucket{{le="+Inf"}} {len(self._api_timings)}')
            lines.append(f"fossilsafe_api_latency_seconds_sum {self._total_latency_sum:.4f}")
            lines.append(f"fossilsafe_api_latency_seconds_count {self._total_requests}")
        lines.append("")
        
        # Timestamp
        lines.append(f"# Generated at {now_utc_iso()}")
        
        return "\n".join(lines)
