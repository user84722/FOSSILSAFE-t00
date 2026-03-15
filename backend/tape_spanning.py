"""
Tape Spanning Manager - Handles multi-volume archives across multiple tapes.
"""
import os
import subprocess
import threading
from typing import Dict, Optional, Callable, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SpanningState(Enum):
    """State of a spanning operation."""
    IDLE = 'idle'
    RUNNING = 'running'
    WAITING_FOR_TAPE = 'waiting_for_tape'
    TAPE_CHANGE_IN_PROGRESS = 'tape_change_in_progress'
    COMPLETED = 'completed'
    FAILED = 'failed'


@dataclass
class TapeSequence:
    """Tracks tapes used in a spanning archive."""
    job_id: int
    tapes: List[Dict] = field(default_factory=list)
    current_index: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    
    def add_tape(self, barcode: str, start_file: str = None):
        """Add a tape to the sequence."""
        self.tapes.append({
            'barcode': barcode,
            'index': len(self.tapes),
            'start_file': start_file,
            'added_at': datetime.now().isoformat(),
        })
        self.current_index = len(self.tapes) - 1
    
    def get_current_tape(self) -> Optional[Dict]:
        """Get current tape info."""
        if 0 <= self.current_index < len(self.tapes):
            return self.tapes[self.current_index]
        return None


@dataclass
class SpanningSession:
    """Active spanning session."""
    job_id: int
    state: SpanningState = SpanningState.IDLE
    tape_sequence: TapeSequence = None
    total_bytes_written: int = 0
    current_tape_bytes: int = 0
    error: Optional[str] = None
    waiting_since: Optional[datetime] = None
    callback: Optional[Callable] = None


class TapeSpanningManager:
    """Manages multi-volume tape spanning operations."""
    
    def __init__(self, tape_controller, socketio=None, log_callback=None):
        self.tape_controller = tape_controller
        self.socketio = socketio
        self.log_callback = log_callback
        self._sessions: Dict[int, SpanningSession] = {}
        self._lock = threading.Lock()
        self._tape_change_event = threading.Event()
    
    def _log(self, message: str, level: str = 'info'):
        """Log a message."""
        if self.log_callback:
            self.log_callback(message, level)
        else:
            print(f"[{level.upper()}] {message}")
    
    def _emit(self, event: str, data: Dict):
        """Emit a SocketIO event."""
        if self.socketio:
            self.socketio.emit(event, data, namespace='/')
    
    def create_session(self, job_id: int, initial_tape: str) -> SpanningSession:
        """Create a new spanning session for a job."""
        with self._lock:
            session = SpanningSession(
                job_id=job_id,
                state=SpanningState.IDLE,
                tape_sequence=TapeSequence(job_id=job_id)
            )
            session.tape_sequence.add_tape(initial_tape)
            self._sessions[job_id] = session
            return session
    
    def get_session(self, job_id: int) -> Optional[SpanningSession]:
        """Get active session for a job."""
        return self._sessions.get(job_id)
    
    def request_tape_change(self, job_id: int, reason: str = "Tape full") -> bool:
        """
        Request a tape change, pausing the job until new tape is ready.
        
        This is called when tar reports end of media.
        """
        session = self.get_session(job_id)
        if not session:
            self._log(f"No spanning session for job {job_id}", 'error')
            return False
        
        session.state = SpanningState.WAITING_FOR_TAPE
        session.waiting_since = datetime.now()
        
        current_tape = session.tape_sequence.get_current_tape()
        tape_num = len(session.tape_sequence.tapes)
        
        self._log(f"Job {job_id}: Requesting tape change. Tape {tape_num} ({current_tape['barcode']}) full.")
        
        # Emit event for UI
        self._emit('tape_change_required', {
            'job_id': job_id,
            'reason': reason,
            'current_tape': current_tape['barcode'],
            'tape_number': tape_num,
            'bytes_written': session.total_bytes_written,
        })
        
        return True
    
    def provide_next_tape(self, job_id: int, barcode: str) -> bool:
        """
        Provide the next tape for a spanning operation.
        Called by UI when user selects next tape.
        """
        session = self.get_session(job_id)
        if not session:
            return False
        
        if session.state != SpanningState.WAITING_FOR_TAPE:
            self._log(f"Job {job_id} not waiting for tape", 'warning')
            return False
        
        session.state = SpanningState.TAPE_CHANGE_IN_PROGRESS
        
        try:
            # Unload current tape
            self.tape_controller.unload_tape()
            
            # Load new tape
            self.tape_controller.load_tape(barcode)
            
            # Add to sequence
            session.tape_sequence.add_tape(barcode)
            session.current_tape_bytes = 0
            session.state = SpanningState.RUNNING
            
            tape_num = len(session.tape_sequence.tapes)
            self._log(f"Job {job_id}: Tape changed to {barcode} (tape {tape_num})")
            
            # Signal the waiting tar process
            self._tape_change_event.set()
            
            # Emit success
            self._emit('tape_change_complete', {
                'job_id': job_id,
                'new_tape': barcode,
                'tape_number': tape_num,
            })
            
            return True
            
        except Exception as e:
            session.state = SpanningState.FAILED
            session.error = str(e)
            self._log(f"Tape change failed: {e}", 'error')
            return False
    
    def wait_for_tape(self, job_id: int, timeout: int = 3600) -> bool:
        """
        Wait for tape to be available (called from backup process).
        
        Args:
            job_id: Job ID
            timeout: Max seconds to wait (default 1 hour)
            
        Returns:
            True if tape available, False if timeout/cancelled
        """
        session = self.get_session(job_id)
        if not session:
            return False
        
        self._tape_change_event.clear()
        
        # Wait for tape change
        result = self._tape_change_event.wait(timeout=timeout)
        
        if not result:
            self._log(f"Job {job_id}: Timed out waiting for tape change", 'warning')
            session.state = SpanningState.FAILED
            session.error = "Tape change timeout"
            return False
        
        return session.state == SpanningState.RUNNING
    
    def complete_session(self, job_id: int):
        """Mark spanning session as complete."""
        session = self.get_session(job_id)
        if session:
            session.state = SpanningState.COMPLETED
            self._log(f"Job {job_id}: Spanning complete. Used {len(session.tape_sequence.tapes)} tapes.")
    
    def fail_session(self, job_id: int, error: str):
        """Mark spanning session as failed."""
        session = self.get_session(job_id)
        if session:
            session.state = SpanningState.FAILED
            session.error = error
    
    def get_session_status(self, job_id: int) -> Optional[Dict]:
        """Get current status of a spanning session."""
        session = self.get_session(job_id)
        if not session:
            return None
        
        return {
            'job_id': job_id,
            'state': session.state.value,
            'tape_count': len(session.tape_sequence.tapes),
            'current_tape': session.tape_sequence.get_current_tape(),
            'total_bytes': session.total_bytes_written,
            'waiting_since': session.waiting_since.isoformat() if session.waiting_since else None,
            'error': session.error,
        }
    
    def build_tar_command(self, job_id: int, device: str, files_list: str, 
                          extra_args: List[str] = None) -> List[str]:
        """
        Build tar command with multi-volume support.
        
        The new-volume-script is called when tape is full.
        """
        # Create the tape change script
        script_path = f"/tmp/fossilsafe_tape_change_{job_id}.sh"
        self._create_tape_change_script(script_path, job_id)
        
        cmd = [
            'tar',
            '-cvf', device,
            '--multi-volume',
            f'--new-volume-script={script_path}',
            '-T', files_list,
        ]
        
        if extra_args:
            cmd.extend(extra_args)
        
        return cmd
    
    def _create_tape_change_script(self, path: str, job_id: int):
        """Create the script that tar calls when it needs a new volume."""
        script = f"""#!/bin/bash
# FossilSafe tape change script for job {job_id}
# This signals the backend to request a tape change

curl -X POST http://localhost:5000/api/spanning/{job_id}/request-change \\
    -H "Content-Type: application/json" \\
    -d '{{"reason": "Tape full"}}' || true

# Wait for tape change to complete
MAX_WAIT=3600
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    STATUS=$(curl -s http://localhost:5000/api/spanning/{job_id}/status | grep -o '"state":"[^"]*"' | cut -d'"' -f4)
    if [ "$STATUS" = "running" ]; then
        exit 0  # Continue with new tape
    elif [ "$STATUS" = "failed" ]; then
        exit 1  # Abort
    fi
    sleep 2
    WAITED=$((WAITED + 2))
done

exit 1  # Timeout
"""
        with open(path, 'w') as f:
            f.write(script)
        os.chmod(path, 0o755)
