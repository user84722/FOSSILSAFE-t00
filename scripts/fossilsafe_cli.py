#!/usr/bin/env python3
"""
FossilSafe Unified CLI Tool
Control your FossilSafe appliance from the command line.

Environment Variables:
  FOSSILSAFE_API_URL  Base URL of the backend (default: http://127.0.0.1:5000)
  FOSSILSAFE_API_KEY  API Key for authentication (X-API-Key header)
"""

import os
import sys
import json
import click
import urllib.request
import urllib.error
import urllib.parse
import time
from datetime import datetime
from typing import Optional, Dict, Any

try:
    from rich.console import Console
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.live import Live
    from rich.table import Table
    from rich.text import Text
    from rich.align import Align
    from rich.box import ROUNDED
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

class FossilSafeAPI:
    def __init__(self, base_url: str, api_key: Optional[str]):
        self.base_url = base_url.rstrip('/')
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["X-API-Key"] = api_key

    def request(self, method: str, path: str, params: Optional[Dict] = None, json_data: Optional[Dict] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
            
        data = None
        if json_data:
            data = json.dumps(json_data).encode('utf-8')
            
        req = urllib.request.Request(url, data=data, headers=self.headers, method=method)
        
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode('utf-8')
                if not body: return {}
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    return {"text": body}
                    
        except urllib.error.HTTPError as e:
            msg = e.reason
            try:
                err_body = e.read().decode('utf-8')
                err_json = json.loads(err_body)
                msg = err_json.get('error') or err_json.get('message') or msg
            except: pass
            
            if e.code == 401:
                click.secho("Error: Unauthorized. Check FOSSILSAFE_API_KEY.", fg="red", err=True)
            else:
                click.secho(f"Error ({e.code}): {msg}", fg="red", err=True)
            sys.exit(1)
        except urllib.error.URLError as e:
            click.secho(f"Error: Could not connect to {self.base_url}. Is the backend running? Reason: {e.reason}", fg="red", err=True)
            sys.exit(1)
        except Exception as e:
            click.secho(f"Error: {e}", fg="red", err=True)
            sys.exit(1)

class FossilSafeTUI:
    def __init__(self, api: 'FossilSafeAPI'):
        self.api = api
        self.console = Console()
        
    def get_layout(self) -> Layout:
        layout = Layout()
        layout.split(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=3)
        )
        layout["main"].split_row(
            Layout(name="left", ratio=2),
            Layout(name="right", ratio=1)
        )
        return layout

    def render_header(self) -> Panel:
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="right")
        grid.add_row(
            "[b]FOSSILSAFE[/b] | [dim]Unified Control Dashboard[/dim]",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        return Panel(grid, style="white on blue", box=ROUNDED)

    def render_system_health(self) -> Panel:
        try:
            # We use a shorter timeout for the TUI to keep it responsive
            data = self.api.request("GET", "/api/healthz").get('data', {})
            ok = data.get('ok', False)
            status_text = "[bold green]HEALTHY[/bold green]" if ok else "[bold red]UNHEALTHY[/bold red]"
            
            table = Table.grid(padding=(0, 1))
            table.add_row("Status:", status_text)
            table.add_row("Uptime:", f"{data.get('uptime', 0):.1f}s")
            table.add_row("API Version:", data.get('version', 'unknown'))
            return Panel(table, title="[b]System Health[/b]", border_style="cyan", box=ROUNDED)
        except:
            return Panel("Backend Connection Lost", title="[b]System Health[/b]", border_style="red", box=ROUNDED)

    def render_active_jobs(self) -> Panel:
        try:
            jobs = self.api.request("GET", "/api/jobs").get('jobs', [])
            if not jobs:
                return Panel(Align.center("\n[grey50]No active jobs in queue[/grey50]"), title="[b]Active Workflows[/b]", border_style="green", box=ROUNDED)
            
            table = Table(box=None, expand=True)
            table.add_column("ID", style="dim")
            table.add_column("Type")
            table.add_column("Status")
            table.add_column("Progress")
            
            for j in jobs[:5]:
                progress = j.get('progress', 0)
                color = "green" if progress > 50 else "yellow"
                table.add_row(
                    str(j.get('id')),
                    j.get('type', 'unknown').upper(),
                    j.get('status', 'unknown'),
                    f"[{color}]{progress}%[/{color}]"
                )
            return Panel(table, title="[b]Active Workflows[/b]", border_style="green", box=ROUNDED)
        except:
            return Panel("Error fetching job list", title="[b]Active Workflows[/b]", border_style="red", box=ROUNDED)

    def render_hardware_summary(self) -> Panel:
        try:
            data = self.api.request("GET", "/api/status").get('data', {})
            drives = data.get('drives', [])
            changers = data.get('changers', [])
            
            table = Table.grid(padding=(0, 1))
            table.add_row("Libraries:", f"[bold]{len(changers)}[/bold]")
            table.add_row("Drives:", f"[bold]{len(drives)}[/bold]")
            table.add_row("", "")
            
            for d in drives[:3]:
                status = d.get('status', 'unknown')
                color = "green" if status == "idle" else "yellow"
                table.add_row(f"  {d.get('id', 'drive')[:10]}:", f"[{color}]{status.upper()}[/{color}]")
                
            return Panel(table, title="[b]Hardware Topology[/b]", border_style="magenta", box=ROUNDED)
        except:
            return Panel("Hardware interface offline", title="[b]Hardware Topology[/b]", border_style="red", box=ROUNDED)

    def render_footer(self) -> Panel:
        return Panel(
            Align.center("[b]CTRL+C[/b] to Exit | Unified CLI Appliance Console"),
            style="white on grey11", box=ROUNDED
        )

@click.group()
@click.pass_context
def cli(ctx):
    """FossilSafe appliance management utility."""
    base_url = os.environ.get("FOSSILSAFE_API_URL", "http://127.0.0.1:5000")
    api_key = os.environ.get("FOSSILSAFE_API_KEY")
    ctx.obj = FossilSafeAPI(base_url, api_key)

@cli.command(name="dashboard")
@click.pass_obj
def dashboard(api):
    """Interactive real-time appliance dashboard."""
    if not RICH_AVAILABLE:
        click.secho("Error: 'rich' library is required for dashboard mode.", fg="red")
        click.echo("Install it with: pip install rich")
        return

    tui = FossilSafeTUI(api)
    layout = tui.get_layout()
    
    with Live(layout, refresh_per_second=2, screen=True):
        try:
            while True:
                layout["header"].update(tui.render_header())
                layout["left"].update(tui.render_active_jobs())
                layout["right"].update(
                    Layout().split(
                        Layout(tui.render_system_health()),
                        Layout(tui.render_hardware_summary())
                    )
                )
                layout["footer"].update(tui.render_footer())
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass

# --- System Commands ---
@cli.group()
def system():
    """System information and diagnostics."""
    pass

@system.command(name="status")
@click.pass_obj
def system_status(api):
    """Show system status summary."""
    res = api.request("GET", "/api/status")
    click.echo(json.dumps(res.get('data', {}), indent=2))

@system.command(name="health")
@click.pass_obj
def system_health(api):
    """Check system health status."""
    res = api.request("GET", "/api/healthz")
    data = res.get('data', {})
    status = click.style("OK", fg="green") if data.get('ok') else click.style("ERROR", fg="red")
    click.echo(f"Overall Health: {status}")
    click.echo(json.dumps(data, indent=2))

@system.group(name="diag")
def system_diag():
    """Hardware diagnostics and support bundles."""
    pass

@system_diag.command(name="run")
@click.option("--type", "diag_type", default="smart", help="Diagnostic type (smart, mechanical, full)")
@click.pass_obj
def system_diag_run(api, diag_type):
    """Initiate a new hardware diagnostic check."""
    res = api.request("POST", "/api/diagnostics/run", json_data={"type": diag_type})
    click.echo(f"Diagnostic started. Report ID: {res.get('data', {}).get('id')}")

@system_diag.command(name="history")
@click.option("--limit", default=10, help="Number of reports to show")
@click.pass_obj
def system_diag_history(api, limit):
    """List historical diagnostic reports."""
    res = api.request("GET", "/api/diagnostics/reports", params={"limit": limit})
    reports = res.get('reports', [])
    click.echo(f"{'ID':<6} {'Date':<20} {'Status':<12} {'Summary'}")
    click.echo("-" * 70)
    for r in reports:
        click.echo(f"{r.get('id'):<6} {r.get('created_at', ''):<20} {r.get('status', ''):<12} {r.get('summary', '')}")

@system_diag.command(name="bundle")
@click.pass_obj
def system_support_bundle(api):
    """Generate and trigger support bundle creation."""
    res = api.request("POST", "/api/diagnostics/bundle")
    click.echo(f"Support bundle generation initiated: {res.get('message')}")

@system.command(name="libraries")
@click.pass_obj
def system_libraries(api):
    """List configured tape libraries."""
    res = api.request("GET", "/api/system/libraries")
    libs = res.get('data', []) if isinstance(res.get('data'), list) else res.get('data', {}).get('data', [])
    click.echo(f"{'ID':<20} {'Name':<30} {'Default'}")
    click.echo("-" * 60)
    for lib in libs:
        is_def = "*" if lib.get('is_default') else ""
        click.echo(f"{lib.get('id'):<20} {lib.get('display_name'):<30} {is_def}")

# --- Tape Commands ---
@cli.group()
def tape():
    """Tape library and media operations."""
    pass

@tape.command(name="list")
@click.pass_obj
def tape_list(api):
    """List current media inventory."""
    res = api.request("GET", "/api/tapes")
    tapes = res.get('data', {}).get('tapes', [])
    click.echo(f"{'Barcode':<12} {'Slot':<10} {'State':<10} {'Active Job'}")
    click.echo("-" * 50)
    for t in tapes:
        state = "FULL" if t.get('drive_full') else "EMPTY"
        locCode = f"Dr{t.get('drive_index')}" if t.get('location_type') == 'drive' else f"S{t.get('slot')}"
        click.echo(f"{t.get('barcode') or '<NO ID>':<12} {locCode:<10} {state:<10} {t.get('active_job_id') or ''}")

@tape.command(name="load")
@click.argument("barcode")
@click.option("--drive", type=int, default=0, help="Drive index")
@click.pass_obj
def tape_load(api, barcode, drive):
    """Load media into a drive."""
    res = api.request("POST", f"/api/library/load/{barcode}", params={"drive": drive})
    click.echo(res.get('message', 'Load command sent'))

@tape.command(name="unload")
@click.option("--drive", type=int, default=0, help="Drive index")
@click.pass_obj
def tape_unload(api, drive):
    """Unload media from a drive."""
    res = api.request("POST", "/api/library/unload", json_data={"drive": drive})
    click.echo(res.get('message', 'Unload command sent'))

# --- KMS Commands ---
@cli.group()
def kms():
    """Key Management System configuration."""
    pass

@kms.command(name="status")
@click.pass_obj
def kms_status(api):
    """Show KMS provider health and status."""
    res = api.request("GET", "/api/kms/status")
    click.echo(json.dumps(res, indent=2))

@kms.command(name="rotate")
@click.option("--yes", is_flag=True, help="Skip confirmation")
@click.pass_obj
def kms_rotate(api, yes):
    """Rotate the master encryption key."""
    if not yes:
        click.confirm("Are you sure you want to rotate the master encryption key?", abort=True)
    res = api.request("POST", "/api/kms/rotate")
    click.echo(res.get('message', 'Rotation initiated'))

# --- Audit Commands ---
@cli.group()
def audit():
    """Audit log inspection and verification."""
    pass

@audit.command(name="verify")
@click.pass_obj
def audit_verify(api):
    """Trigger a cryptographic integrity audit."""
    res = api.request("POST", "/api/audit/verify")
    if res.get('success'):
        click.secho("Integrity Verified: Sound", fg="green")
    else:
        click.secho(f"Integrity Failed: {res.get('message')}", fg="red")

@audit.command(name="history")
@click.option("--limit", default=10, help="Number of records to show")
@click.pass_obj
def audit_history(api, limit):
    """Show historical integrity audit results."""
    res = api.request("GET", "/api/audit/verification-history", params={"limit": limit})
    history = res.get('history', [])
    click.echo(f"{'Date':<20} {'Result':<10} {'Entries':<8} {'Error'}")
    click.echo("-" * 60)
    for h in history:
        res_str = click.style("VALID", fg="green") if h.get('valid') else click.style("FAILED", fg="red")
        click.echo(f"{h.get('timestamp', ''):<20} {res_str:<10} {h.get('total_entries', 0):<8} {h.get('error_message') or ''}")

# --- Job Commands ---
@cli.group()
def job():
    """Backup and maintenance job management."""
    pass

@job.command(name="list")
@click.option("--limit", default=10, help="Number of jobs to show")
@click.pass_obj
def job_list(api, limit):
    """List recent job execution status."""
    res = api.request("GET", "/api/jobs", params={"limit": limit})
    jobs = res.get('data', {}).get('jobs', [])
    click.echo(f"{'ID':<6} {'Name':<25} {'Type':<12} {'Status':<15}")
    click.echo("-" * 65)
    for j in jobs:
        status_style = {"fg": "green"} if j['status'] == 'completed' else {"fg": "red"} if j['status'] == 'failed' else {"fg": "yellow"}
        click.echo(f"{j['id']:<6} {j.get('name', '')[:25]:<25} {j['type']:<12} ", nl=False)
        click.secho(j['status'], **status_style)

@job.command(name="cancel")
@click.argument("job_id", type=int)
@click.pass_obj
def job_cancel(api, job_id):
    """Cancel an active job."""
    res = api.request("POST", f"/api/jobs/{job_id}/cancel")
    if res.get('success'):
        click.secho(f"Job {job_id} cancelled.", fg="green")
    else:
        click.secho(f"Failed to cancel job {job_id}: {res.get('error')}", fg="red")

@job.command(name="pause")
@click.argument("job_id", type=int)
@click.pass_obj
def job_pause(api, job_id):
    """Pause an active job."""
    res = api.request("POST", f"/api/jobs/{job_id}/pause")
    if res.get('success'):
        click.secho(f"Job {job_id} paused.", fg="green")
    else:
        click.secho(f"Failed to pause job {job_id}: {res.get('error')}", fg="red")



# --- Settings Commands ---
@cli.group()
def settings():
    """Appliance configuration and settings."""
    pass

@settings.command(name="show")
@click.pass_obj
def settings_show(api):
    """Show current appliance settings."""
    res = api.request("GET", "/api/settings")
    click.echo(json.dumps(res.get('data', getattr(res, 'data', {})), indent=2))

if __name__ == "__main__":
    cli()
