#!/usr/bin/env python3
import argparse
import os
import re
import time
import random
import string
import subprocess
from datetime import datetime
from typing import Dict, List, Optional

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn
from rich.panel import Panel
from rich.live import Live

# Ensure standard paths
LTO_TOOL_PATHS = ["/usr/sbin", "/sbin", "/usr/local/sbin", "/usr/bin", "/bin"]
for p in LTO_TOOL_PATHS:
    if p not in os.environ.get("PATH", "").split(":"):
        os.environ["PATH"] = f"{os.environ.get('PATH', '')}:{p}"

from backend.config_store import load_config, load_state
from backend.tape.devices import get_devices
from backend.tape.runner import TapeCommandRunner

console = Console()

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

class TapeBenchmark:
    def __init__(self, changer: str, drive: str, barcode: str):
        self.changer = changer
        self.drive = drive
        self.barcode = barcode
        self.runner = TapeCommandRunner()
        self.results = []
        
        # Discover utils
        self.mtx_bin = _find_util(["mtx"])
        self.mt_bin = _find_util(["mt", "mt-st"])
        self.ltfs_bin = _find_util(["ltfs"])
        self.umount_bin = _find_util(["umount"])

    def log_result(self, operation: str, duration: float, status: str, details: str = ""):
        self.results.append({
            "operation": operation,
            "duration": round(duration, 2),
            "status": status,
            "details": details
        })

    def run_command(self, cmd: List[str], name: str) -> bool:
        start = time.time()
        result = self.runner.run(cmd, name=name, allow_retry=True)
        duration = time.time() - start
        
        status = "PASS" if result.returncode == 0 else "FAIL"
        details = result.error_message if result.returncode != 0 else ""
        self.log_result(name, duration, status, details)
        return result.returncode == 0

    def benchmark_throughput(self, mb_to_write: int = 1000):
        """Benchmark write and read throughput."""
        if not self.ltfs_bin:
            console.print("[bold red]ltfs utility not found. Skipping throughput test.[/bold red]")
            return

        test_file = "/tmp/fsafe_bench.dat"
        chunk_size = 1024 * 1024 # 1MB
        
        console.print(f"[cyan]Generating {mb_to_write}MB of test data...[/cyan]")
        with open(test_file, 'wb') as f:
            for _ in range(mb_to_write):
                f.write(os.urandom(chunk_size))

        # Mount LTFS
        console.print("[cyan]Mounting LTFS...[/cyan]")
        mount_point = "/mnt/fsafe_bench"
        os.makedirs(mount_point, exist_ok=True)
        
        start_mount = time.time()
        # Use found ltfs binary
        res = self.runner.run(["sudo", self.ltfs_bin, "-o", f"devname={self.drive}", mount_point], name="ltfs_mount")
        if res.returncode != 0:
            console.print(f"[bold red]Failed to mount LTFS: {res.error_message}[/bold red]")
            return
        self.log_result("ltfs_mount", time.time() - start_mount, "PASS")

        try:
            # Write Test
            dest_path = os.path.join(mount_point, "bench_data.dat")
            console.print(f"[cyan]Writing {mb_to_write}MB to tape...[/cyan]")
            
            with Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
            ) as progress:
                task = progress.add_task("Writing...", total=mb_to_write * chunk_size)
                
                start_write = time.time()
                with open(test_file, 'rb') as src, open(dest_path, 'wb') as dst:
                    while True:
                        chunk = src.read(chunk_size)
                        if not chunk:
                            break
                        dst.write(chunk)
                        os.fsync(dst.fileno()) # Ensure it hits the drive buffer
                        progress.update(task, advance=len(chunk))
                
                duration_write = time.time() - start_write
                throughput_write = mb_to_write / duration_write
                self.log_result("write_throughput", duration_write, "PASS", f"{throughput_write:.2f} MB/s")
                console.print(f"[green]Write completed at {throughput_write:.2f} MB/s[/green]")

            # Read Test
            console.print(f"[cyan]Reading {mb_to_write}MB from tape...[/cyan]")
            with Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
            ) as progress:
                task = progress.add_task("Reading...", total=mb_to_write * chunk_size)
                
                start_read = time.time()
                read_back_path = "/tmp/fsafe_readback.dat"
                with open(dest_path, 'rb') as src, open(read_back_path, 'wb') as dst:
                    while True:
                        chunk = src.read(chunk_size)
                        if not chunk:
                            break
                        dst.write(chunk)
                        progress.update(task, advance=len(chunk))
                
                duration_read = time.time() - start_read
                throughput_read = mb_to_write / duration_read
                self.log_result("read_throughput", duration_read, "PASS", f"{throughput_read:.2f} MB/s")
                console.print(f"[green]Read completed at {throughput_read:.2f} MB/s[/green]")

        finally:
            console.print("[cyan]Unmounting LTFS...[/cyan]")
            start_umount = time.time()
            if self.umount_bin:
                self.runner.run(["sudo", self.umount_bin, mount_point], name="ltfs_umount")
            else:
                self.runner.run(["sudo", "umount", mount_point], name="ltfs_umount")
            self.log_result("ltfs_umount", time.time() - start_umount, "PASS")
            
            # Clean up
            if os.path.exists(test_file): os.remove(test_file)
            if os.path.exists("/tmp/fsafe_readback.dat"): os.remove("/tmp/fsafe_readback.dat")

    def find_slot_for_barcode(self, barcode: str) -> Optional[int]:
        """Find the slot number for a given barcode using mtx status."""
        if not self.mtx_bin:
            return None
        
        result = self.runner.run(["sudo", self.mtx_bin, "-f", self.changer, "status"], name="mtx_status")
        if result.returncode != 0:
            return None
        
        # Check Data Transfer Elements first (already loaded)
        # Data Transfer Element 0:Full (Storage Element 1 Loaded):VolumeTag = HDA044L6
        pattern_loaded = re.compile(rf"Data Transfer Element \d+:Full \(Storage Element (\d+) Loaded\).*?VolumeTag\s*=\s*{re.escape(barcode)}", re.IGNORECASE)
        match_loaded = pattern_loaded.search(result.stdout)
        if match_loaded:
            return int(match_loaded.group(1))

        # Check Storage Elements
        # Storage Element 1:Full :VolumeTag=HDA044L6
        pattern_slot = re.compile(rf"Storage Element (\d+):Full.*?VolumeTag\s*=\s*{re.escape(barcode)}", re.IGNORECASE)
        match_slot = pattern_slot.search(result.stdout)
        if match_slot:
            return int(match_slot.group(1))
        return None

    def is_tape_loaded(self, barcode: str, drive_idx: int = 0) -> bool:
        """Check if the given barcode is already loaded in the specified drive."""
        if not self.mtx_bin:
            return False
        result = self.runner.run(["sudo", self.mtx_bin, "-f", self.changer, "status"], name="mtx_status")
        if result.returncode != 0:
            return False
        pattern = re.compile(rf"Data Transfer Element {drive_idx}:Full.*?VolumeTag\s*=\s*{re.escape(barcode)}", re.IGNORECASE)
        return bool(pattern.search(result.stdout))

    def run_full_benchmark(self, size_mb: int = 500):
        if not self.mtx_bin:
            console.print("[bold red]mtx utility not found. Cannot run benchmark.[/bold red]")
            return

        # Find slot
        console.print(f"[cyan]Searching for tape {self.barcode}...[/cyan]")
        slot = self.find_slot_for_barcode(self.barcode)
        if not slot:
            console.print(f"[bold red]Tape {self.barcode} not found in any slot.[/bold red]")
            return
        
        console.print(Panel(f"[header]Hardware Benchmark: {self.barcode} (Slot {slot})[/header]", expand=False))
        
        # 1. Load Tape
        if self.is_tape_loaded(self.barcode):
            console.print(f"[green]Tape {self.barcode} is already loaded in drive 0.[/green]")
        else:
            console.print(f"[cyan]Loading tape {self.barcode} (Slot {slot}) into drive 0...[/cyan]")
            if not self.run_command(["sudo", self.mtx_bin, "-f", self.changer, "load", str(slot), "0"], "mtx_load"):
                return

        try:
            # 2. Throughput Test
            self.benchmark_throughput(size_mb)

        finally:
            # 3. Unload Tape
            console.print(f"[cyan]Unloading tape into slot {slot}...[/cyan]")
            self.run_command(["sudo", self.mtx_bin, "-f", self.changer, "unload", str(slot), "0"], "mtx_unload")

    def print_summary(self):
        table = Table(title="Benchmark Results Summary")
        table.add_column("Operation", style="cyan")
        table.add_column("Duration (s)", justify="right")
        table.add_column("Status", style="bold")
        table.add_column("Details")

        for res in self.results:
            color = "green" if res["status"] == "PASS" else "red"
            table.add_row(
                res["operation"],
                str(res["duration"]),
                f"[{color}]{res['status']}[/{color}]",
                res["details"]
            )
        
        console.print()
        console.print(table)


def main():
    parser = argparse.ArgumentParser(description="LTO Hardware Benchmark Utility")
    parser.add_argument("--barcode", required=True, help="Barcode of the test tape")
    parser.add_argument("--size", type=int, default=500, help="Test file size in MB")
    args = parser.parse_args()

    config = load_config()
    state = load_state()
    devices, _ = get_devices(config, state)
    
    changer = devices.get("changer_sg")
    drive = devices.get("drive_sg") # For LTFS we usually need the sg device for some commands, or nst
    # Wait, ltfs tool uses the tape device (e.g. /dev/st0 or /dev/nst0)
    drive_node = devices.get("drive_nst") or "/dev/nst0"

    if not changer or not drive_node:
        console.print("[bold red]Hardware not detected accurately. Run diag.py first.[/bold red]")
        return

    bench = TapeBenchmark(changer, drive_node, args.barcode)
    bench.run_full_benchmark(args.size)
    bench.print_summary()

if __name__ == "__main__":
    main()
