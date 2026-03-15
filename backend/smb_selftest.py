import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request, g

from backend.smb_client import SMBClient, SMBScanError
from backend.smb_fixture import SMBFixtureError, get_smb_fixture_diagnostics, start_smb_fixture


@dataclass(frozen=True)
class SmbSelfTestDependencies:
    get_smb_client: Callable[[], Optional[SMBClient]]
    initialize_smb_client: Callable[[], Dict[str, Optional[str]]]
    get_smb_unavailable_reason: Callable[[], Optional[str]]
    validate_smb_path: Callable[[str], Tuple[bool, Optional[str]]]
    log_info: Callable[[str, str, Optional[str]], None]
    log_error: Callable[[str, str, Optional[str]], None]
    log_request_event: Callable[[str, str, Optional[Dict[str, object]]], None]


def _make_smb_selftest_error(
    code: str,
    message: str,
    status_code: int,
    detail: Optional[str] = None,
    logs_tail: Optional[str] = None,
    diagnostics: Optional[Dict[str, object]] = None,
):
    payload = {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "detail": detail,
            "logs_tail": logs_tail,
        },
        "request_id": getattr(g, "request_id", None),
    }
    if diagnostics is not None:
        payload["diagnostics"] = diagnostics
    return jsonify(payload), status_code


def _run_smb_client_only_test(smb_client: SMBClient, smb_path: str, username: str, password: str) -> Dict[str, object]:
    result = {
        "test": "SMB Connectivity",
        "path": smb_path,
        "checks": [],
    }

    try:
        connected = smb_client.test_connection(smb_path, username, password)
        result["checks"].append({
            "name": "Connection",
            "status": "pass" if connected else "fail",
            "message": "Connected successfully" if connected else "Connection failed",
        })
    except Exception as exc:
        result["checks"].append({
            "name": "Connection",
            "status": "fail",
            "message": str(exc),
        })

    try:
        if smb_client.can_read(smb_path, username, password):
            result["checks"].append({
                "name": "Read Access",
                "status": "pass",
                "message": "Read access confirmed",
            })
        else:
            result["checks"].append({
                "name": "Read Access",
                "status": "fail",
                "message": "Cannot read from path",
            })
    except Exception as exc:
        result["checks"].append({
            "name": "Read Access",
            "status": "warning",
            "message": f"Could not verify: {exc}",
        })

    try:
        files = smb_client.list_files(smb_path, username, password, limit=10)
        result["checks"].append({
            "name": "Directory Listing",
            "status": "pass",
            "message": f"Found {len(files)} items",
        })
        result["sample_files"] = files[:5]
    except Exception as exc:
        result["checks"].append({
            "name": "Directory Listing",
            "status": "fail",
            "message": str(exc),
        })

    statuses = [check["status"] for check in result["checks"]]
    result["overall"] = "fail" if "fail" in statuses else ("warning" if "warning" in statuses else "pass")
    return result


def _write_smb_credentials_file(username: str, password: str, domain: str = "") -> str:
    handle = tempfile.NamedTemporaryFile(mode="w", delete=False, prefix="smb_creds_")
    handle.write(f"username={username}\n")
    handle.write(f"password={password}\n")
    if domain:
        handle.write(f"domain={domain}\n")
    handle.flush()
    handle.close()
    os.chmod(handle.name, 0o600)
    return handle.name


def _cleanup_smb_credentials_file(path: str) -> None:
    try:
        os.remove(path)
    except Exception:
        pass


def _run_smbclient_version_check() -> Dict[str, object]:
    cmd = ["smbclient", "-V"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = "\n".join(filter(None, [result.stdout, result.stderr])).strip()
        if result.returncode == 0:
            return {
                "success": True,
                "command": cmd,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "message": output or "smbclient available",
            }
        return {
            "success": False,
            "command": cmd,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error_code": "smbclient_failed",
            "message": "smbclient exited with errors",
            "detail": output or f"smbclient returned {result.returncode}",
        }
    except FileNotFoundError:
        return {
            "success": False,
            "command": cmd,
            "returncode": None,
            "error_code": "smb_tool_missing",
            "message": "SMB client not available",
            "detail": "smbclient command not found",
        }
    except Exception as exc:
        return {
            "success": False,
            "command": cmd,
            "returncode": None,
            "error_code": "smbclient_failed",
            "message": "smbclient check failed",
            "detail": str(exc),
        }


def _parse_smbclient_failure(smb_client: SMBClient, output: str) -> Tuple[str, str]:
    if smb_client._is_auth_failure(output):
        return "auth_failed", "SMB authentication failed"
    if smb_client._is_share_not_found(output):
        return "share_not_found", "SMB share not found"
    if smb_client._is_host_unreachable(output):
        return "host_unreachable", "SMB host unreachable"
    return "connection_failed", "SMB connection failed"


def _run_smbclient_remote_check(
    smb_client: SMBClient,
    smb_path: str,
    username: str,
    password: str,
    domain: str = "",
) -> Dict[str, object]:
    cmd = ["smbclient", smb_path]
    creds_file = None
    try:
        creds_file = _write_smb_credentials_file(username, password, domain)
        cmd.extend(["-A", creds_file, "-c", "ls"])
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        output = "\n".join(filter(None, [stdout, stderr])).strip()
        if result.returncode != 0:
            error_code, message = _parse_smbclient_failure(smb_client, output)
            return {
                "success": False,
                "command": cmd,
                "returncode": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "error_code": error_code,
                "message": message,
                "detail": output or f"smbclient returned {result.returncode}",
            }
        file_count = 0
        total_bytes = 0
        sample_files: List[str] = []
        for line in stdout.splitlines():
            cleaned = line.strip()
            if not cleaned or cleaned.startswith(".") or "blocks of size" in cleaned:
                continue
            parts = cleaned.split()
            if not parts:
                continue
            name = parts[0]
            if name in {".", ".."}:
                continue
            attributes = parts[1] if len(parts) > 1 else ""
            if "D" in attributes:
                continue
            size = 0
            if len(parts) > 2 and parts[2].isdigit():
                size = int(parts[2])
            elif len(parts) > 1 and parts[1].isdigit():
                size = int(parts[1])
            file_count += 1
            total_bytes += size
            if len(sample_files) < 5:
                sample_files.append(name)
        return {
            "success": True,
            "command": cmd,
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "file_count": file_count,
            "total_bytes": total_bytes,
            "sample_files": sample_files,
            "message": "SMB client listing completed",
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "command": cmd,
            "returncode": None,
            "error_code": "timeout",
            "message": "SMB client listing timed out",
            "detail": "smbclient timed out",
        }
    except FileNotFoundError:
        return {
            "success": False,
            "command": cmd,
            "returncode": None,
            "error_code": "smb_tool_missing",
            "message": "SMB client not available",
            "detail": "smbclient command not found",
        }
    except Exception as exc:
        return {
            "success": False,
            "command": cmd,
            "returncode": None,
            "error_code": "connection_failed",
            "message": "SMB client listing failed",
            "detail": str(exc),
        }
    finally:
        if creds_file:
            _cleanup_smb_credentials_file(creds_file)


def create_smb_selftest_blueprint(deps: SmbSelfTestDependencies) -> Blueprint:
    blueprint = Blueprint("smb_selftest", __name__)

    @blueprint.route("/api/selftest/smb", methods=["POST"])
    def selftest_smb():
        """Test SMB connectivity to a given path."""
        try:
            data = request.json
            smb_path = data.get("smb_path", "")
            username = data.get("username", "")
            password = data.get("password", "")

            deps.log_request_event(
                "SMB self-test requested",
                "selftest",
                {"smb_path": smb_path},
            )

            if not smb_path:
                return jsonify({"success": False, "error": "SMB path required"}), 400

            valid, error = deps.validate_smb_path(smb_path)
            if not valid:
                return jsonify({"success": False, "error": error}), 400

            deps.log_info(f"Running SMB connectivity test to {smb_path}", "selftest")
            smb_client = deps.get_smb_client()
            if smb_client is None:
                raise RuntimeError("SMB client not initialized")
            result = _run_smb_client_only_test(smb_client, smb_path, username, password)
            deps.log_info(f"SMB test completed: {result['overall']}", "selftest")
            return jsonify({"success": True, "result": result, "results": result})
        except Exception as exc:
            deps.log_error(f"SMB self-test failed: {exc}", "selftest")
            return jsonify({"success": False, "error": str(exc)}), 500

    @blueprint.route("/api/diagnostics/smb_selftest", methods=["POST"])
    def diagnostics_smb_selftest():
        """Run a local SMB stack self-test without external dependencies."""
        start_time = time.monotonic()
        deps.log_request_event("SMB local self-test requested", "selftest")
        diagnostics = get_smb_fixture_diagnostics()
        fixture_detail = "Local SMB fixture unavailable."
        data = request.get_json(silent=True) or {}
        fallback_path = str(data.get("smb_path") or "").strip()
        fallback_username = str(data.get("username") or "")
        fallback_password = str(data.get("password") or "")
        smb_client = deps.get_smb_client()
        if smb_client is None:
            deps.initialize_smb_client()
            smb_client = deps.get_smb_client()
        if smb_client is None:
            return _make_smb_selftest_error(
                "smb_unavailable",
                "SMB client not initialized",
                503,
                deps.get_smb_unavailable_reason() or "SMB tooling not available",
                diagnostics=diagnostics,
            )
        try:
            with start_smb_fixture() as fixture:
                def append_log(detail: Optional[str]) -> Optional[str]:
                    log_text = fixture.read_log()
                    if not log_text:
                        return detail
                    parts = [detail] if detail else []
                    parts.append("SMBD log:")
                    parts.append(log_text.strip())
                    return "\n".join(parts)

                try:
                    scan_result = smb_client.scan_directory(
                        fixture.share_path,
                        fixture.username,
                        fixture.password,
                        scan_mode="full",
                        port=fixture.port,
                    )
                except SMBScanError as exc:
                    detail = append_log(exc.detail)
                    deps.log_error(f"SMB self-test scan failed: {exc.message}", "selftest", detail)
                    return _make_smb_selftest_error(
                        exc.code or "smb_selftest_failed",
                        exc.message or "SMB self-test failed",
                        502,
                        detail,
                        diagnostics=fixture.diagnostics,
                    )
                duration_ms = int((time.monotonic() - start_time) * 1000)
                warnings = list(fixture.warnings)
                warnings.extend(scan_result.get("warnings") or [])
                result = {
                    "success": True,
                    "message": "SMB self-test completed",
                    "file_count": scan_result.get("file_count", 0),
                    "total_bytes": scan_result.get("total_size", 0),
                    "duration_ms": duration_ms,
                    "detail": {
                        "share_path": fixture.share_path,
                        "port": fixture.port,
                        "expected_files": fixture.expected_files,
                        "expected_bytes": fixture.expected_bytes,
                        "sample_paths": scan_result.get("sample_paths", []),
                        "warnings": warnings,
                        "method": scan_result.get("method"),
                    },
                }
                if result["file_count"] != fixture.expected_files or result["total_bytes"] != fixture.expected_bytes:
                    detail = (
                        f"Expected {fixture.expected_files} files / {fixture.expected_bytes} bytes, "
                        f"got {result['file_count']} files / {result['total_bytes']} bytes."
                    )
                    detail = append_log(detail)
                    return _make_smb_selftest_error(
                        "smb_selftest_failed",
                        "SMB self-test did not enumerate expected files",
                        502,
                        detail,
                        diagnostics=fixture.diagnostics,
                    )
                deps.log_info("SMB local self-test succeeded", "selftest")
                return jsonify({
                    "success": True,
                    "strategy_used": "smbd",
                    "message": "SMB self-test completed using local fixture",
                    "result": result,
                    "results": result,
                    "diagnostics": fixture.diagnostics,
                })
        except SMBFixtureError as exc:
            fixture_detail = exc.detail or str(exc)
            deps.log_error(f"SMB self-test fixture failed: {exc}", "selftest", fixture_detail)
            diagnostics = exc.diagnostics or diagnostics
            diagnostics["fixture_error"] = {
                "code": exc.code,
                "message": str(exc),
                "detail": exc.detail,
                "logs_tail": exc.logs_tail,
            }
        except Exception as exc:
            deps.log_error(f"SMB self-test failed: {exc}", "selftest")
            return _make_smb_selftest_error(
                "smb_selftest_failed",
                "SMB self-test failed",
                500,
                str(exc),
                diagnostics=diagnostics,
            )

        smbclient_check = _run_smbclient_version_check()
        diagnostics["smbclient_check"] = smbclient_check
        if not smbclient_check.get("success"):
            return _make_smb_selftest_error(
                str(smbclient_check.get("error_code") or "smb_tool_missing"),
                str(smbclient_check.get("message") or "SMB client not available"),
                503,
                str(smbclient_check.get("detail") or ""),
                diagnostics=diagnostics,
            )

        fixture_notice = "Local SMB fixture unavailable; using client-only diagnostics."
        if fallback_path:
            valid, error = deps.validate_smb_path(fallback_path)
            if not valid:
                result = {
                    "success": False,
                    "status": "warning",
                    "strategy": "smbclient",
                    "message": f"{fixture_notice} Remote SMB check skipped: {error}.",
                    "code": "smb_fixture_unavailable",
                    "detail": fixture_detail,
                    "logs_tail": (diagnostics.get("fixture_error") or {}).get("logs_tail"),
                }
                return jsonify({
                    "success": True,
                    "strategy_used": "smbclient",
                    "message": result["message"],
                    "result": result,
                    "results": result,
                    "diagnostics": diagnostics,
                })
            remote_check = _run_smbclient_remote_check(smb_client, fallback_path, fallback_username, fallback_password)
            remote_diagnostics = {
                "command": remote_check.get("command"),
                "returncode": remote_check.get("returncode"),
                "stdout": remote_check.get("stdout"),
                "stderr": remote_check.get("stderr"),
            }
            mount_result = None
            mount_error = None
            try:
                mount_result = smb_client.scan_directory(
                    fallback_path,
                    fallback_username,
                    fallback_password,
                    scan_mode="quick",
                )
            except SMBScanError as exc:
                mount_error = {
                    "code": exc.code,
                    "message": exc.message,
                    "detail": exc.detail,
                }
            duration_ms = int((time.monotonic() - start_time) * 1000)
            if mount_result:
                result = {
                    "success": True,
                    "status": "pass",
                    "strategy": mount_result.get("method") or "mount",
                    "message": "Remote SMB scan completed.",
                    "file_count": mount_result.get("file_count", 0),
                    "total_bytes": mount_result.get("total_size", 0),
                    "duration_ms": duration_ms,
                    "detail": {
                        "path": fallback_path,
                        "sample_paths": mount_result.get("sample_paths", []),
                        "warnings": mount_result.get("warnings") or [],
                        "fixture_notice": fixture_notice,
                        "remote_listing": {
                            "file_count": remote_check.get("file_count", 0),
                            "total_bytes": remote_check.get("total_bytes", 0),
                            "sample_files": remote_check.get("sample_files", []),
                        },
                    },
                }
                return jsonify({
                    "success": True,
                    "strategy_used": result["strategy"],
                    "message": result["message"],
                    "result": result,
                    "results": result,
                    "diagnostics": diagnostics,
                })
            status = "warning" if remote_check.get("success") else "fail"
            result = {
                "success": False,
                "status": status,
                "strategy": "smbclient",
                "message": mount_error.get("message") if mount_error else (remote_check.get("message") or "Remote SMB check failed"),
                "code": mount_error.get("code") if mount_error else remote_check.get("error_code"),
                "detail": mount_error.get("detail") if mount_error else remote_check.get("detail"),
                "logs_tail": (diagnostics.get("fixture_error") or {}).get("logs_tail"),
                "diagnostics": remote_diagnostics,
            }
            return jsonify({
                "success": True,
                "strategy_used": "smbclient",
                "message": result["message"],
                "result": result,
                "results": result,
                "diagnostics": diagnostics,
            })

        result = {
            "success": True,
            "status": "warning",
            "strategy": "smbclient_check",
            "message": f"{fixture_notice} Provide an SMB path to run a remote share check.",
            "code": "smb_fixture_unavailable",
            "detail": fixture_detail,
            "logs_tail": (diagnostics.get("fixture_error") or {}).get("logs_tail"),
        }
        return jsonify({
            "success": True,
            "strategy_used": "smbclient_check",
            "message": result["message"],
            "result": result,
            "results": result,
            "diagnostics": diagnostics,
        })

    return blueprint
