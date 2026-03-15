import argparse
import grp
import json
import os
import pwd
import stat
import subprocess
import sys
import time
from typing import Dict, List, Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich.theme import Theme

# Add standard search paths for LTO tools if they are not in PATH
LTO_TOOL_PATHS = ["/usr/sbin", "/sbin", "/usr/local/sbin", "/usr/bin", "/bin"]
for p in LTO_TOOL_PATHS:
    if p not in os.environ.get("PATH", "").split(":"):
        os.environ["PATH"] = f"{os.environ.get('PATH', '')}:{p}"

from backend.config_store import (
    get_config_path,
    get_credential_key_path,
    get_data_dir,
    get_state_path,
    load_config,
    load_state,
)
from backend.tape.devices import get_devices
from backend.tape.runner import TapeCommandRunner

# Custom theme for FossilSafe
custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "pass": "bold green",
    "header": "bold magenta",
})

console = Console(theme=custom_theme)

def _format_command_result(result) -> Dict[str, object]:
    return {
        "command": result.command,
        "returncode": result.returncode,
        "duration": result.duration,
        "timed_out": result.timed_out,
        "error_type": result.error_type,
        "error_message": result.error_message,
        "stdout": (result.stdout or "")[-4000:],
        "stderr": (result.stderr or "")[-4000:],
    }

def _describe_mode(mode: int) -> str:
    return stat.filemode(mode)


def _owner_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _group_name(gid: int) -> str:
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def _permission_snapshot(path: str, needs_write: bool = False, needs_exec: bool = False) -> Dict[str, object]:
    entry: Dict[str, object] = {
        "path": path,
        "exists": os.path.exists(path),
    }
    if not entry["exists"]:
        return entry
    try:
        stat_result = os.stat(path)
        mode = stat_result.st_mode
    except OSError as exc:
        entry["error"] = str(exc)
        return entry
    entry.update({
        "mode": _describe_mode(mode),
        "owner": _owner_name(stat_result.st_uid),
        "group": _group_name(stat_result.st_gid),
        "readable": os.access(path, os.R_OK),
        "writable": os.access(path, os.W_OK),
        "executable": os.access(path, os.X_OK),
        "is_char_device": stat.S_ISCHR(mode),
    })
    if needs_write:
        entry["needs_write"] = True
        entry["write_ok"] = bool(entry.get("writable"))
    if needs_exec:
        entry["needs_exec"] = True
        entry["exec_ok"] = bool(entry.get("executable"))
    return entry


def get_permission_snapshot() -> Dict[str, object]:
    config_path = get_config_path()
    data_dir = get_data_dir()
    state_path = get_state_path()
    credential_path = get_credential_key_path()
    device_paths = [
        "/dev/fossilsafe-changer",
        "/dev/fossilsafe-drive-sg",
        "/dev/fossilsafe-drive-nst",
    ]

    checks = [
        _permission_snapshot(config_path),
        _permission_snapshot(data_dir, needs_write=True, needs_exec=True),
        _permission_snapshot(state_path, needs_write=True),
        _permission_snapshot(credential_path, needs_write=True),
    ]
    for path in device_paths:
        checks.append(_permission_snapshot(path, needs_write=True))

    errors = []
    for entry in checks:
        if not entry.get("exists"):
            continue
        if entry.get("needs_write") and not entry.get("write_ok"):
            errors.append(f"{entry['path']} is not writable")
        if entry.get("needs_exec") and not entry.get("exec_ok"):
            errors.append(f"{entry['path']} is not executable")
    return {"checks": checks, "errors": errors}


def _find_util(names: List[str]) -> Optional[str]:
    """Find the first available utility from a list of names."""
    for name in names:
        # Check standard paths first
        for path in LTO_TOOL_PATHS:
            full_path = os.path.join(path, name)
            if os.path.isfile(full_path) and os.access(full_path, os.X_OK):
                return full_path
        # Fallback to which
        try:
            result = subprocess.run(["which", name], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
        except:
            pass
    return None

def run_diagnostics() -> Dict[str, object]:
    config = load_config()
    state = load_state()
    devices, health = get_devices(config, state)

    runner = TapeCommandRunner(timeouts=(config.get("tape", {}) or {}).get("timeouts"))
    changer = devices.get("changer_sg")
    drive = devices.get("drive_nst")
    permissions = get_permission_snapshot()

    checks: List[Dict[str, object]] = []
    commands: List[Dict[str, object]] = []

    def add_check(name: str, status: str, message: str, detail: Optional[Dict[str, object]] = None):
        entry = {"name": name, "status": status, "message": message}
        if detail:
            entry["detail"] = detail
        checks.append(entry)

    start = time.time()

    # Discover utils
    mtx_bin = _find_util(["mtx"])
    mt_bin = _find_util(["mt", "mt-st"])
    stenc_bin = _find_util(["stenc"])

    if changer:
        if mtx_bin:
            result = runner.run([mtx_bin, "-f", changer, "status"], name="mtx_status", allow_retry=True)
            commands.append({"name": "mtx_status", **_format_command_result(result)})
            if result.returncode == 0 and not result.timed_out:
                add_check("Tape Library (mtx)", "pass", "mtx status OK")
            else:
                add_check("Tape Library (mtx)", "warning", result.error_message or "mtx status failed")
        else:
            add_check("Tape Library (mtx)", "error", "mtx utility not found in PATH")
    else:
        add_check("Tape Library (mtx)", "skipped", "No changer device detected")

    if drive:
        if mt_bin:
            result = runner.run([mt_bin, "-f", drive, "status"], name="mt_status", allow_retry=True)
            commands.append({"name": "mt_status", **_format_command_result(result)})
            if result.returncode == 0 and not result.timed_out:
                add_check(f"Tape Drive ({os.path.basename(mt_bin)})", "pass", "mt status OK")
            else:
                add_check(f"Tape Drive ({os.path.basename(mt_bin)})", "warning", result.error_message or "mt status failed")
        else:
            add_check("Tape Drive (mt)", "error", "mt/mt-st utility not found in PATH")

        # Check for stenc (Hardware Encryption)
        if stenc_bin:
            stenc_result = runner.run([stenc_bin, "--version"], name="stenc_check", allow_retry=False)
            if stenc_result.returncode == 0:
                add_check("Hardware Encryption (stenc)", "pass", "stenc utility found")
            else:
                add_check("Hardware Encryption (stenc)", "warning", "stenc utility found but failed to run")
        else:
            add_check("Hardware Encryption (stenc)", "warning", "stenc utility not found; hardware encryption unavailable")
    else:
        add_check("Tape Drive (mt)", "skipped", "No tape drive detected")

    duration = time.time() - start
    statuses = [c["status"] for c in checks]
    if "fail" in statuses:
        overall = "fail"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "pass"

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_seconds": round(duration, 2),
        "overall": overall,
        "devices": devices,
        "health": health,
        "permissions": permissions,
        "checks": checks,
        "commands": commands,
    }


def render_report(report: Dict[str, object]) -> None:
    """Render the diagnostic report using Rich."""
    console.print()
    console.print(Panel("[header]FossilSafe Tape Diagnostics[/header]", expand=False))
    console.print(f"Timestamp: [info]{report['timestamp']}[/info]")
    console.print(f"Duration:  [info]{report['duration_seconds']}s[/info]")
    
    status_color = "pass" if report["overall"] == "pass" else "warning" if report["overall"] == "warning" else "error"
    console.print(f"Overall Status: [{status_color}]{report['overall'].upper()}[/{status_color}]")
    console.print()

    # Hardware Table
    hw_table = Table(title="Hardware Status")
    hw_table.add_column("Component", style="cyan")
    hw_table.add_column("Device Node", style="magenta")
    hw_table.add_column("Status", style="bold")
    
    devices = report.get("devices", {})
    health = report.get("health", {})
    
    # Check actual device node existence in current environment
    changer_node = devices.get("changer_sg")
    drive_node = devices.get("drive_nst")
    
    changer_status = "[pass]OK" if changer_node and os.path.exists(changer_node) else "[error]OFFLINE"
    drive_status = "[pass]OK" if drive_node and os.path.exists(drive_node) else "[error]OFFLINE"
    
    hw_table.add_row("Changer (SG)", changer_node or "N/A", changer_status)
    hw_table.add_row("Drive (NST)", drive_node or "N/A", drive_status)
    console.print(hw_table)
    console.print()

    # Checks Table
    checks_table = Table(title="Diagnostic Checks")
    checks_table.add_column("Check", style="cyan")
    checks_table.add_column("Result", style="bold")
    checks_table.add_column("Message")
    
    for check in report.get("checks", []):
        color = "pass" if check["status"] == "pass" else "warning" if check["status"] == "warning" else "error"
        if check["status"] == "skipped":
            color = "info"
        checks_table.add_row(check["name"], f"[{color}]{check['status'].upper()}[/{color}]", check["message"])
    
    console.print(checks_table)
    console.print()

    # Permissions Section
    if report.get("permissions", {}).get("errors"):
        console.print("[error]Permission Issues Detected:[/error]")
        for err in report["permissions"]["errors"]:
            console.print(f" - {err}")
        console.print()
    else:
        console.print("[pass]✓ No permission issues detected.[/pass]")
        console.print()


def main() -> None:
    parser = argparse.ArgumentParser(description="FossilSafe Tape Diagnostics")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of pretty TUI")
    args = parser.parse_args()

    # When running as a script, we might not be in the right path for imports
    # but the sys.path modification at the top handles it if PYTHONPATH is set.
    
    report = run_diagnostics()
    
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        render_report(report)


if __name__ == "__main__":
    main()
