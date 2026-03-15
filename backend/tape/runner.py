import subprocess
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple


@dataclass
class CommandResult:
    command: List[str]
    stdout: str
    stderr: str
    returncode: int
    duration: float
    timed_out: bool
    error_type: Optional[str] = None
    error_message: Optional[str] = None


class CommandHistory:
    def __init__(self, limit: int = 50):
        self.limit = limit
        self.entries: List[Dict[str, object]] = []

    def add(self, entry: Dict[str, object]) -> None:
        self.entries.insert(0, entry)
        if len(self.entries) > self.limit:
            self.entries = self.entries[:self.limit]

    def to_list(self) -> List[Dict[str, object]]:
        return list(self.entries)

    def to_list_redacted(self) -> List[Dict[str, object]]:
        redacted_entries = []
        for entry in self.entries:
            sanitized = dict(entry)
            command = sanitized.get("command")
            if isinstance(command, list):
                sanitized["command"] = [
                    "<redacted>" if isinstance(token, str) and any(key in token.lower() for key in ("api_key", "token", "password"))
                    else token
                    for token in command
                ]
            if "stdout" in sanitized:
                sanitized["stdout"] = "<redacted>"
            if "stderr" in sanitized:
                sanitized["stderr"] = "<redacted>"
            redacted_entries.append(sanitized)
        return redacted_entries


def default_timeouts() -> Dict[str, int]:
    return {
        "mtx_status": 60,
        "mtx_inquiry": 60,
        "mtx_load": 300,
        "mtx_unload": 300,
        "mtx_transfer": 300,
        "mtx_inventory": 90,
        "mt_status": 60,
        "mt_erase": 7200,
        "ltfs": 900,
        "mkltfs": 5400,
        "ltfsck": 5400,
        "sg_inq": 30,
    }


import random

class ChaosInjector:
    def __init__(self):
        # Default: Disabled
        self.config = {
            "enabled": False,
            "timeout_rate": 0.0,
            "error_rate": 0.0,
            "busy_rate": 0.0
        }

    def update_config(self, new_config: Dict):
        self.config.update(new_config)

    def maybe_fail(self, command: List[str]) -> Optional[CommandResult]:
        if not self.config.get("enabled"):
            return None
            
        # Don't fail status checks too aggressively or we assume library is dead
        # But we do want to test retries on status checks
        
        # Sim Timeout
        if random.random() < self.config.get("timeout_rate", 0.0):
             return CommandResult(
                 command=command,
                 stdout="",
                 stderr="Transformation timed out.",
                 returncode=124,
                 duration=10.0,
                 timed_out=True,
                 error_type="timeout",
                 error_message="Simulated Chaos Timeout"
             )

        # Sim Device Busy
        if random.random() < self.config.get("busy_rate", 0.0):
             return CommandResult(
                 command=command,
                 stdout="Device or resource busy",
                 stderr="Device busy",
                 returncode=1,
                 duration=0.1,
                 timed_out=False,
                 error_type="device_busy",
                 error_message="Simulated Chaos Busy"
             )

        # Sim Medium Error (IO Error)
        if random.random() < self.config.get("error_rate", 0.0):
             return CommandResult(
                 command=command,
                 stdout="",
                 stderr="Medium Error: Additional sense: Write error",
                 returncode=1,
                 duration=0.5,
                 timed_out=False,
                 error_type="unknown", # Or specific medium error if we mapped it
                 error_message="Simulated Medium Error"
             )
        
        return None

# Singleton global injector for API control
chaos_injector = ChaosInjector()

class TapeCommandRunner:
    def __init__(
        self,
        timeouts: Optional[Dict[str, int]] = None,
        history: Optional[CommandHistory] = None,
        log_callback: Optional[Callable[[Dict[str, object]], None]] = None,
    ):
        self.timeouts = default_timeouts()
        if timeouts:
            self.timeouts.update(timeouts)
        self.history = history or CommandHistory(limit=50)
        self.log_callback = log_callback
        self._backoff_state: Dict[str, Dict[str, float]] = {}
        self.retry_policy = {
            "mtx_status": 3,
            "mtx_inquiry": 3,
            "mtx_inventory": 3,
            "mtx_load": 2,
            "mtx_unload": 2,
            "mtx_transfer": 2,
            "mt_status": 3,
            "probe": 3,
            "sg_inq": 2,
        }

    @staticmethod
    def classify_error(stdout: str, stderr: str, returncode: int, timed_out: bool) -> Tuple[Optional[str], Optional[str]]:
        if timed_out:
            return "timeout", "Command timed out"
        if returncode == 0:
            return None, None
        combined = f"{stderr}\n{stdout}".lower()
        if "permission denied" in combined:
            return "permission_denied", "Permission denied"
        if "device busy" in combined or "resource busy" in combined:
            return "device_busy", "Device is busy"
        if "illegal request" in combined or "read element status" in combined:
            return "illegal_request", "Illegal request or wrong device"
        if "transport error" in combined or "connection timed out" in combined or "timed out" in combined:
            return "transport", "Transient transport error"
        if "not ready" in combined or "not present" in combined:
            return "not_ready", "Device not ready"
        return "unknown", (stderr or stdout or "").strip() or "Command failed"

    def _apply_backoff(self, key: str) -> None:
        state = self._backoff_state.get(key)
        if not state:
            return
        delay = state.get("delay", 0)
        last = state.get("last", 0)
        now = time.time()
        remaining = delay - (now - last)
        if remaining > 0:
            time.sleep(remaining)

    def _record_backoff(self, key: str, success: bool) -> None:
        if success:
            self._backoff_state.pop(key, None)
            return
        state = self._backoff_state.get(key, {"delay": 0, "last": 0})
        delay = state["delay"] or 1
        delay = min(delay * 2, 30)
        self._backoff_state[key] = {"delay": delay, "last": time.time()}

    def _emit_log(self, entry: Dict[str, object]) -> None:
        if not self.log_callback:
            return
        try:
            self.log_callback(entry)
        except Exception:
            return

    def run(
        self,
        command: List[str],
        timeout: Optional[int] = None,
        name: Optional[str] = None,
        allow_retry: bool = False,
        retryable_errors: Optional[List[str]] = None,
        lock: Optional[object] = None,
        input_data: Optional[str] = None,
    ) -> CommandResult:
        timeout_value = timeout
        if timeout_value is None and name:
            timeout_value = self.timeouts.get(name)

        key = name or "command"
        retries = 0
        if allow_retry:
            retries = self.retry_policy.get(key, 0)
        # BUGFIX: Removing 'device_busy' from retryable_errors. If the drive is busy,
        # we want to fail-fast so we do not block synchronous API workers indefinitely.
        retryable_errors = retryable_errors or ["timeout", "transport", "not_ready"]

        last_result: Optional[CommandResult] = None
        for attempt in range(retries + 1):
            self._apply_backoff(key)

            # CHAOS CHECK
            chaos_result = chaos_injector.maybe_fail(command)
            if chaos_result:
                # Log simulated failure
                error_type, error_message = chaos_result.error_type, chaos_result.error_message
                self._record_backoff(key, success=False)
                entry = {
                    "command": command,
                    "name": f"{key} [CHAOS]",
                    "start_time": datetime.utcnow().isoformat() + "Z",
                    "end_time": datetime.utcnow().isoformat() + "Z",
                    "timeout": timeout_value,
                    "duration": chaos_result.duration,
                    "returncode": chaos_result.returncode,
                    "timed_out": chaos_result.timed_out,
                    "stdout": chaos_result.stdout,
                    "stderr": chaos_result.stderr,
                    "error_type": error_type,
                    "attempt": attempt + 1,
                }
                self.history.add(entry)
                self._emit_log(entry)
                last_result = CommandResult(**entry)
                if not self._is_retryable(allow_retry, error_type, retryable_errors):
                    return last_result
                continue

            start = time.time()
            start_iso = datetime.fromtimestamp(start, timezone.utc).isoformat().replace("+00:00", "Z")
            stdout = ""
            stderr = ""
            returncode = 0
            timed_out = False

            if lock:
                thread_id = threading.get_ident()
                if hasattr(lock, 'acquire'):
                    # self._log(f"Thread {thread_id} acquiring lock for {name or command[0]}...")
                    lock.acquire()
                    # self._log(f"Thread {thread_id} acquired lock for {name or command[0]}")
                else:
                    self._log(f"Warning: lock object for {name or command[0]} does not have acquire() method", 'warning')

            try:
                result = subprocess.run(
                    command,
                    input=input_data,
                    capture_output=True,
                    text=True,
                    timeout=timeout_value,
                )
                stdout = result.stdout or ""
                stderr = result.stderr or ""
                returncode = result.returncode
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                stdout = (exc.stdout or "")
                stderr = (exc.stderr or "")
                returncode = 124
            finally:
                if lock:
                    lock.release()
            end = time.time()
            duration = end - start
            end_iso = datetime.fromtimestamp(end, timezone.utc).isoformat().replace("+00:00", "Z")
            error_type, error_message = self.classify_error(stdout, stderr, returncode, timed_out)

            success = returncode == 0 and not timed_out
            self._record_backoff(key, success=success)
            entry = {
                "command": command,
                "name": key,
                "start_time": start_iso,
                "end_time": end_iso,
                "timeout": timeout_value,
                "duration": duration,
                "returncode": returncode,
                "timed_out": timed_out,
                "stdout": stdout[-4000:],
                "stderr": stderr[-4000:],
                "error_type": error_type,
                "attempt": attempt + 1,
            }
            self.history.add(entry)
            self._emit_log(entry)

            last_result = CommandResult(
                command=command,
                stdout=stdout,
                stderr=stderr,
                returncode=returncode,
                duration=duration,
                timed_out=timed_out,
                error_type=error_type,
                error_message=error_message,
            )

            if success:
                return last_result
            if not allow_retry or error_type not in retryable_errors:
                return last_result

            backoff = min(2 ** attempt, 8)
            time.sleep(backoff)

        return last_result or CommandResult(
            command=command,
            stdout="",
            stderr="",
            returncode=1,
            duration=0.0,
            timed_out=False,
            error_type="unknown",
            error_message="Command did not execute",
        )

    def probe(self, command: List[str], name: str = "probe") -> CommandResult:
        return self.run(command, name=name, allow_retry=True)
