import grp
import os
import pwd
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence


class SMBFixtureError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        detail: Optional[str] = None,
        logs_tail: Optional[str] = None,
        diagnostics: Optional[Dict[str, object]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.detail = detail
        self.logs_tail = logs_tail
        self.diagnostics = diagnostics


@dataclass
class SMBFixture:
    temp_dir: tempfile.TemporaryDirectory
    share_path: str
    username: str
    password: str
    port: int
    expected_files: int
    expected_bytes: int
    warnings: List[str]
    process: subprocess.Popen
    log_file: Path
    diagnostics: Dict[str, object]

    def read_log(self) -> str:
        try:
            if self.log_file.exists():
                return self.log_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"Unable to read SMB log: {exc}"
        return ""


@dataclass(frozen=True)
class SMBFixturePaths:
    base_dir: Path
    share_dir: Path
    log_dir: Path
    lock_dir: Path
    state_dir: Path
    cache_dir: Path
    run_dir: Path
    private_dir: Path
    log_file: Path
    smb_conf: Path
    smb_passwd: Path


def build_fixture_paths(base_path: Path) -> SMBFixturePaths:
    return SMBFixturePaths(
        base_dir=base_path,
        share_dir=base_path / "share",
        log_dir=base_path / "logs",
        lock_dir=base_path / "lock",
        state_dir=base_path / "state",
        cache_dir=base_path / "cache",
        run_dir=base_path / "run",
        private_dir=base_path / "private",
        log_file=base_path / "logs" / "smbd.log",
        smb_conf=base_path / "smb.conf",
        smb_passwd=base_path / "smbpasswd",
    )


def build_smb_fixture_config(paths: SMBFixturePaths, port: int) -> str:
    return "\n".join([
        "[global]",
        "workgroup = WORKGROUP",
        "security = user",
        "server role = standalone server",
        "map to guest = Bad User",
        "guest account = nobody",
        "logging = file",
        "max log size = 0",
        "log level = 1",
        "disable spoolss = yes",
        "load printers = no",
        "printing = bsd",
        "printcap name = /dev/null",
        "server min protocol = SMB2",
        "client min protocol = SMB2",
        f"smb ports = {port}",
        "interfaces = 127.0.0.1/8",
        "bind interfaces only = yes",
        f"log file = {paths.log_file}",
        f"pid directory = {paths.run_dir}",
        f"lock directory = {paths.lock_dir}",
        f"state directory = {paths.state_dir}",
        f"cache directory = {paths.cache_dir}",
        f"private dir = {paths.private_dir}",
        "passdb backend = smbpasswd",
        f"smb passwd file = {paths.smb_passwd}",
        "panic action = /bin/true",
        "",
        "[fixture]",
        f"path = {paths.share_dir}",
        "browseable = yes",
        "read only = no",
        "guest ok = yes",
    ]) + "\n"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run_command(command: Sequence[str]) -> Dict[str, object]:
    try:
        proc = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "command": list(command),
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except FileNotFoundError as exc:
        return {
            "command": list(command),
            "returncode": None,
            "error": f"{exc}",
        }
    except Exception as exc:
        return {
            "command": list(command),
            "returncode": None,
            "error": f"{exc}",
        }


def _collect_prerequisites(service_user: str, smbd_path: Optional[str]) -> Dict[str, object]:
    prereqs = {
        "getent_passwd": _run_command(["getent", "passwd", service_user]),
        "id_u": _run_command(["id", "-u"]),
        "id_g": _run_command(["id", "-g"]),
    }
    if smbd_path:
        prereqs["ldd_smbd"] = _run_command(["ldd", smbd_path])
    return prereqs


def _wait_for_port(
    port: int,
    process: subprocess.Popen,
    timeout_s: float = 8.0,
    poll_interval_s: float = 0.2,
) -> float:
    start = time.monotonic()
    deadline = start + timeout_s
    last_error: Optional[Exception] = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            waited = time.monotonic() - start
            detail = f"SMBD exited early with code {process.returncode} after {waited:.1f}s."
            raise SMBFixtureError("smbd_start_failed", "SMB fixture failed to start", detail)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return time.monotonic() - start
        except OSError as exc:
            last_error = exc
            time.sleep(poll_interval_s)
    waited = time.monotonic() - start
    detail = f"Timed out after {waited:.1f}s waiting for SMB fixture on 127.0.0.1:{port}."
    if last_error:
        detail = f"{detail} Last error: {last_error}"
    raise SMBFixtureError("smbd_unreachable", "SMB fixture did not start", detail)

def _select_temp_root() -> str:
    preferred_root = "/var/lib/fossilsafe/tmp"
    if os.path.isdir(preferred_root) and os.access(preferred_root, os.W_OK):
        return preferred_root
    return tempfile.gettempdir()

def _resolve_service_user() -> Optional[Dict[str, object]]:
    service_user = os.environ.get("FOSSILSAFE_SERVICE_USER", "fossilsafe")
    try:
        entry = pwd.getpwnam(service_user)
        group = grp.getgrnam(service_user)
        return {
            "name": service_user,
            "uid": entry.pw_uid,
            "gid": group.gr_gid,
        }
    except (KeyError, OSError):
        return {
            "name": service_user,
            "uid": None,
            "gid": None,
        }


def _ensure_directory(path: Path, mode: int, ownership: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    path.mkdir(parents=True, exist_ok=True)
    details: Dict[str, object] = {
        "path": str(path),
        "mode": oct(mode),
        "chmod_ok": True,
        "chown_ok": True,
        "writable": os.access(path, os.W_OK),
    }
    try:
        os.chmod(path, mode)
    except OSError as exc:
        details["chmod_ok"] = False
        details["chmod_error"] = str(exc)
    if ownership and os.geteuid() == 0 and ownership.get("uid") is not None and ownership.get("gid") is not None:
        try:
            os.chown(path, int(ownership["uid"]), int(ownership["gid"]))
        except OSError as exc:
            details["chown_ok"] = False
            details["chown_error"] = str(exc)
    details["writable"] = os.access(path, os.W_OK)
    return details


def _format_log_detail(detail: Optional[str], log_text: str) -> Optional[str]:
    parts = [detail] if detail else []
    if log_text:
        parts.append("SMBD log:")
        parts.append(log_text.strip())
    return "\n".join(parts) if parts else None


def _tail_text(text: str, max_lines: int = 80) -> str:
    if not text:
        return ""
    lines = text.strip().splitlines()
    tail = lines[-max_lines:] if len(lines) > max_lines else lines
    return "\n".join(tail)


def get_smb_fixture_diagnostics() -> Dict[str, object]:
    smbd_path = shutil.which("smbd")
    smbpasswd_path = shutil.which("smbpasswd")
    testparm_path = shutil.which("testparm")
    temp_root = _select_temp_root()
    ownership = _resolve_service_user()
    prerequisites = _collect_prerequisites(ownership.get("name") or "fossilsafe", smbd_path)
    return {
        "strategy": "smbd",
        "smbd_path": smbd_path,
        "smbpasswd_path": smbpasswd_path,
        "testparm_path": testparm_path,
        "smbd_available": bool(smbd_path),
        "smbpasswd_available": bool(smbpasswd_path),
        "testparm_available": bool(testparm_path),
        "temp_root": temp_root,
        "prerequisites": prerequisites,
        "sandbox": {
            "uid": os.geteuid(),
            "gid": os.getegid(),
            "service_user": ownership.get("name") if ownership else None,
            "service_uid": ownership.get("uid") if ownership else None,
            "service_gid": ownership.get("gid") if ownership else None,
            "tmp_writable": os.access("/tmp", os.W_OK),
            "temp_root_writable": os.access(temp_root, os.W_OK),
            "smbd_executable": bool(smbd_path and os.access(smbd_path, os.X_OK)),
        },
    }


def build_fixture_diagnostics(
    paths: SMBFixturePaths,
    port: int,
    smbd_path: Optional[str],
    smbpasswd_path: Optional[str],
    command: List[str],
    testparm_path: Optional[str] = None,
    sandbox: Optional[Dict[str, object]] = None,
    prerequisites: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    return {
        "strategy": "smbd",
        "smbd_path": smbd_path,
        "smbpasswd_path": smbpasswd_path,
        "testparm_path": testparm_path,
        "smbd_available": bool(smbd_path),
        "smbpasswd_available": bool(smbpasswd_path),
        "testparm_available": bool(testparm_path),
        "port": port,
        "command": command,
        "sandbox": sandbox or {},
        "prerequisites": prerequisites or {},
        "paths": {
            "base_dir": str(paths.base_dir),
            "share_dir": str(paths.share_dir),
            "log_dir": str(paths.log_dir),
            "lock_dir": str(paths.lock_dir),
            "state_dir": str(paths.state_dir),
            "cache_dir": str(paths.cache_dir),
            "run_dir": str(paths.run_dir),
            "private_dir": str(paths.private_dir),
            "log_file": str(paths.log_file),
            "smb_conf": str(paths.smb_conf),
            "smb_passwd": str(paths.smb_passwd),
        },
        "start_stdout": None,
        "start_stderr": None,
        "returncode": None,
        "log_tail": None,
        "listen_wait_ms": None,
        "strategy_attempts": [],
    }


@contextmanager
def start_smb_fixture() -> Iterator[SMBFixture]:
    smbd_path = shutil.which("smbd")
    if not smbd_path:
        raise SMBFixtureError(
            "smb_tool_missing",
            "SMB self-test unavailable: Samba not installed",
            "smbd command not found",
        )

    try:
        temp_dir = tempfile.TemporaryDirectory(prefix="smb_fixture_", dir=_select_temp_root())
    except OSError as exc:
        raise SMBFixtureError("smb_fixture_unwritable", "SMB self-test storage unavailable", str(exc))
    warnings: List[str] = []
    try:
        base_path = Path(temp_dir.name)
        ownership = _resolve_service_user()
        if not os.access(base_path, os.W_OK):
            raise SMBFixtureError(
                "smb_fixture_unwritable",
                "SMB self-test storage unavailable",
                f"Fixture base path not writable: {base_path}",
            )
        paths = build_fixture_paths(base_path)
        dir_checks: Dict[str, object] = {}
        dir_checks["base_dir"] = _ensure_directory(paths.base_dir, 0o755, ownership)
        dir_checks["share_dir"] = _ensure_directory(paths.share_dir, 0o755, ownership)
        dir_checks["log_dir"] = _ensure_directory(paths.log_dir, 0o755, ownership)
        dir_checks["lock_dir"] = _ensure_directory(paths.lock_dir, 0o755, ownership)
        dir_checks["state_dir"] = _ensure_directory(paths.state_dir, 0o755, ownership)
        dir_checks["cache_dir"] = _ensure_directory(paths.cache_dir, 0o755, ownership)
        dir_checks["run_dir"] = _ensure_directory(paths.run_dir, 0o700, ownership)
        dir_checks["private_dir"] = _ensure_directory(paths.private_dir, 0o700, ownership)
        if not os.access(paths.log_dir, os.W_OK):
            raise SMBFixtureError(
                "smb_fixture_unwritable",
                "SMB self-test storage unavailable",
                f"Fixture log path not writable: {paths.log_dir}",
            )
        if not os.access(paths.share_dir, os.W_OK):
            raise SMBFixtureError(
                "smb_fixture_unwritable",
                "SMB self-test storage unavailable",
                f"Fixture share path not writable: {paths.share_dir}",
            )
        nested_dir = paths.share_dir / "nested"
        nested_dir.mkdir(parents=True, exist_ok=True)
        file1 = paths.share_dir / "fixture.txt"
        file1.write_bytes(b"fixture")
        file2 = nested_dir / "nested.bin"
        file2.write_bytes(b"1234567890")
        expected_files = 2
        expected_bytes = len(b"fixture") + len(b"1234567890")

        username = f"fixture_{uuid.uuid4().hex[:8]}"
        password = uuid.uuid4().hex[:12]

        testparm_path = shutil.which("testparm")
        smbpasswd_path = shutil.which("smbpasswd")
        last_error: Optional[SMBFixtureError] = None
        process = None
        diagnostics: Optional[Dict[str, object]] = None
        prereqs = _collect_prerequisites(ownership.get("name") or "fossilsafe", smbd_path)
        command_variants = [
            [smbd_path, "-F", "-s", str(paths.smb_conf), "-l", str(paths.log_dir), "-d", "1"],
            [smbd_path, "--foreground", "--no-process-group", "-s", str(paths.smb_conf), "-l", str(paths.log_dir), "-d", "1"],
            [smbd_path, "-i", "-s", str(paths.smb_conf), "-l", str(paths.log_dir), "-d", "1"],
        ]
        strategy_attempts: List[Dict[str, object]] = []
        for attempt_index, command in enumerate(command_variants):
            port = _free_port()
            paths.smb_conf.write_text(build_smb_fixture_config(paths, port), encoding="utf-8")
            sandbox = {
                "uid": os.geteuid(),
                "gid": os.getegid(),
                "service_user": ownership.get("name") if ownership else None,
                "service_uid": ownership.get("uid") if ownership else None,
                "service_gid": ownership.get("gid") if ownership else None,
                "tmp_writable": os.access("/tmp", os.W_OK),
                "temp_root_writable": os.access(str(paths.base_dir), os.W_OK),
                "smbd_executable": bool(smbd_path and os.access(smbd_path, os.X_OK)),
                "dir_checks": dir_checks,
            }
            diagnostics = build_fixture_diagnostics(
                paths,
                port,
                smbd_path,
                smbpasswd_path,
                command,
                testparm_path,
                sandbox,
                prerequisites=prereqs,
            )
            diagnostics["strategy_attempts"] = strategy_attempts

            if smbpasswd_path:
                proc = subprocess.run(
                    [smbpasswd_path, "-a", "-s", "-c", str(paths.smb_conf), username],
                    input=f"{password}\n{password}\n",
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if proc.returncode != 0 and "SMB fixture user creation failed; guest access enabled." not in warnings:
                    warnings.append("SMB fixture user creation failed; guest access enabled.")
            elif "smbpasswd not available; guest access enabled." not in warnings:
                warnings.append("smbpasswd not available; guest access enabled.")

            if testparm_path:
                testparm_proc = subprocess.run(
                    [testparm_path, "-s", str(paths.smb_conf)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                diagnostics["testparm"] = {
                    "returncode": testparm_proc.returncode,
                    "stdout": testparm_proc.stdout,
                    "stderr": testparm_proc.stderr,
                }
                if testparm_proc.returncode != 0:
                    detail = "\n".join(filter(None, [testparm_proc.stdout, testparm_proc.stderr]))
                    raise SMBFixtureError(
                        "smbd_config_invalid",
                        "SMB fixture config failed validation",
                        detail,
                        _tail_text(detail),
                        diagnostics,
                    )

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            attempt_detail: Dict[str, object] = {
                "command": command,
                "result": "pending",
                "returncode": None,
                "listen_wait_ms": None,
                "start_stdout": "",
                "start_stderr": "",
                "log_tail": "",
            }
            try:
                wait_start = time.monotonic()
                listen_wait = _wait_for_port(port, process)
                diagnostics["listen_wait_ms"] = int(listen_wait * 1000)
                diagnostics["start_stdout"] = ""
                diagnostics["start_stderr"] = ""
                diagnostics["returncode"] = process.poll()
                log_text = ""
                try:
                    log_text = paths.log_file.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    log_text = ""
                diagnostics["log_tail"] = _tail_text(log_text)
                attempt_detail.update({
                    "result": "started",
                    "returncode": diagnostics["returncode"],
                    "listen_wait_ms": diagnostics["listen_wait_ms"],
                    "log_tail": diagnostics["log_tail"],
                })
                strategy_attempts.append(attempt_detail)
                break
            except SMBFixtureError as exc:
                diagnostics["listen_wait_ms"] = int((time.monotonic() - wait_start) * 1000)
                try:
                    stdout, stderr = process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    stdout, stderr = "", ""
                log_text = ""
                try:
                    log_text = paths.log_file.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    log_text = ""
                diagnostics["start_stdout"] = stdout
                diagnostics["start_stderr"] = stderr
                diagnostics["returncode"] = process.poll()
                diagnostics["listen_wait_ms"] = diagnostics.get("listen_wait_ms") or None
                detail = "\n".join(filter(None, [stdout, stderr]))
                detail = _format_log_detail(detail, log_text)
                logs_tail = "\n".join(filter(None, [
                    _tail_text(stderr or ""),
                    _tail_text(log_text),
                ])).strip() or None
                diagnostics["log_tail"] = _tail_text(log_text)
                attempt_detail.update({
                    "result": "failed",
                    "returncode": diagnostics["returncode"],
                    "listen_wait_ms": diagnostics["listen_wait_ms"],
                    "start_stdout": stdout,
                    "start_stderr": stderr,
                    "log_tail": diagnostics["log_tail"],
                    "error": exc.detail or exc.code,
                })
                strategy_attempts.append(attempt_detail)
                last_error = SMBFixtureError(
                    "smbd_start_failed",
                    "SMB fixture failed to start",
                    detail or exc.detail,
                    logs_tail,
                    diagnostics,
                )
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                if attempt_index >= len(command_variants) - 1:
                    raise last_error
        if not process or not diagnostics:
            raise last_error or SMBFixtureError("smbd_start_failed", "SMB fixture failed to start")

        fixture = SMBFixture(
            temp_dir=temp_dir,
            share_path="//127.0.0.1/fixture",
            username=username,
            password=password,
            port=diagnostics["port"],
            expected_files=expected_files,
            expected_bytes=expected_bytes,
            warnings=warnings,
            process=process,
            log_file=paths.log_file,
            diagnostics=diagnostics,
        )
        yield fixture
    finally:
        try:
            if "process" in locals():
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
        finally:
            temp_dir.cleanup()
