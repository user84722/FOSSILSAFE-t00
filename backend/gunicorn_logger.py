import threading
import time

from gunicorn.glogging import Logger

POLLING_ENDPOINTS = {
    "/api/banner",
    "/api/jobs",
    "/api/logs",
    "/api/restore/jobs",
    "/api/status",
    "/api/status/banner",
}
POLLING_SUPPRESS_SECONDS = 60

_auth_suppress_lock = threading.Lock()
_auth_suppress_cache = {}


def _normalize_path(path: str) -> str:
    if not path:
        return ""
    if path != "/":
        return path.rstrip("/")
    return path


def _is_polling_path(path: str) -> bool:
    return _normalize_path(path) in POLLING_ENDPOINTS


def _should_suppress(status_code: int, path: str) -> bool:
    return status_code in (401, 403) and _is_polling_path(path)


def _should_log_once(client_ip: str, path: str, status_code: int) -> bool:
    now = time.monotonic()
    key = (client_ip, path, status_code)
    with _auth_suppress_lock:
        last = _auth_suppress_cache.get(key)
        if last is not None and now - last < POLLING_SUPPRESS_SECONDS:
            return False
        _auth_suppress_cache[key] = now
        if len(_auth_suppress_cache) > 500:
            cutoff = now - POLLING_SUPPRESS_SECONDS
            stale_keys = [k for k, ts in _auth_suppress_cache.items() if ts < cutoff]
            for stale in stale_keys:
                _auth_suppress_cache.pop(stale, None)
        return True


class FossilSafeLogger(Logger):
    """Suppress noisy polling 401/403 access logs while keeping 5xx visible."""

    def access(self, resp, req, environ, request_time):
        status_code = None
        if resp is not None and getattr(resp, "status", None):
            try:
                status_code = int(str(resp.status).split()[0])
            except (TypeError, ValueError, IndexError):
                status_code = None
        if status_code in (401, 403):
            path = getattr(req, "path", "") or environ.get("PATH_INFO", "")
            path = _normalize_path(path)
            if _should_suppress(status_code, path):
                client_ip = environ.get("REMOTE_ADDR", "unknown")
                if _should_log_once(client_ip, path, status_code):
                    self.error(
                        "Unauthorized polling: %s from %s (suppressed for %ss)",
                        path,
                        client_ip,
                        POLLING_SUPPRESS_SECONDS,
                    )
                return
        super().access(resp, req, environ, request_time)
