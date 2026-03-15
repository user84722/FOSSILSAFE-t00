#!/usr/bin/env python3
"""
Tape Library Controller - Hardware interface for LTO tape libraries

Supports common homelab tape libraries including:
- HP/HPE StoreEver series (MSL2024, MSL4048, MSL6480, etc.)
- Dell PowerVault TL series (TL1000, TL2000, TL4000)
- IBM TS series (TS2900, TS3100, TS3200, TS4300)
- Quantum Scalar series (i3, i6, i40, i80)
- Fujitsu Eternus LT series
- Overland NEO series
- Tandberg/Overland StorageLoader
- Most SCSI/SAS tape libraries with mtx support

Handles all SCSI/SAS communication with tape drive and library changer
"""

import os
import re
import shutil
import subprocess
import time
import threading
import logging
from datetime import datetime
from collections import deque
from typing import Dict, List, Optional, Tuple

from backend.config_store import load_config, load_state, update_state
from backend.tape.devices import discover_devices, get_devices, is_medium_changer_device
from backend.tape.runner import CommandResult, TapeCommandRunner
from backend.exceptions import (
    HardwareError, HardwareCommunicationError, TapeLoadError, 
    TapeUnloadError, TapeMountError, TapeUnmountError, CalibrationError,
    ComplianceError, TapeFormatError, TapeError
)
from typing import Any
from backend.utils.hardware import get_tape_alerts

LIBRARY_ONLINE = "ONLINE"
LIBRARY_BUSY = "BUSY"
LIBRARY_DEGRADED = "DEGRADED"
LIBRARY_OFFLINE = "OFFLINE"

logger = logging.getLogger(__name__)


class TapeLibraryController:
    """Controller for LTO tape library operations (LTO-5 through LTO-9)"""
    
    # Global reentrant lock for hardware operations to prevent mechanical/bus collisions
    _changer_lock = threading.RLock()
    
    def __init__(
        self,
        device='/dev/nst0',
        changer='/dev/sg1',
        config=None,
        state=None,
        command_runner=None,
        log_callback=None,
        event_logger=None,
        db=None,
        webhook_service=None,
    ):
        """
        Initialize tape library controller
        
        Args:
            device: Tape drive device (e.g., /dev/nst0)
            changer: Changer device for robot arm (e.g., /dev/sg1), or None for drive-only mode
        """
        self.webhook_service = webhook_service
        self.drive_devices = {}
        if isinstance(device, dict):
            self.drive_devices = {int(k): v for k, v in device.items()}
        elif isinstance(device, (list, tuple)):
            self.drive_devices = {idx: path for idx, path in enumerate(device)}
        else:
            self.drive_devices = {0: device}
        
        self._library_info_cache = None
        self._last_library_info_at = 0
        
        self.device = self.drive_devices.get(0, device)
        self.changer = changer
        self.drive_sg = None
        self.drive_number = 0  # Default drive
        self.mounted_tape = None
        self.mounted_tapes = {}
        self.mount_points = {}
        self.drive_only_mode = False  # True if no changer/library
        self.manual_tape_barcode = None  # For drive-only mode
        self.library_error = None
        self.library_online = None
        self._base_library_state = LIBRARY_OFFLINE
        self._debounced_state = LIBRARY_OFFLINE
        self._state_changed_at = 0.0
        self._probe_failures = 0
        self._probe_failure_timestamps = deque(maxlen=20)
        
        # Status caching (FS-02)
        self._mtx_status_cache = None
        self._last_mtx_status_at = 0
        self._mt_status_cache = {}  # Dict[int, Tuple[float, CommandResult]]
        self._status_cache_ttl = float(os.environ.get('FOSSILSAFE_STATUS_CACHE_TTL', 2.0))
        self._offline_threshold = 3
        self._offline_window_seconds = 180
        self._state_debounce_seconds = 8
        self._busy_operations = 0
        self._last_mtx_check = None
        self._mtx_check_interval = 30
        self._auto_correct_attempted = False
        self._config = config or load_config()
        self._state = state or load_state()
        self.db = db
        
        # Load persisted mounted tapes
        tape_state = self._state.get("tape") if isinstance(self._state.get("tape"), dict) else {}
        persisted_mounts = tape_state.get("mounted_tapes")
        if isinstance(persisted_mounts, dict):
             self.mounted_tapes = {int(k): v for k, v in persisted_mounts.items()}
             self.mounted_tape = self.mounted_tapes.get(0)
             
        self._home_slots: Dict[int, Dict[str, object]] = {}
        self._load_home_slots()
        self._last_inventory = []
        self._last_inventory_at = None
        self._event_logger = event_logger
        self._last_hardware_op_time = 0.0
        self._hardware_op_cooldown = float((self._config.get("tape", {}) or {}).get("op_cooldown", 2.0))
        self.command_runner = command_runner or TapeCommandRunner(
            timeouts=(self._config.get("tape", {}) or {}).get("timeouts"),
            log_callback=log_callback,
        )
        tape_settings = self._config.get("tape", {}) or {}
        self._offline_threshold = int(tape_settings.get("offline_threshold", self._offline_threshold))
        self._offline_window_seconds = int(tape_settings.get("offline_window_seconds", self._offline_window_seconds))
        self._state_debounce_seconds = int(tape_settings.get("state_debounce_seconds", self._state_debounce_seconds))
        self._mtx_unload_order = str(tape_settings.get("mtx_unload_order", "slot_drive")).lower()
        if self._mtx_unload_order not in ("drive_slot", "slot_drive"):
            self._mtx_unload_order = "slot_drive"
        erase_timeout = tape_settings.get("erase_timeout_seconds")
        if erase_timeout:
            try:
                self.command_runner.timeouts["mt_erase"] = int(erase_timeout)
            except (TypeError, ValueError):
                pass
        self._last_probe = None

    def _log_event(self, level: str, message: str, details: Optional[Dict[str, object]] = None) -> None:
        if self.db and level == 'error' and details and details.get('barcode'):
            try:
                self.db.increment_tape_error_count(details.get('barcode'))
            except Exception:
                pass

        if not self._event_logger:
            return
        payload = {
            "level": level,
            "message": message,
            "details": details or {},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        try:
            self._event_logger(payload)
        except Exception:
            return

    def _refresh_inventory_cache(self, reason: str) -> None:
        if self.drive_only_mode:
            return
        try:
            self._last_mtx_check = None
            inventory = self.scan_barcodes()
            self._last_inventory = inventory
            self._last_inventory_at = time.time()
            self._log_event("info", "Library inventory refreshed", {"reason": reason, "count": len(inventory)})
        except Exception as exc:
            self._log_event("warning", "Library inventory refresh failed", {"reason": reason, "error": str(exc)})
        
    def initialize(self):
        """Initialize the tape library and verify connectivity"""
        self._resolve_devices()
        if not os.path.exists(self.device):
            raise HardwareError(f"Tape drive not found at {self.device}")
        
        # Check for changer - if not present, switch to drive-only mode
        if not self.changer or not os.path.exists(self.changer):
            self.drive_only_mode = True
            self._log_event("warning", "No tape library changer found - running in DRIVE-ONLY mode")
            self._log_event("info", f"Tape drive: {self.device}")
            self._log_event("info", "Manual tape changes will be required")
            self._set_base_state(LIBRARY_ONLINE if os.path.exists(self.device) else LIBRARY_OFFLINE)
            return
        
        
        # Library mode - test mtx status
        if self._check_library_status(force=True):
            self._log_event("info", f"Tape library initialized: {self.changer}")
            self.recover_state_on_startup()
        else:
            self._log_event("error", "Library changer not responding", {"error": str(self.library_error)})

    def recover_state_on_startup(self) -> None:
        """
        Sync internal state with physical reality after a fresh start.
        Crucial for recovering from power loss or crash where state.json is stale.
        """
        self._log_event("info", "Running startup state recovery...")
        
        # 1. Reset Stale 'BUSY' state
        # Since we just started, we cannot be running an operation yet.
        if self._busy_operations > 0:
            self._log_event("warning", f"Found stale BUSY count ({self._busy_operations}) in state. Reseting to 0.")
            self._busy_operations = 0
            
        # 2. Sync Drive Status (Reality Check)
        # state.json might say "Tape A Mounted", but drive might be empty or have Tape B.
        try:
            status = self.get_drive_status() # Probes hardware (mtx status)
            
            real_tape = status.get('loaded_tape')
            real_in_use = status.get('in_use')
            
            cached_tape = self._get_mounted_tape(self.drive_number)
            
            if real_tape != cached_tape:
                self._log_event("warning", f"State Mismatch Detected! State says '{cached_tape}', Hardware says '{real_tape}'")
                
                # Trust Hardware
                self._set_mounted_tape(self.drive_number, real_tape)
                
                # If hardware says empty, ensure we aren't tracking it as mounted
                if not real_in_use:
                    self._set_mounted_tape(self.drive_number, None)
                    
                self._log_event("info", "State synchronized with hardware reality.")
                
        except Exception as e:
            self._log_event("error", "Failed to verify drive status during recovery", {"error": str(e)})

        
    def is_online(self) -> bool:
        """Check if library is online and responding"""
        if self.drive_only_mode:
            # In drive-only mode, check if drive device exists
            return os.path.exists(self.device)
        return self._check_library_status()

    def get_library_state(self) -> str:
        if self._debounced_state == LIBRARY_OFFLINE:
            return LIBRARY_OFFLINE
        if self._busy_operations > 0:
            return LIBRARY_BUSY
        return self._debounced_state

    def is_busy(self) -> bool:
        return self._busy_operations > 0

    def get_library_error(self) -> Optional[str]:
        """Return last library error if available."""
        return self.library_error

    def get_last_probe(self) -> Optional[Dict[str, object]]:
        """Return last probe result if available."""
        return self._last_probe

    def safe_shutdown_cleanup(self) -> Dict[str, object]:
        """Best-effort cleanup on shutdown without blocking indefinitely."""
        results: Dict[str, Any] = {"attempted": [], "errors": []}
        for drive in sorted(self.drive_devices.keys()):
            try:
                current = self.get_current_tape(drive=drive)
                if not current:
                    continue
                if self._is_ltfs_mounted(drive=drive):
                    try:
                        self.unmount_ltfs(drive=drive)
                    except Exception as exc:
                        results["errors"].append(f"Drive {drive} unmount failed: {exc}")
                self.unload_tape(drive=drive)
                results["attempted"].append({"drive": drive, "action": "unload", "status": "ok"})
            except Exception as exc:
                results["errors"].append(f"Drive {drive} unload failed: {exc}")
        return results
    
    def is_drive_only(self) -> bool:
        """Check if running in drive-only mode (no changer)"""
        return self.drive_only_mode
    
    def set_manual_tape(self, barcode: str):
        """
        Set the barcode/name of manually inserted tape (drive-only mode).
        This should be called after the user physically inserts a tape.
        """
        self.manual_tape_barcode = barcode
        self._set_mounted_tape(0, barcode)

    def _set_mounted_tape(self, drive: int, barcode: Optional[str]):
        """Track mounted tape per drive."""
        self.mounted_tapes[drive] = barcode
        if drive == 0:
            self.mounted_tape = barcode

    def _get_mounted_tape(self, drive: int) -> Optional[str]:
        """Get mounted tape for a drive."""
        if drive == 0 and self.mounted_tape is not None:
            return self.mounted_tape
        return self.mounted_tapes.get(drive)

    def _get_mount_point(self, drive: int) -> str:
        """Get mount point for a drive."""
        if drive not in self.mount_points:
            # Use data_dir if available for hardened environments
            data_dir = os.environ.get('FOSSILSAFE_DATA_DIR', '/var/lib/fossilsafe')
            mnt_base = os.path.join(data_dir, 'mnt')
            self.mount_points[drive] = os.path.join(mnt_base, f'drive_{drive}')
        return self.mount_points[drive]

    def _get_device(self, drive: int) -> str:
        """Get device path for a drive."""
        return self.drive_devices.get(drive, self.device)

    def _get_device_sg(self, drive: int) -> Optional[str]:
        """Get SCSI generic device path for a drive."""
        # Check if we have a cached drive_sg
        if drive == 0 and self.drive_sg:
            return self.drive_sg
        
        # Try to resolve via sysfs if possible
        try:
            device_path = self._get_device(drive)
            if device_path:
                # Import here to avoid circular dependencies if any
                from backend.tape.devices import _scsi_tape_sg_path
                sg_path = _scsi_tape_sg_path(os.path.basename(device_path))
                if sg_path:
                    return sg_path
        except Exception:
            pass
            
        # Fallback to the primary drive_sg or the symlink
        return self.drive_sg or "/dev/fossilsafe-drive-sg"

    def _load_home_slots(self) -> None:
        tape_state = {}
        if isinstance(self._state, dict):
            tape_state = self._state.get("tape") if isinstance(self._state.get("tape"), dict) else {}
        raw = tape_state.get("home_slots") if isinstance(tape_state, dict) else {}
        if not isinstance(raw, dict):
            return
        for drive_key, entry in raw.items():
            try:
                drive = int(drive_key)
            except (TypeError, ValueError):
                continue
            if isinstance(entry, dict) and "slot" in entry:
                self._home_slots[drive] = {
                    "slot": entry.get("slot"),
                    "barcode": entry.get("barcode"),
                }

    def _persist_home_slots(self) -> None:
        try:
            state = load_state()
            tape_state = state.get("tape") if isinstance(state.get("tape"), dict) else {}
            tape_state["home_slots"] = {
                str(drive): {"slot": info.get("slot"), "barcode": info.get("barcode")}
                for drive, info in self._home_slots.items()
            }
            update_state({"tape": tape_state})
        except Exception:
            return

    def _remember_home_slot(self, drive: int, slot: int, barcode: Optional[str]) -> None:
        if slot is None:
            return
        self._home_slots[drive] = {"slot": slot, "barcode": barcode}
        self._persist_home_slots()

    def remember_home_slot(self, drive: int, slot: int, barcode: Optional[str]) -> None:
        """Record a drive's source slot for later unload."""
        self._remember_home_slot(drive, slot, barcode)

    def _clear_home_slot(self, drive: int) -> None:
        if drive in self._home_slots:
            self._home_slots.pop(drive, None)
            self._persist_home_slots()

    def _get_home_slot(self, drive: int, barcode: Optional[str]) -> Optional[int]:
        entry = self._home_slots.get(drive)
        if not entry:
            return None
        if barcode and entry.get("barcode") and entry.get("barcode") != barcode:
            return None
        return entry.get("slot")

    def _find_empty_slot(self) -> Optional[int]:
        try:
            result = self._run_mtx_command(['status'])
            if result.timed_out:
                return None
            for line in (result.stdout or "").splitlines():
                match = re.search(r'Storage Element (\d+):Empty', line)
                if match:
                    return int(match.group(1))
        except Exception:
            return None
        return None

    def _resolve_unload_slot(self, drive: int, barcode: Optional[str]) -> Optional[int]:
        home_slot = self._get_home_slot(drive, barcode)
        if home_slot is not None:
            return home_slot
        return self._find_empty_slot()
    def get_library_info(self):
        """Get library hardware information (cached)"""
        now = time.time()
        ttl = 60 # 1 minute cache
        
        # Return cache if valid
        if self._library_info_cache is not None and (now - self._last_library_info_at) < ttl:
            return self._library_info_cache
            
        # Check for active hardware-locking jobs to avoid blocking UI
        if self.db:
            try:
                active = self.db.get_active_jobs()
                drive_busy = any(j.get('status') == 'running' for j in active)
                if drive_busy and self._library_info_cache is not None:
                    # Return stale cache instead of blocking for 10s during backup
                    return self._library_info_cache
            except Exception:
                pass

            
        # Drive-only mode returns basic drive info
        if self.drive_only_mode:
            info = {
                'name': 'Standalone Tape Drive',
                'model': 'Unknown',
                'vendor': 'Unknown',
                'type': 'LTO',
                'drives': 1,
                'slots': 0,
                'device': self.device,
                'drive_device': self.device,
                'drive_only': True,
                'online': os.path.exists(self.device),
                'state': self.get_library_state()
            }
            
            # Try to get drive model from sg_inq
            try:
                sg_result = self.command_runner.run(
                    ['sg_inq', '-i', self.device],
                    name="sg_inquiry",
                    allow_retry=True,
                    lock=self._changer_lock
                )
                if sg_result.returncode == 0:
                    vendor_match = re.search(r'Vendor identification:\s*(.+)', sg_result.stdout)
                    product_match = re.search(r'Product identification:\s*(.+)', sg_result.stdout)
                    
                    if vendor_match:
                        info['vendor'] = vendor_match.group(1).strip()
                    if product_match:
                        info['model'] = product_match.group(1).strip()
                        info['name'] = f"{info['vendor']} {info['model']}".strip()
            except Exception:
                pass
            
            # Add encryption status
            info['encryption'] = self.get_encryption_status(0)
            
            return info
        
        # Library mode
        try:
            result = self._run_mtx_command(['status'])
            if getattr(result, "timed_out", False):
                raise HardwareCommunicationError("mtx status timed out")
            output = result.stdout
            
            # Parse library info from mtx status
            info = {
                'name': 'Tape Library',
                'model': 'Unknown',
                'vendor': 'Unknown',
                'type': 'LTO',
                'drives': 1,
                'slots': 0,
                'device': self.changer,
                'drive_device': self.device,
                'drive_only': False,
                'state': self.get_library_state()
            }
            
            # Count slots from mtx output
            slot_count = len(re.findall(r'Storage Element \d+:', output, re.I))
            drive_count = len(re.findall(r'Data Transfer Element \d+:', output, re.I))
            mailslot_count = len(re.findall(r'(?:Import/Export|Mail) Element \d+|Storage Element \d+ IMPORT/EXPORT', output, re.I))
            
            # Fetch mail slot configurations
            prefs = getattr(self, '_config', {}).get('preferences', {})
            ms_enabled = str(prefs.get('mail_slot_enabled', 'true')).lower() == 'true'
            ms_auto = str(prefs.get('mail_slot_auto_detect', 'true')).lower() == 'true'
            ms_manual = int(prefs.get('mail_slot_manual_index', 0))

            info['slots'] = slot_count
            info['drives'] = max(1, drive_count)
            info['mailslot_count'] = mailslot_count
            
            # Mailslot is "enabled" if toggle is on AND (it's auto-detected OR manually set)
            info['mailslot_enabled'] = ms_enabled and (
                (ms_auto and mailslot_count > 0) or 
                (not ms_auto and ms_manual > 0)
            )
            
            # Try to get model from sg_inq
            try:
                sg_result = self.command_runner.run(
                    ['sg_inq', self.changer],
                    name="sg_inq",
                    allow_retry=True,
                    lock=self._changer_lock
                )
                if sg_result.returncode == 0:
                    # Parse vendor/product
                    vendor_match = re.search(r'Vendor identification:\s*(.+)', sg_result.stdout)
                    product_match = re.search(r'Product identification:\s*(.+)', sg_result.stdout)
                    
                    if vendor_match:
                        info['vendor'] = vendor_match.group(1).strip()
                    if product_match:
                        info['model'] = product_match.group(1).strip()
                    if vendor_match and product_match:
                        info['name'] = f"{info['vendor']} {info['model']}"
            except Exception:
                pass
            
            # Add encryption status for default drive
            info['encryption'] = self.get_encryption_status(self.drive_number)
            
            self._library_info_cache = info
            self._last_library_info_at = now
            return info
        except Exception as e:
            return {
                'name': 'Tape Library',
                'model': 'Unknown',
                'vendor': 'Unknown',
                'type': 'LTO',
                'drives': 1,
                'slots': 0,
                'error': str(e)
            }
    
    def get_drive_status(self) -> Dict:
        """Get current drive status"""
        try:
            result = self._run_mtx_command(['status'])
            if getattr(result, "timed_out", False):
                return {
                    'available': False,
                    'in_use': False,
                    'error': 'mtx status timed out'
                }
            output = result.stdout
            
            status = {
                'available': True,
                'in_use': False,
                'loaded_tape': None,
                'drive_number': self.drive_number,
                'source_slot': None
            }
            
            drive_pattern = re.compile(
                r'Data Transfer Element (\d+):(Full|Empty)'
                r'(?:\s*\(Storage Element (\d+) Loaded\))?'
                r'(?:\s*:?VolumeTag\s*=\s*(\w+))?',
                re.IGNORECASE,
            )
            for line in output.split('\n'):
                drive_match = drive_pattern.search(line)
                if not drive_match:
                    continue
                drive_num = int(drive_match.group(1))
                if drive_num != self.drive_number:
                    continue
                is_full = drive_match.group(2) == 'Full'
                source_slot = int(drive_match.group(3)) if drive_match.group(3) else None
                barcode = drive_match.group(4) if drive_match.group(4) else None
                status['source_slot'] = source_slot
                if is_full:
                    status['loaded_tape'] = barcode
                    status['in_use'] = True
                break
            
            return status
        except Exception as e:
            return {
                'available': False,
                'in_use': False,
                'error': str(e)
            }

    def get_current_tape(self, drive: int = 0) -> Optional[Dict]:
        """Get the currently loaded tape barcode, if any."""
        if self.drive_only_mode:
            mounted = self._get_mounted_tape(drive)
            if mounted:
                return {'barcode': mounted, 'drive_only': True, 'drive': drive}
            return None

        status = self.get_drive_status()
        if status.get('in_use'):
            return {
                'barcode': status.get('loaded_tape'),
                'drive_only': False,
                'drive': drive,
                'has_barcode': bool(status.get('loaded_tape')),
                'source_slot': status.get('source_slot'),
            }
        return None
    
    def scan_barcodes(self) -> List[Dict]:
        """
        Scan library for tape barcodes using barcode reader
        Returns list of tapes with their barcode, slot, and status
        """
        return self.scan_library(mode="deep")

    def _log_scan_command(self, result: CommandResult, inventory_requested: bool, operation: str) -> None:
        self._log_event(
            "info",
            "Library scan command",
            {
                "command": result.command,
                "duration": result.duration,
                "inventory_requested": inventory_requested,
                "operation": operation,
            },
        )

    def _scan_status_only(self, inventory_requested: bool) -> List[Dict]:
        result = self._run_mtx_command(['status'])
        self._log_scan_command(result, inventory_requested=inventory_requested, operation="status")
        if getattr(result, "timed_out", False):
            raise HardwareCommunicationError("mtx status timed out")
        return self._parse_mtx_status(result.stdout)

    def scan_library(self, mode: str = "deep") -> List[Dict]:
        """
        Scan library inventory.

        Modes:
            - "fast": status only
            - "deep": inventory + status
        """
        mode = (mode or "deep").lower()
        if mode not in ("fast", "deep"):
            mode = "deep"

        if self.drive_only_mode:
            if not self.manual_tape_barcode:
                return []
            return [{
                'barcode': self.manual_tape_barcode,
                'slot': None,
                'status': 'available'
            }]

        try:
            inventory_requested = mode == "deep"
            if mode == "fast":
                return self._scan_status_only(inventory_requested=False)

            inventory_result = self._run_mtx_command(['inventory'])
            self._log_scan_command(inventory_result, inventory_requested=True, operation="inventory")
            if getattr(inventory_result, "timed_out", False):
                raise HardwareCommunicationError("mtx inventory timed out")

            return self._scan_status_only(inventory_requested=True)
        except Exception as e:
            raise HardwareError(f"Failed to scan barcodes: {e}")

    def _get_drive_barcode(self, device: str) -> Optional[str]:
        """
        Attempt to read barcode from tape drive using tapeinfo (MAM).
        Returns None if not supported or not found.
        """
        try:
            result = self.command_runner.run(
                ['tapeinfo', '-f', device],
                name="tapeinfo",
                allow_retry=True,
                timeout_sec=30
            )
            if result.returncode != 0:
                return None
            
            match = re.search(r'Barcode:\s+(\S+)', result.stdout)
            if match:
                return match.group(1).strip()
            return None
        except Exception:
            return None
    
    def inventory(self) -> List[Dict]:
        """
        Get complete inventory of tapes in library
        Alias for scan_barcodes for compatibility
        """
        return self.scan_barcodes()

    def inventory_may_have_changed(self) -> bool:
        """Best-effort indicator for library inventory changes."""
        if self.drive_only_mode:
            return False
        return self.get_library_state() != LIBRARY_BUSY
    
    def poll_tape_alerts(self, drive: int = 0) -> List[Dict]:
        """Poll for TapeAlerts on a specific drive and persist to DB if found."""
        target_sg = self.drive_sg
        if drive != 0:
            # Multi-drive support for SG is handled via discover_devices map
            # but TapeLibraryController currently focuses on a primary drive.
            # We'll stick to self.drive_sg for now or try to resolve it.
            pass
            
        if not target_sg:
            return []

        barcode = self._get_mounted_tape(drive)
        if not barcode:
            # Try to get it from hardware if state is empty
            barcode = self._get_drive_barcode(self._get_device(drive))

        alerts = get_tape_alerts(target_sg)
        drive_path = self._get_device(drive)
        if alerts and self.db and barcode:
            for alert in alerts:
                self.db.add_tape_alert(barcode, alert, drive_id=drive_path)
                if alert.get('severity') == 'critical':
                    self._log_event("error", f"CRITICAL TAPE ALERT [{barcode}]: {alert['name']}", {"barcode": barcode, "alert": alert})
                elif alert.get('severity') == 'warning':
                    self._log_event("warning", f"Tape Alert [{barcode}]: {alert['name']}", {"barcode": barcode, "alert": alert})
                
                if self.webhook_service:
                    self.webhook_service.trigger_event("TAPE_ALERT", {
                        "barcode": barcode,
                        "alert_code": alert.get('code'),
                        "severity": alert.get('severity'),
                        "description": alert.get('name')
                    })
        
        return alerts

    def load_tape(self, barcode: str, drive: int = 0) -> bool:
        """
        Load a tape from slot into drive
        
        In drive-only mode, this verifies that a tape is inserted and sets the barcode.
        The user must physically insert the tape before calling this.
        
        Args:
            barcode: Tape barcode (e.g., '000001L6')
            drive: Drive number (default 0)
        """
        # Drive-only mode: verify against hardware if possible
        if self.drive_only_mode:
            self._log_event("info", f"Drive-only mode: Verifying tape '{barcode}' in drive")
            
            # 1. Verify drive has a tape
            status = self.get_drive_status()
            # If status says empty but we are in drive-only mode, we might need to trust the user 
            # OR tapeinfo might show it's ready. mtx status often fails on standalone drives.
            # We'll rely on tapeinfo directly.
            
            detected_barcode = self._get_drive_barcode(self.device)
            
            if detected_barcode:
                 if detected_barcode != barcode:
                     self._log_event("warning", f"Barcode mismatch! Hardware reports '{detected_barcode}', user claimed '{barcode}'")
                     raise TapeLoadError(f"Barcode mismatch: Drive reports {detected_barcode}, but you requested {barcode}")
                 else:
                     self._log_event("info", f"Barcode verified: {detected_barcode}")
            else:
                self._log_event("warning", "Could not verify barcode from hardware (MAM empty or drive incorrect). trusting user input.")

            self.manual_tape_barcode = barcode
            self._set_mounted_tape(drive, barcode)
            return True
        
        # Library mode: use changer to load tape
        try:
            # Poll alerts for whatever was in the drive BEFORE loading new tape
            self.poll_tape_alerts(drive)
            
            self._refresh_inventory_cache("pre-load")
            status = self.get_drive_status()
            if status.get('in_use'):
                loaded_barcode = status.get('loaded_tape')
                if loaded_barcode == barcode:
                    self._set_mounted_tape(drive, barcode)
                    if status.get('source_slot') is not None:
                        self._remember_home_slot(drive, status.get('source_slot'), barcode)
                    return True
                if loaded_barcode or status.get('source_slot') is not None:
                    self._log_event("info", f"Drive {drive} is full; unloading before loading {barcode}")
                self.unload_tape(drive)
            # Find slot number for barcode
            tapes = self.scan_barcodes()
            slot = None
            for tape in tapes:
                if tape['barcode'] == barcode:
                    slot = tape['slot']
                    break
            
            if slot is None:
                raise TapeLoadError(f"Tape {barcode} not found in library")
            
            # Unload current tape if any
            if self._get_mounted_tape(drive):
                self.unload_tape(drive)
            
            # Load the tape
            self._log_event("info", f"Loading tape {barcode} from slot {slot} into drive {drive}")
            self._enter_busy()
            success = False
            error = None
            try:
                result = self._run_mtx_command(['load', str(slot), str(drive)])
                if result.timed_out:
                    if self._confirm_drive_loaded(barcode, drive):
                        self._set_mounted_tape(drive, barcode)
                        self._remember_home_slot(drive, slot, barcode)
                        success = True
                        return True
                    error = "Load timed out; drive state unknown"
                    raise TapeLoadError("Load timed out and drive state could not be confirmed")
                
                self._set_mounted_tape(drive, barcode)
                self._remember_home_slot(drive, slot, barcode)
                success = True
                
                if self.db:
                    try:
                        self.db.increment_tape_mount_count(barcode)
                    except Exception as e:
                        self._log_event("warning", "Failed to increment tape mount count", {"error": str(e)})

                return True
            except Exception as e:
                error = error or str(e)
                raise
            finally:
                self._exit_busy(success=success, error=error)
                self._refresh_inventory_cache("load")

        except Exception as e:
            raise TapeLoadError(f"Failed to load tape {barcode}: {e}")
        return False
    
    def unload_tape(self, drive: int = 0, dest_slot: Optional[int] = None) -> bool:
        """
        Unload tape from drive back to its slot
        
        In drive-only mode, this just clears the state. User must physically remove tape.
        
        Args:
            drive: Drive number (default 0)
        """
        try:
            mounted = self._get_mounted_tape(drive)
            drive_has_tape = bool(mounted)
            if not self.drive_only_mode and not drive_has_tape:
                self._refresh_inventory_cache("pre-unload")
                status = self.get_drive_status()
                drive_has_tape = bool(status.get('in_use'))
                if status.get('in_use') and mounted is None:
                    mounted = status.get('loaded_tape')
                if status.get('source_slot') is not None:
                    self._remember_home_slot(drive, status.get('source_slot'), mounted)
            if not drive_has_tape:
                self._log_event("info", "No tape currently loaded")
                return True
            
            # Unmount if LTFS mounted
            if self._is_ltfs_mounted(drive=drive):
                self.unmount_ltfs(drive=drive)
            
            # Drive-only mode: just clear state, user handles physical removal
            if self.drive_only_mode:
                self._log_event("info", f"Drive-only mode: Please remove tape '{mounted}' from drive")
                self._set_mounted_tape(drive, None)
                self.manual_tape_barcode = None
                return True
            
            # Library mode: use changer to unload
            home_slot = self._get_home_slot(drive, mounted)
            unload_slot = dest_slot if dest_slot is not None else self._resolve_unload_slot(drive, mounted)
            if unload_slot is None:
                self._set_base_state(LIBRARY_DEGRADED, error="No available slot found to unload tape")
                raise TapeUnloadError("No available slot found to unload tape")
            if home_slot is None:
                self._log_event("warning", f"Home slot unknown for {mounted}; unloading to slot {unload_slot}")
            
            # Send an eject (offline) command before robotic unload so kernel unlocks the medium
            import time
            device_path = self._get_device(drive)
            self._log_event("info", f"Sending offline/eject command to drive {drive}")
            
            # The drive firmware might still be flushing LTFS index for minutes after OS umount.
            # 'mt offline' will fail with EIO while the drive is busy. We must poll until it accepts the offline.
            mt_success = False
            mt_wait_start = time.time()
            while time.time() - mt_wait_start < 300: # 5 minutes max wait
                try:
                    res = self.command_runner.run(['mt', '-f', device_path, 'offline'], name="mt_offline")
                    if res.returncode == 0:
                        mt_success = True
                        break
                except Exception as mt_err:
                    if "Input/output error" in str(mt_err) or "Device or resource busy" in str(mt_err):
                        time.sleep(5)
                        continue
                    else:
                        self._log_event("warning", f"Drive offline command got unexpected error: {mt_err}")
                        break
                
                # Command didn't throw exception but maybe returned non-zero
                time.sleep(5)
                
            if mt_success:
                time.sleep(2) # Give drive firmware a moment to physically process the eject
            else:
                self._log_event("warning", "Drive offline command timed out or failed, attempting forced robotic unload anyway.")
                
            self._log_event("info", f"Unloading tape from drive {drive} to slot {unload_slot}")
            self._enter_busy()
            success = False
            error = None
            try:
                command = self.build_unload_command(drive, unload_slot)
                self._log_event("info", "Running mtx unload", {"command": command, "drive": drive, "slot": unload_slot})
                result = self._run_mtx_command(command)
                if result.timed_out:
                    if self._confirm_drive_unloaded(drive):
                        self._set_mounted_tape(drive, None)
                        self._clear_home_slot(drive)
                        success = True
                        
                        # Poll alerts AFTER successful unload
                        self.poll_tape_alerts(drive)
                        
                        return True
                    error = "Unload timed out; drive state unknown"
                    raise TapeUnloadError("Unload timed out and drive state could not be confirmed")
                
                self._set_mounted_tape(drive, None)
                self._clear_home_slot(drive)
                success = True
                
                # Poll alerts AFTER successful unload
                self.poll_tape_alerts(drive)
                
                return True
            except Exception as e:
                error = error or str(e)
                raise
            finally:
                self._exit_busy(success=success, error=error)
                self._refresh_inventory_cache("unload")

        except Exception as e:
            raise TapeUnloadError(f"Failed to unload tape: {e}")
        return False

    def force_unload_tape(self, drive: int = 0, dest_slot: Optional[int] = None) -> bool:
        """
        Force unload tape from drive and clear local state.

        This is intended for recovery when the controller believes the library
        is degraded or the mounted tape state is out of sync.
        """
        errors = []
        mounted = self._get_mounted_tape(drive)
        try:
            if self._is_ltfs_mounted(drive=drive):
                self.unmount_ltfs(drive=drive)
        except Exception as e:
            errors.append(f"unmount failed: {e}")

        if self.drive_only_mode:
            if mounted:
                self._log_event("info", f"Drive-only mode: Please remove tape '{mounted}' from drive")
            self._set_mounted_tape(drive, None)
            self.manual_tape_barcode = None
            if errors:
                raise Exception("; ".join(errors))
            return True

        unload_slot = dest_slot if dest_slot is not None else self._resolve_unload_slot(drive, mounted)
        if unload_slot is None:
            errors.append("No available slot found to unload tape")
            raise Exception("; ".join(errors))
        self._log_event("warning", f"Force unloading tape from drive {drive} to slot {unload_slot}")
        self._enter_busy()
        try:
            command = self.build_unload_command(drive, unload_slot)
            self._log_event("warning", "Running mtx force unload", {"command": command, "drive": drive, "slot": unload_slot})
            result = self._run_mtx_command(command)
            if result.timed_out and not self._confirm_drive_unloaded(drive):
                errors.append("Unload timed out; drive state unknown")
            self._set_mounted_tape(drive, None)
            self._clear_home_slot(drive)
            if errors:
                raise Exception("; ".join(errors))
            return True
        except Exception as e:
            self._set_mounted_tape(drive, None)
            raise TapeUnloadError(f"Force unload failed: {e}")
        finally:
            self._exit_busy(success=not errors, error="; ".join(errors) if errors else None)
            self._refresh_inventory_cache("force_unload")
        return False

    def move_tape(self, source: Dict[str, int], destination: Dict[str, int], barcode: Optional[str] = None) -> bool:
        """
        Move a tape between slots and drives using mtx.

        Args:
            source: {"type": "slot"|"drive", "value": int}
            destination: {"type": "slot"|"drive", "value": int}
            barcode: Optional barcode for updating mounted state.
        """
        if self.drive_only_mode:
            raise HardwareError("Tape moves require a library changer")

        source_type = source.get("type")
        dest_type = destination.get("type")
        source_value = source.get("value")
        dest_value = destination.get("value")

        if source_type not in ("slot", "drive", "mailslot") or dest_type not in ("slot", "drive", "mailslot"):
            raise HardwareError("Invalid source or destination type")
        if source_type == "slot" and dest_type == "slot" and source_value == dest_value:
            raise HardwareError("Source and destination slots must differ")
        if source_type == "drive" and dest_type == "drive":
            raise HardwareError("Drive-to-drive moves are not supported")

        self._refresh_inventory_cache("pre-move")
        self._enter_busy()
        success = False
        error = None
        try:
            if source_type == "slot" and dest_type == "drive":
                result = self._run_mtx_command(["load", str(source_value), str(dest_value)])
                if result.timed_out:
                    raise TapeLoadError("Load timed out")
                if barcode:
                    self._set_mounted_tape(dest_value, barcode)
                self._remember_home_slot(dest_value, source_value, barcode)
            elif source_type == "drive" and dest_type == "slot":
                drive_index = source_value
                slot_index = dest_value
                command = self.build_unload_command(drive_index, slot_index)
                self._log_event("info", "Running mtx unload for move", {"command": command, "drive": drive_index, "slot": slot_index})
                result = self._run_mtx_command(command)
                if result.timed_out:
                    raise TapeUnloadError("Unload timed out")
                self._set_mounted_tape(source_value, None)
                self._clear_home_slot(source_value)
            elif source_type == "slot" and dest_type == "slot":
                result = self._run_mtx_command(["transfer", str(source_value), str(dest_value)])
                if result.timed_out:
                    raise HardwareError("Transfer timed out")
            elif (source_type == "mailslot" or dest_type == "mailslot"):
                # mtx uses 'transfer' for slot-to-slot including mailslots
                # Some libraries might use 'import'/'export' commands but 'transfer' is more universal in mtx
                src_val = str(source_value)
                dst_val = str(dest_value)
                
                # We need to qualify the numbers if mtx requires it, 
                # but usually mtx treats them as a flat address space 
                # or uses specific transfer subcommands.
                # In most mtx versions, Import/Export elements are just another range of slots.
                result = self._run_mtx_command(["transfer", src_val, dst_val])
                if result.timed_out:
                    raise HardwareError("Mailslot transfer timed out")
            else:
                raise HardwareError("Unsupported move combination")
            success = True
            return True
        except Exception as e:
            error = str(e)
            raise
        finally:
            self._exit_busy(success=success, error=error)
            self._refresh_inventory_cache("move")

    def identify_drive_mapping(self, mtx_drive_index: int, barcode: str) -> Optional[str]:
        """
        Identify which logical /dev/nstX device correlates to an physical mtx drive index.
        
        Algorithm:
        1. Move a known barcode into the physical drive index.
        2. Scan all possible tape devices using tapeinfo.
        3. Match the barcode from MAM data.
        """
        if self.drive_only_mode:
            raise CalibrationError("Calibration requires a library changer")

        self._log_event("info", f"Starting drive identification for mtx drive {mtx_drive_index} with tape {barcode}")
        
        try:
            # 1. Physical move
            # Note: Data Transfer Element indices in mtx start at 0
            self.move_tape({"type": "slot", "value": -1}, {"type": "drive", "value": mtx_drive_index}, barcode=barcode)
            
            # 2. Sequential scan of all detected nst devices
            # We use the discover_devices logic to find all potential candidates
            potential_drives, _ = discover_devices()
            
            for drive_info in potential_drives:
                path = drive_info.path
                self._log_event("debug", f"Checking device {path} for barcode {barcode}")
                
                detected_barcode = self._get_drive_barcode(path)
                if detected_barcode == barcode:
                    self._log_event("success", f"Matched mtx drive {mtx_drive_index} to logical device {path}")
                    return path
            
            self._log_event("warning", f"Failed to identify logical device for mtx drive {mtx_drive_index}")
            return None
            
        except Exception as e:
            self._log_event("error", f"Identification failed for mtx drive {mtx_drive_index}: {e}")
            raise CalibrationError(f"Drive identification failed: {e}")

    def recover_library(self, drive: int = 0, force_unload: bool = False) -> Dict[str, object]:
        """Attempt to recover from degraded/offline library state."""
        result: Dict[str, object] = {
            "force_unload_attempted": False,
            "force_unload_ok": None,
            "force_unload_error": None,
        }

        if force_unload:
            result["force_unload_attempted"] = True
            try:
                self.force_unload_tape(drive)
                result["force_unload_ok"] = True
            except Exception as exc:
                result["force_unload_ok"] = False
                result["force_unload_error"] = str(exc)

        self._probe_failures = 0
        self._probe_failure_timestamps.clear()
        self._auto_correct_attempted = False
        self.library_error = None
        self._last_probe = None
        self._last_mtx_check = None
        self._state_changed_at = 0

        online = self._check_library_status(force=True)
        result.update({
            "library_online": bool(online),
            "library_state": self.get_library_state(),
            "library_error": self.library_error,
        })
        return result
    
    def format_tape(self, barcode: str, force: bool = False, drive: int = 0, progress_callback=None) -> bool:
        """
        Format tape with LTFS
        
        Args:
            barcode: Tape barcode
            force: Force format even if tape appears formatted
        """
        if self.db and self.db.is_tape_locked(barcode):
            raise ComplianceError(f"Tape {barcode} is under WORM retention lock and cannot be formatted.")
            
        # Safety Check: Block cleaning tapes from format
        if barcode.startswith('CLN'):
            raise ValueError(f"Tape {barcode} is a cleaning tape and cannot be formatted.")
        if self.db:
            tape = self.db.get_tape(barcode)
            if tape and tape.get('is_cleaning_tape'):
                raise ValueError(f"Tape {barcode} is a registered cleaning tape and cannot be formatted.")
            
        success = False
        self._enter_busy()
        try:
            # Load tape if not already loaded
            if self._get_mounted_tape(drive) != barcode:
                self.load_tape(barcode, drive)
            
            print(f"Formatting tape {barcode} with LTFS...")
            
            tape_serial = self._derive_tape_serial(barcode)

            # Ensure the tape is unmounted before mkltfs (it fails with EBUSY otherwise)
            try:
                self.unmount_ltfs(drive=drive)
            except Exception as unmount_err:
                print(f"Warning: Failed to unmount prior to format: {unmount_err}")

            # Format with mkltfs
            device_path = self._get_device(drive)
            if self.drive_sg and os.path.exists(self.drive_sg):
                if self.changer and os.path.realpath(self.drive_sg) == os.path.realpath(self.changer):
                    print(
                        f"⚠ Drive sg device {self.drive_sg} resolves to changer {self.changer}; "
                        f"falling back to {device_path} for mkltfs."
                    )
                else:
                    device_path = self.drive_sg
                    
            cmd = ['mkltfs', '-d', device_path, '--tape-serial', tape_serial, '--volume-name', barcode]
            if force:
                cmd.append('--force')
            
            # Filter None values and ensure all elements are strings for subprocess.Popen
            cmd = [str(c) for c in cmd if c is not None]

            import subprocess
            import re
            import time
            
            process = None
            stdout_lines = []
            
            for attempt in range(6):
                logger.info(f"Starting mkltfs process (attempt {attempt+1}/6): {' '.join(cmd)}")
                print(f"DEBUG: Running mkltfs: {' '.join(cmd)}")
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )
                
                stdout_lines = []
                buf = ""
                while True:
                    char = process.stdout.read(1)
                    if not char:
                        if process.poll() is not None:
                            break
                        time.sleep(0.1)
                        continue # No character but process still running
                        
                    buf += char
                    if char in ('\n', '\r'):
                        line = buf.strip()
                        buf = ""
                        if not line:
                            continue
                        
                        stdout_lines.append(line)
                        if line:
                            logger.info(f"mkltfs output: {line}")
                            print(f"mkltfs: {line}")
                        
                        # Parse mkltfs progress. Usually: 
                        # [LTFS15063I] Formatting: Partition 0. (50%)
                        # OR: LTFS15063I Formatting: Partition 0. (50%)
                        if progress_callback:
                            # Discrete marker detection for environments without percentages
                            if "Partitioning the medium" in line:
                                progress_callback(5, f"Formatting {barcode}: Partitioning...")
                            elif "Writing label to partition b" in line:
                                progress_callback(10, f"Formatting {barcode}: Writing label (Part B)...")
                            elif "Writing index to partition b" in line:
                                progress_callback(15, f"Formatting {barcode}: Writing index (Part B)...")
                            elif "Writing label to partition a" in line:
                                progress_callback(90, f"Formatting {barcode}: Writing label (Part A)...")
                            elif "Writing index to partition a" in line:
                                progress_callback(95, f"Formatting {barcode}: Writing index (Part A)...")
                            elif "Medium formatted successfully" in line:
                                progress_callback(100, f"Formatting {barcode}: Complete")

                            # More robust regex to handle various mkltfs output formats
                            # Look for 'Partition' followed by a number, and later a percentage
                            match = re.search(r'Partition\s+(\d+):\s+(\d+)%', line)
                            if match:
                                part = int(match.group(1))
                                pct = int(match.group(2))
                                
                                overall_pct = 0
                                if part == 0:
                                    overall_pct = int(pct * 0.15)
                                elif part == 1:
                                    overall_pct = 15 + int(pct * 0.85)
                                    
                                logger.info(f"Parsed mkltfs progress: Part {part}, {pct}% -> Overall {overall_pct}%")
                                progress_callback(overall_pct, f"Formatting {barcode}: {overall_pct}% (Partition {part} {pct}%)")
                
                returncode = process.wait()
                logger.info(f"mkltfs process exited with returncode {returncode}")
                print(f"DEBUG: mkltfs exited with {returncode}")
                
                if process.returncode != 0:
                    err_msg = " ".join(stdout_lines).lower()
                    if "busy" in err_msg or "cannot open device" in err_msg or "-21711" in err_msg:
                        print(f"Warning: mkltfs returned EBUSY (attempt {attempt+1}/6). Waiting 15s for FUSE/hardware to release...")
                        time.sleep(15)
                        try:
                            self.unmount_ltfs(drive=drive)
                        except Exception:
                            pass
                        continue
                    else:
                        raise TapeFormatError(f"mkltfs failed: {' '.join(stdout_lines)}")
                else:
                    break
                    
            if process and process.returncode != 0:
                raise TapeFormatError(f"mkltfs failed after retries: {' '.join(stdout_lines)}")

            verification = self.verify_ltfs(barcode=barcode, drive=drive)
            if not verification.get("ok"):
                raise TapeFormatError(f"LTFS verification failed: {verification.get('error')}")
            if verification.get("warning"):
                warning = verification.get("warning")
                print(f"⚠ {warning}")
                self._log_event("warning", warning, {"drive": drive, "barcode": barcode})
            
            self._log_event('info', f'Tape {barcode} formatted with LTFS successfully', {'barcode': barcode})

            success = True
            return True
        except Exception as e:
            self._log_event('error', f"Failed to format tape {barcode}: {e}")
            raise TapeFormatError(f"Failed to format tape {barcode}: {e}")
        finally:
            self._exit_busy(success=success)

    def wipe_tape(self, barcode: str, drive: int = 0, mode: str = 'quick') -> bool:
        """
        Wipe/erase tape contents.
        
        Args:
            barcode: Tape barcode to wipe
            drive: Drive index to use
            mode: Erase mode - 'quick' (rewind+weof, ~5s), 'format' (mkltfs, ~30s), 
                  or 'secure' (mt erase full overwrite, 4-10 hours)
        """
        if mode not in ('quick', 'format', 'secure'):
            raise ValueError(f"Invalid erase mode '{mode}'. Must be 'quick', 'format', or 'secure'.")
            
        if self.db and self.db.is_tape_locked(barcode):
            raise ComplianceError(f"Tape {barcode} is under WORM retention lock and cannot be wiped.")
            
        # Safety Check: Block cleaning tapes from wipe
        if barcode.startswith('CLN'):
            raise ValueError(f"Tape {barcode} is a cleaning tape and cannot be wiped.")
        if self.db:
            tape = self.db.get_tape(barcode)
            if tape and tape.get('is_cleaning_tape'):
                raise ValueError(f"Tape {barcode} is a registered cleaning tape and cannot be wiped.")
            
        try:
            # Load tape if not already loaded
            if self._get_mounted_tape(drive) != barcode:
                self.load_tape(barcode, drive)
            
            if mode == 'quick':
                print(f"Quick erasing tape {barcode} (EOD marker at BOT)...")
                self.run_mt_command(["rewind"], timeout_key="mt_status")
                self.run_mt_command(["weof"], timeout_key="mt_status")
                self._log_event("info", f"Tape {barcode} quick-erased (EOD at BOT)", {"drive": drive, "barcode": barcode, "mode": "quick"})
                
            elif mode == 'format':
                print(f"Reformatting tape {barcode} with LTFS...")
                self.format_tape(barcode, force=True, drive=drive)
                self._log_event("info", f"Tape {barcode} reformatted (LTFS)", {"drive": drive, "barcode": barcode, "mode": "format"})
                
            elif mode == 'secure':
                print(f"Secure erasing tape {barcode} (full physical overwrite)...")
                # Execute erase command using the validated/parameterized runner
                # We use the "mt_erase" timeout key which defaults to 7200s (2 hours)
                result = self.run_mt_command(["erase"], timeout_key="mt_erase")
                if result.returncode != 0:
                     raise RuntimeError(f"Erase failed: {result.stderr or result.stdout}")
                self._log_event("info", f"Tape {barcode} secure-erased (full overwrite)", {"drive": drive, "barcode": barcode, "mode": "secure"})
            
            return True
        except Exception as e:
            print(f"Error wiping tape {barcode} (mode={mode}): {e}")
            raise

    def _derive_tape_serial(self, barcode: str) -> str:
        """Derive tape serial for mkltfs from barcode."""
        # ... (existing code matches)
        if not barcode:
            raise ValueError("Tape barcode is required to derive tape serial")
        normalized = barcode.strip().upper()
        if len(normalized) < 6:
            raise ValueError(f"Tape barcode must be 6 characters minimum, got {len(normalized)}")
        if len(normalized) == 6 and re.fullmatch(r'[A-Z0-9]{6}', normalized):
            return normalized
        if len(normalized) == 8 and re.fullmatch(r'[A-Z0-9]{8}', normalized):
            return normalized[:6]
        
        # Fallback for manual names: Hash to 6 chars (alphanumeric uppercase)
        allowed = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        import hashlib
        h = hashlib.md5(normalized.encode()).digest()
        serial = ""
        for i in range(6):
            serial += allowed[h[i] % len(allowed)]
        return serial

    def dump_tape(self, barcode: str, destination_path: str, drive: int = 0) -> bool:
        """
        Emergency dump of tape content to a directory using tar.
        Does NOT use LTFS - raw read.
        """
        try:
            # Load tape if not already loaded
            if self._get_mounted_tape(drive) != barcode:
                self.load_tape(barcode, drive)
            
            # Rewind
            self.run_mt_command(['rewind'])

            device_path = self._get_device(drive)
            # Use non-rewinding device if possible, but tar usually handles it
            
            if not os.path.exists(destination_path):
                os.makedirs(destination_path, exist_ok=True)

            print(f"Dumping tape {barcode} to {destination_path}...")
            
            # Use tar to extract all content
            # -b 128 = 64KB blocking factor, typical for LTO
            cmd = ['tar', '-xvf', device_path, '-C', destination_path, '-b', '128']
            
            # This is a blocking call that might take hours
            # output will be captured in logs
            self.command_runner.run(cmd, name=f"dump_{barcode}", timeout=86400) # 24h timeout
            
            print(f"✓ Tape {barcode} dumped successfully")
            return True
            
        except Exception as e:
            raise HardwareError(f"Failed to dump tape {barcode}: {e}")
            
    def mount_ltfs(self, barcode: str = None, drive: int = 0) -> str:
        """
        Mount tape with LTFS
        
        Args:
            barcode: Tape barcode (loads if not already loaded)
        
        Returns:
            Mount point path
        """
        try:
            # Load tape if specified and not loaded
            mounted = self._get_mounted_tape(drive)
            if barcode and mounted != barcode:
                self.load_tape(barcode, drive)
                mounted = self._get_mounted_tape(drive)
            
            if not mounted:
                raise TapeError("No tape loaded in drive")
            
            # Create mount point if needed
            mount_point = self._get_mount_point(drive)
            
            try:
                # First try to cleanly make dirs
                os.makedirs(mount_point, exist_ok=True)
            except OSError as e:
                # Catch Errno 17 (File exists) caused by broken mount
                # or Errno 107 (Transport endpoint is not connected)
                if e.errno in (17, 107) or "Transport endpoint" in str(e):
                    print(f"Detected possible stale FUSE mount at {mount_point}, forcing umount...")
                    self.command_runner.run(['sudo', 'umount', '-f', mount_point], name="umount_stale")
                    time.sleep(1)
                    os.makedirs(mount_point, exist_ok=True)
                else:
                    raise
            
            # Check if already mounted
            if self._is_ltfs_mounted(drive=drive, mount_point=mount_point):
                return mount_point

            if not self._find_fusermount():
                raise TapeMountError("LTFS mount unavailable (fusermount not present).")
            
            print(f"Mounting tape {mounted} with LTFS at {mount_point}")
            
            # Mount with ltfs
            device_path = self._get_device(drive)
            # Use drive_sg for IBM LTFS if available
            if self.drive_sg and os.path.exists(self.drive_sg):
                if not (self.changer and os.path.realpath(self.drive_sg) == os.path.realpath(self.changer)):
                    device_path = self.drive_sg

            cmd = [
                'ltfs',
                mount_point,
                '-o',
                'devname=' + device_path
            ]
            
            result = self.command_runner.run(cmd, name="ltfs")

            if result.returncode != 0:
                raise TapeMountError(f"ltfs mount failed: {result.stderr or result.stdout}")
            
            # Wait for mount to be ready
            time.sleep(2)
            
            print(f"✓ Tape mounted at {mount_point}")
            return mount_point
            
        except Exception as e:
            raise TapeMountError(f"Failed to mount LTFS: {e}")
    
    def unmount_ltfs(self, mount_point: Optional[str] = None, drive: int = 0) -> bool:
        """Unmount LTFS"""
        try:
            if mount_point is None:
                mount_point = self._get_mount_point(drive)

            if not self._is_ltfs_mounted(drive=drive, mount_point=mount_point):
                return True
            
            print(f"Unmounting LTFS from {mount_point}")
            
            result = self.command_runner.run(
                ['sudo', 'umount', mount_point],
                timeout=60,
                name="umount",
                allow_retry=True
            )
            
            if result.returncode != 0:
                err_msg = (result.stderr or result.stdout or "").lower()
                if "not mounted" in err_msg:
                    print(f"✓ LTFS already unmounted: {err_msg}")
                    return True
                    
                # Try forced unmount
                forced_result = self.command_runner.run(
                    ['sudo', 'umount', '-f', mount_point],
                    timeout=60,
                    name="umount_force",
                    allow_retry=True
                )
                if forced_result.returncode != 0:
                    f_err_msg = (forced_result.stderr or forced_result.stdout or "").lower()
                    if "not mounted" in f_err_msg:
                        print(f"✓ LTFS already unmounted: {f_err_msg}")
                        return True
                    raise Exception(
                        f"Forced unmount failed: {forced_result.stderr or result.stderr}"
                    )
            
            print("✓ LTFS OS unmount completed")
            
            # CRITICAL FIX: The OS umount unmaps the filesystem almost immediately,
            # but the physical tape drive continues writing the LTFS index blocks to tape
            # for up to 60+ seconds. We MUST wait for the drive to truly finish before
            # returning, otherwise subsequent `mtx unload` commands will crash with "Device busy".
            logger.info("Waiting for LTFS index flush to complete on hardware...")
            import time
            device_sg = self._get_device_sg(drive)
            if device_sg and os.path.exists(device_sg):
                wait_start = time.time()
                timeout = 180  # Max 3 minutes to flush index
                
                while time.time() - wait_start < timeout:
                    # check if the device is responsive to sg_logs
                    prob_res = self.command_runner.run(
                        ['sudo', 'sg_logs', '--page=0x38', self._get_device(drive)],
                        timeout=10,
                        name="sg_logs_probe",
                        allow_retry=False
                    )
                    
                    if prob_res.returncode == 0:
                        logger.info(f"Hardware index flush complete after {int(time.time() - wait_start)}s.")
                        break
                    
                    err = (prob_res.stderr or prob_res.stdout or "").lower()
                    if "busy" in err or "device or resource busy" in err:
                        time.sleep(2)  # Still writing index, wait
                    elif prob_res.returncode == 127:
                        # sg_logs missing, try mt status as fallback
                        mt_res = self.command_runner.run(
                            ['sudo', 'mt', '-f', self._get_device(drive), 'status'],
                            timeout=10,
                            name="mt_status_probe",
                            allow_retry=False
                        )
                        if mt_res.returncode == 0:
                            # If mt works but doesn't report busy, we might be safe or mt just isn't detailed enough.
                            # We'll wait at least 30s total if we can't get a definitive 'busy' signal.
                            if time.time() - wait_start > 30:
                                time.sleep(5) # Add a small sleep to avoid thrashing
                            else:
                                time.sleep(5)
                        else:
                            # Both failed, fallback to simple sleep if we haven't waited long enough
                            if time.time() - wait_start < 45:
                                time.sleep(5)
                            else:
                                break
                    else:
                        # Some other error happened, maybe the device node vanished, safe to break
                        break
                            
            print("✓ LTFS unmount fully completed")
            return True
            
        except Exception as e:
            raise TapeUnmountError(f"Failed to unmount LTFS: {e}")
    
    def get_tape_info(self, barcode: str = None, drive: int = 0) -> Dict:
        """
        Get detailed information about a tape
        
        Args:
            barcode: Tape barcode (uses loaded tape if not specified)
        """
        try:
            mounted = self._get_mounted_tape(drive)
            if barcode and mounted != barcode:
                self.load_tape(barcode, drive)
                mounted = self._get_mounted_tape(drive)
            
            if not mounted:
                raise TapeError("No tape loaded")
            
            # Mount tape to get info
            if not self._is_ltfs_mounted(drive=drive):
                self.mount_ltfs(drive=drive)
            
            # Get capacity info using ltfs
            device_path = self._get_device(drive)
            result = self.command_runner.run(
                ['ltfs', '-o', 'devname=' + device_path, '--get-capacity'],
                name="ltfs_get_capacity",
                allow_retry=True,
                lock=self._changer_lock
            )
            
            # Parse output for capacity information
            info = {
                'barcode': mounted,
                'capacity': self._parse_capacity(result.stdout),
                'used': self._get_used_space(drive=drive),
                'generation': self._parse_generation(mounted),
                'status': 'loaded'
            }
            
            return info
            
        except Exception as e:
            return {
                'barcode': barcode or mounted,
                'error': str(e)
            }

    def collect_ltfs_metadata(self, barcode: str = None, drive: int = 0, leave_mounted: bool = False) -> Dict[str, object]:
        """
        Collect LTFS capacity and usage metadata for a tape.
        """
        mounted = None
        mounted_here = False
        try:
            mounted = self._get_mounted_tape(drive)
            if barcode and mounted != barcode:
                self.load_tape(barcode, drive)
                mounted = self._get_mounted_tape(drive)

            if not mounted:
                raise TapeError("No tape loaded")

            if not self._is_ltfs_mounted(drive=drive):
                self.mount_ltfs(drive=drive)
                mounted_here = True

            mount_point = self._get_mount_point(drive)
            stats = os.statvfs(mount_point)
            capacity_bytes = stats.f_frsize * stats.f_blocks
            free_bytes = stats.f_frsize * stats.f_bavail
            used_bytes = max(0, capacity_bytes - free_bytes)
            return {
                "barcode": mounted,
                "capacity_bytes": capacity_bytes,
                "used_bytes": used_bytes,
                "free_bytes": free_bytes,
                "volume_name": mounted,
                "ltfs_formatted": True,
            }
        finally:
            if mounted_here and not leave_mounted:
                try:
                    self.unmount_ltfs(drive=drive)
                except Exception:
                    pass

    def verify_ltfs(self, barcode: str = None, drive: int = 0) -> Dict[str, object]:
        """Verify LTFS formatting and return metadata when possible."""
        mounted_here = False
        try:
            mounted = self._get_mounted_tape(drive)
            if barcode and mounted != barcode:
                self.load_tape(barcode, drive)
                mounted = self._get_mounted_tape(drive)
            if not mounted:
                raise TapeError("No tape loaded")

            device_path = self._get_device(drive)
            ltfsck_device, ltfsck_warning = self._get_ltfsck_device(drive)
            ltfsck_path = shutil.which("ltfsck")
            fusermount_path = self._find_fusermount()
            warning_messages = []
            if ltfsck_warning:
                warning_messages.append(ltfsck_warning)
            if ltfsck_path:
                result = self.command_runner.run([ltfsck_path, ltfsck_device], name="ltfsck")
                if result.returncode != 0:
                    if not fusermount_path:
                        return {
                            "ok": False,
                            "error": result.stderr or result.stdout or "ltfsck failed",
                        }
                    warning_messages.append(result.stderr or result.stdout or "ltfsck failed")

            if not self._is_ltfs_mounted(drive=drive):
                if not fusermount_path:
                    return {
                        "ok": True,
                        "warning": "LTFS mount verification unavailable (fusermount not present). Format may still succeed.",
                        "ltfs_formatted": True,
                    }
                self.mount_ltfs(drive=drive)
                mounted_here = True
            metadata = self.collect_ltfs_metadata(barcode=mounted, drive=drive, leave_mounted=True)
            metadata["ok"] = True
            if warning_messages:
                metadata["warning"] = "; ".join(warning_messages)
            return metadata
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            if mounted_here:
                try:
                    self.unmount_ltfs(drive=drive)
                except Exception:
                    pass

    def _find_fusermount(self) -> Optional[str]:
        """Return path to fusermount binary if available."""
        return shutil.which("fusermount") or shutil.which("fusermount3")

    def build_unload_command(self, drive: int, slot: int) -> List[str]:
        if self._mtx_unload_order == "drive_slot":
            return ["unload", str(drive), str(slot)]
        return ["unload", str(slot), str(drive)]

    def _build_unload_command(self, drive: int, slot: int) -> List[str]:
        """Backward-compatible alias for build_unload_command."""
        return self.build_unload_command(drive, slot)

    def _get_ltfsck_device(self, drive: int = 0) -> Tuple[str, Optional[str]]:
        device_path = self._get_device(drive)
        if not self.drive_sg or not os.path.exists(self.drive_sg):
            return device_path, None
        if self.changer and os.path.realpath(self.drive_sg) == os.path.realpath(self.changer):
            warning = (
                f"Drive sg device {self.drive_sg} resolves to changer {self.changer}; "
                f"falling back to {device_path} for ltfsck."
            )
            return device_path, warning
        return self.drive_sg, None
    
    def verify_tape(self, barcode: str = None, drive: int = 0) -> bool:
        """
        Verify tape integrity
        
        Args:
            barcode: Tape barcode (uses loaded tape if not specified)
        """
        try:
            mounted = self._get_mounted_tape(drive)
            if barcode and mounted != barcode:
                self.load_tape(barcode, drive)
                mounted = self._get_mounted_tape(drive)
            
            if not mounted:
                raise TapeError("No tape loaded")
            
            print(f"Verifying tape {mounted}...")
            
            # Mount and check filesystem
            mount_point = self.mount_ltfs(drive=drive)
            
            # Read index to verify
            device_path = self._get_device(drive)
            result = self.command_runner.run(['ltfsck', device_path], name="ltfsck")

            if result.returncode != 0:
                raise Exception(f"Verification failed: {result.stderr or result.stdout}")
            
            print(f"✓ Tape {mounted} verified successfully")
            return True
            
        except Exception as e:
            raise TapeError(f"Tape verification failed: {e}")
    
    def _wait_for_hardware_cooldown(self, operation: str):
        """Implement cooldown for physical hardware operations to prevent thrashing"""
        if operation in ("status", "inquiry"):
            return
            
        now = time.time()
        elapsed = now - self._last_hardware_op_time
        if elapsed < self._hardware_op_cooldown:
            wait_time = self._hardware_op_cooldown - elapsed
            time.sleep(wait_time)
            
        self._last_hardware_op_time = time.time()

    def _run_mtx_command(self, command):
        """Run mtx command for library operations"""
        self._ensure_valid_changer()
        if not self.changer:
            raise RuntimeError("No medium changer configured; mtx command unavailable")
        mtx_path = self._resolve_mtx_path()
        cmd = [mtx_path, "-f", self.changer]
        
        # Validation Layer
        from backend.utils.validation import validate_slot, validate_drive
        import re
        if isinstance(command, (list, tuple)):
            op = str(command[0]) if command else ""
            for i, part in enumerate(command):
                # Basic sanity: no shell meta-characters
                if isinstance(part, str) and re.search(r'[;&|`$<>(){}\[\]\\]', part):
                     raise ValueError(f"Dangerous character in mtx argument: {part}")
                
                # Semantic validation
                if i > 0 and op in ('load', 'unload', 'transfer'):
                    # All positional arguments for these must be integers (slot or drive)
                    ok, err = validate_slot(part)
                    if not ok:
                        ok, err = validate_drive(part)
                        if not ok:
                            raise ValueError(err)
                        
            cmd.extend([str(part) for part in command])
        else:
            part_str = str(command)
            if re.search(r'[;&|`$<>(){}\[\]\\]', part_str):
                 raise ValueError(f"Dangerous character in mtx argument: {part_str}")
            cmd.append(part_str)
            op = part_str
            
        # Enforce hardware cooldown for physical operations
        self._wait_for_hardware_cooldown(op)
        
        timeout_key = {
            "status": "mtx_status",
            "inquiry": "mtx_inquiry",
            "load": "mtx_load",
            "unload": "mtx_unload",
            "transfer": "mtx_transfer",
            "inventory": "mtx_inventory",
        }.get(op, "mtx_status")

        allow_retry = op in ("status", "inquiry", "inventory", "load", "unload", "transfer")
        
        # Cache for status commands
        now = time.time()
        if op == "status" and self._mtx_status_cache and (now - self._last_mtx_status_at < self._status_cache_ttl):
            return self._mtx_status_cache

        result = self.command_runner.run(cmd, name=timeout_key, allow_retry=allow_retry, lock=self._changer_lock)
        
        if op == "status" and result.returncode == 0:
            self._mtx_status_cache = result
            self._last_mtx_status_at = now
            
        return result
        if result.timed_out:
            self.library_error = f"mtx {op} timed out after {self.command_runner.timeouts.get(timeout_key)}s"
            self._set_base_state(LIBRARY_DEGRADED, error=self.library_error)
            return result

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            message = f"mtx failed rc={result.returncode}"
            if detail:
                message = f"{message}: {detail}"
            if "permission denied" in detail.lower():
                message = f"permission denied opening {self.changer}; check udev rules/group"
            if "read element status" in detail.lower() or "illegal request" in detail.lower():
                if not self._auto_correct_attempted and self._attempt_changer_autocorrect(detail):
                    self._auto_correct_attempted = True
                    return self._run_mtx_command(command)
                message = (
                    f"{message}. This usually means you pointed mtx at a tape drive "
                    f"sg device (e.g. /dev/sg1) instead of the medium changer (e.g. /dev/sg2)."
                )
            
            # Record error in service state
            self.library_error = message
            self._set_base_state(LIBRARY_DEGRADED, error=message)
        
        return result
    def _run_mt_command(self, op: str, drive: int = 0) -> CommandResult:
        """Run mt command for drive operations"""
        device = self._get_device(drive)
        # Validation
        from backend.utils.validation import validate_drive
        ok, err = validate_drive(drive)
        if not ok:
             raise ValueError(err)
             
        # Only allow specific safe mt operations
        if op not in ('status', 'rewind', 'offline', 'weof', 'erase', 'retension'):
             raise ValueError(f"Unauthorized mt operation: {op}")
             
        cmd = ['mt', '-f', device, op]
        
        # Cache for mt status
        now = time.time()
        if op == "status" and drive in self._mt_status_cache:
            last_ts, last_res = self._mt_status_cache[drive]
            if now - last_ts < self._status_cache_ttl:
                return last_res

        result = self.command_runner.run(cmd, name="mt_status" if op == "status" else f"mt_{op}", allow_retry=(op == "status"))
        
        if op == "status" and result.returncode == 0:
            self._mt_status_cache[drive] = (now, result)
            
        return result

    def _check_library_status(self, force: bool = False) -> bool:
        """Run a throttled mtx status check to avoid tight-loop polling."""
        if self.drive_only_mode:
            self._set_base_state(LIBRARY_ONLINE if os.path.exists(self.device) else LIBRARY_OFFLINE)
            return self.library_online

        now = time.time()
        if not force and self._last_mtx_check is not None:
            if now - self._last_mtx_check < self._mtx_check_interval:
                return bool(self.library_online)

        self._last_mtx_check = now
        try:
            result = self._run_mtx_command(['status'])
            if result.timed_out:
                self._record_probe_failure(f"mtx status timed out after {result.duration:.1f}s")
                return self.library_online
            self._record_probe_success()
            return True
        except Exception:
            self._record_probe_failure("mtx status failed")
            return self.library_online

    def _resolve_mtx_path(self) -> str:
        """Resolve the mtx binary, preferring PATH and falling back to /usr/sbin."""
        mtx_path = shutil.which("mtx") or "/usr/sbin/mtx"
        if os.path.exists(mtx_path):
            return mtx_path
        raise FileNotFoundError("mtx not found; install package 'mtx' (binary at /usr/sbin/mtx).")

    def run_mt_command(self, command: List[str], timeout_key: str = "mt_status"):
        """Run mt command for tape drive operations."""
        mt_path = shutil.which("mt") or "/bin/mt"
        cmd = [mt_path, "-f", self.device] + [str(part) for part in command]
        allow_retry = timeout_key == "mt_status"
        result = self.command_runner.run(cmd, name=timeout_key, allow_retry=allow_retry, lock=self._changer_lock)
        if result.timed_out:
            self.library_error = f"mt {' '.join(command)} timed out after {self.command_runner.timeouts.get(timeout_key)}s"
            self._set_base_state(LIBRARY_DEGRADED, error=self.library_error)
        elif result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            message = f"mt failed rc={result.returncode}"
            if detail:
                message = f"{message}: {detail}"
            self.library_error = message
            self._set_base_state(LIBRARY_DEGRADED, error=message)
            raise RuntimeError(message)
        return result

    def run_mt_command_interruptible(
        self,
        command: List[str],
        timeout_key: str = "mt_status",
        cancel_check=None,
        poll_interval: float = 1.0,
    ) -> CommandResult:
        """Run mt command with cancellation support."""
        mt_path = shutil.which("mt") or "/bin/mt"
        cmd = [mt_path, "-f", self.device] + [str(part) for part in command]
        timeout_value = self.command_runner.timeouts.get(timeout_key)
        start = time.time()
        start_iso = datetime.utcfromtimestamp(start).isoformat() + "Z"
        stdout = ""
        stderr = ""
        returncode = 0
        timed_out = False
        error_type = None
        error_message = None

        # Acquire the lock to start the process
        with self._changer_lock:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        try:
            while True:
                if cancel_check and cancel_check():
                    with self._changer_lock:
                        process.terminate()
                        try:
                            stdout, stderr = process.communicate(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            stdout, stderr = process.communicate()
                    returncode = 130
                    error_type = "cancelled"
                    error_message = "Command cancelled"
                    break

                if timeout_value and time.time() - start > timeout_value:
                    timed_out = True
                    with self._changer_lock:
                        process.terminate()
                        try:
                            stdout, stderr = process.communicate(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            stdout, stderr = process.communicate()
                    returncode = 124
                    break

                # We don't strictly need the lock for poll() as it's just checking the process state
                returncode = process.poll()
                if returncode is not None:
                    # But we DO need it for communicate() as it reads from the pipes which were opened under the lock
                    with self._changer_lock:
                        stdout, stderr = process.communicate()
                    break
                
                time.sleep(poll_interval)
        finally:
                end = time.time()
                duration = end - start
                end_iso = datetime.utcfromtimestamp(end).isoformat() + "Z"
                if error_type is None:
                    error_type, error_message = self.command_runner.classify_error(
                        stdout, stderr, returncode, timed_out
                    )
                entry = {
                    "command": cmd,
                    "name": timeout_key,
                    "start_time": start_iso,
                    "end_time": end_iso,
                    "timeout": timeout_value,
                    "duration": duration,
                    "returncode": returncode,
                    "timed_out": timed_out,
                    "stdout": (stdout or "")[-4000:],
                    "stderr": (stderr or "")[-4000:],
                    "error_type": error_type,
                    "attempt": 1,
                }
                self.command_runner.history.add(entry)
                self.command_runner._emit_log(entry)

        if timed_out:
            self.library_error = f"mt {' '.join(command)} timed out after {timeout_value}s"
            self._set_base_state(LIBRARY_DEGRADED, error=self.library_error)
        elif returncode != 0 and error_type != "cancelled":
            detail = (stderr or stdout or "").strip()
            message = f"mt failed rc={returncode}"
            if detail:
                message = f"{message}: {detail}"
            self.library_error = message
            self._set_base_state(LIBRARY_DEGRADED, error=message)

        return CommandResult(
            command=cmd,
            stdout=stdout or "",
            stderr=stderr or "",
            returncode=returncode,
            duration=duration,
            timed_out=timed_out,
            error_type=error_type,
            error_message=error_message,
        )

    def _enter_busy(self) -> None:
        self._busy_operations += 1

    def _exit_busy(self, success: bool, error: Optional[str] = None) -> None:
        self._busy_operations = max(0, self._busy_operations - 1)
        if success:
            self._set_base_state(LIBRARY_ONLINE)
        else:
            self._set_base_state(LIBRARY_DEGRADED, error=error)

    def _set_base_state(self, state: str, error: Optional[str] = None) -> None:
        self._base_library_state = state
        self.library_online = state != LIBRARY_OFFLINE
        if error:
            self.library_error = error
        self._update_debounced_state()

    def _update_debounced_state(self) -> None:
        now = time.time()
        if self._debounced_state == self._base_library_state:
            return
        if now - self._state_changed_at < self._state_debounce_seconds:
            return
        self._debounced_state = self._base_library_state
        self._state_changed_at = now

    def _record_probe_success(self) -> None:
        self._probe_failures = 0
        self._probe_failure_timestamps.clear()
        self._set_base_state(LIBRARY_ONLINE)
        self._last_probe = {"status": "ok", "timestamp": time.time(), "error": None}

    def _record_probe_failure(self, error: str) -> None:
        self._probe_failures += 1
        now = time.time()
        self._probe_failure_timestamps.append(now)
        while self._probe_failure_timestamps and now - self._probe_failure_timestamps[0] > self._offline_window_seconds:
            self._probe_failure_timestamps.popleft()
        if len(self._probe_failure_timestamps) >= self._offline_threshold:
            self._set_base_state(LIBRARY_OFFLINE, error=error)
        else:
            self._set_base_state(LIBRARY_DEGRADED, error=error)
        self._last_probe = {"status": "error", "timestamp": now, "error": error}

    def _confirm_drive_loaded(self, barcode: str, drive: int) -> bool:
        try:
            status = self.get_drive_status()
            return status.get('loaded_tape') == barcode
        except Exception:
            return False

    def _confirm_drive_unloaded(self, drive: int) -> bool:
        try:
            status = self.get_drive_status()
            return not status.get('loaded_tape')
        except Exception:
            return False

    def _attempt_changer_autocorrect(self, detail: str) -> bool:
        devices, health = get_devices(self._config, load_state())
        new_changer = devices.get("changer_sg")
        if new_changer and new_changer != self.changer:
            print(f"⚠ Detected changer mismatch ({self.changer}); switching to {new_changer}.")
            self.changer = new_changer
            state = load_state()
            tape_state = state.get("tape") if isinstance(state.get("tape"), dict) else {}
            tape_state["changer_device"] = new_changer
            update_state({"tape": tape_state})
            return True
        if health.get("warnings"):
            print(f"⚠ Tape device warning: {'; '.join(health['warnings'])}")
        return False

    def _ensure_valid_changer(self) -> None:
        if not self.changer:
            return
        if is_medium_changer_device(self.changer):
            return
        _drives, changers = discover_devices()
        if changers:
            new_changer = changers[0].path
            if new_changer and new_changer != self.changer:
                print(f"⚠ Configured changer {self.changer} is not a medium changer; switching to {new_changer}.")
                self.changer = new_changer
                state = load_state()
                tape_state = state.get("tape") if isinstance(state.get("tape"), dict) else {}
                tape_state["changer_device"] = new_changer
                update_state({"tape": tape_state})

    def _resolve_devices(self) -> None:
        devices, health = get_devices(self._config, self._state)
        if devices.get("drive_nst") and os.path.exists(devices["drive_nst"]):
            self.device = devices["drive_nst"]
            if 0 in self.drive_devices:
                self.drive_devices[0] = self.device
        if devices.get("changer_sg"):
            self.changer = devices["changer_sg"]
        self.drive_sg = devices.get("drive_sg")
        if self.drive_sg and self.changer:
            try:
                if os.path.realpath(self.drive_sg) == os.path.realpath(self.changer):
                    warning = (
                        f"Drive sg device {self.drive_sg} resolves to changer {self.changer}. "
                        "Ensure changer points to the medium changer sg device."
                    )
                    print(f"⚠ {warning}")
                    self.library_error = warning
            except Exception:
                pass
        if health.get("warnings"):
            print(f"⚠ Tape device warnings: {'; '.join(health['warnings'])}")
    
    def _parse_mtx_status(self, output: str) -> List[Dict]:
        """Parse mtx status output to get tape inventory."""
        tapes: List[Dict] = []

        slot_pattern = re.compile(
            r'Storage Element (\d+):(Full|Empty)(?:\s*:?VolumeTag\s*=\s*(\w+))?',
            re.IGNORECASE,
        )
        drive_pattern = re.compile(
            r'Data Transfer Element (\d+):(Full|Empty)'
            r'(?:\s*\(Storage Element (\d+) Loaded\))?'
            r'(?:\s*:?VolumeTag\s*=\s*(\w+))?',
            re.IGNORECASE,
        )
        mailslot_pattern = re.compile(
            r'(?:(?:Import/Export|Mail) Element (\d+)|Storage Element (\d+) IMPORT/EXPORT)\s*:(Full|Empty)(?:\s*:?VolumeTag\s*=\s*(\w+))?',
            re.IGNORECASE,
        )

        # Fetch mail slot configurations (default: auto-detect on)
        prefs = getattr(self, '_config', {}).get('preferences', {})
        ms_enabled = str(prefs.get('mail_slot_enabled', 'true')).lower() == 'true'
        ms_auto = str(prefs.get('mail_slot_auto_detect', 'true')).lower() == 'true'
        
        try:
            ms_manual = int(prefs.get('mail_slot_manual_index', 0))
        except (ValueError, TypeError):
            ms_manual = 0

        for line in output.split('\n'):
            slot_match = slot_pattern.search(line)
            if slot_match:
                slot_num = int(slot_match.group(1))
                is_full = slot_match.group(2) == 'Full'
                barcode = slot_match.group(3) if slot_match.group(3) else None

                # Determine if this slot should be treated as a mail slot via manual override
                is_mail_slot_override = ms_enabled and not ms_auto and ms_manual > 0 and slot_num == ms_manual

                if is_full:
                    alias = None if barcode else f"Slot {slot_num} (no barcode)"
                    is_cleaning = bool(barcode) and barcode.upper().endswith(('CU', 'CLN', 'CLEAN'))
                    has_barcode = bool(barcode)

                    location_type = 'mailslot' if is_mail_slot_override else 'slot'
                    location_name = f"{location_type.capitalize()} {slot_num}"

                    tapes.append({
                        'slot': slot_num,
                        'slot_index': slot_num if location_type == 'slot' else None,
                        'barcode': barcode,
                        'status': 'available',
                        'location': location_name,
                        'location_type': location_type,
                        'has_barcode': has_barcode,
                        'is_placeholder': not has_barcode,
                        'generation': self._parse_generation(barcode) if barcode else 'Unknown',
                        'type': 'cleaning' if is_cleaning else 'data',
                        'is_cleaning_tape': is_cleaning,
                        'alias': alias,
                    })
                else:
                    # Track empty slots too
                    location_type = 'mailslot' if is_mail_slot_override else 'slot'
                    location_name = f"{location_type.capitalize()} {slot_num}"
                    tapes.append({
                        'slot': slot_num,
                        'slot_index': slot_num if location_type == 'slot' else None,
                        'barcode': None,
                        'status': 'empty',
                        'location': location_name,
                        'location_type': location_type,
                        'has_barcode': False,
                        'is_placeholder': True,
                    })
                continue

            drive_match = drive_pattern.search(line)
            if drive_match:
                drive_num = int(drive_match.group(1))
                is_full = drive_match.group(2) == 'Full'
                source_slot = int(drive_match.group(3)) if drive_match.group(3) else None
                barcode = drive_match.group(4) if drive_match.group(4) else None

                if is_full:
                    alias = None if barcode else f"Drive {drive_num} (no barcode)"
                    is_cleaning = bool(barcode) and barcode.upper().endswith(('CU', 'CLN', 'CLEAN'))
                    has_barcode = bool(barcode)

                    tapes.append({
                        'slot': None,
                        'barcode': barcode,
                        'status': 'loaded',
                        'location': f'Drive {drive_num}',
                        'location_type': 'drive',
                        'has_barcode': has_barcode,
                        'is_placeholder': not has_barcode,
                        'generation': self._parse_generation(barcode) if barcode else 'Unknown',
                        'type': 'cleaning' if is_cleaning else 'data',
                        'is_cleaning_tape': is_cleaning,
                        'alias': alias,
                        'drive_index': drive_num,
                        'drive_full': True,
                        'drive_barcode': barcode,
                        'drive_source_slot': source_slot,
                    })
                else:
                    # Track empty drives so UI can show them as drop targets
                    tapes.append({
                        'slot': None,
                        'barcode': None,
                        'status': 'empty',
                        'location': f'Drive {drive_num}',
                        'location_type': 'drive',
                        'has_barcode': False,
                        'is_placeholder': True,
                        'drive_index': drive_num,
                        'drive_full': False,
                    })
                continue
            
            mailslot_match = mailslot_pattern.search(line)
            if mailslot_match:
                # Capture slot number from either group 1 (Import/Export Element) or group 2 (Storage Element XX IMPORT/EXPORT)
                slot_num = int(mailslot_match.group(1) or mailslot_match.group(2))
                is_full = mailslot_match.group(3) == 'Full'
                barcode = mailslot_match.group(4) if mailslot_match.group(4) else None
                
                # Determine what to report this slot as based on auto-detect preference
                report_as = 'mailslot' if (ms_enabled and ms_auto) else 'slot'

                if is_full:
                    alias = None if barcode else f"{report_as.capitalize()} {slot_num} (no barcode)"
                    is_cleaning = bool(barcode) and barcode.upper().endswith(('CU', 'CLN', 'CLEAN'))
                    has_barcode = bool(barcode)

                    tapes.append({
                        'slot': slot_num,
                        'slot_index': slot_num if report_as == 'slot' else None,
                        'barcode': barcode,
                        'status': 'available',
                        'location': f'{report_as.capitalize()} {slot_num}',
                        'location_type': report_as,
                        'has_barcode': has_barcode,
                        'is_placeholder': not has_barcode,
                        'generation': self._parse_generation(barcode) if barcode else 'Unknown',
                        'type': 'cleaning' if is_cleaning else 'data',
                        'is_cleaning_tape': is_cleaning,
                        'alias': alias,
                    })
                else:
                    # Track empty mailslots too
                    tapes.append({
                        'slot': slot_num,
                        'slot_index': slot_num if report_as == 'slot' else None,
                        'barcode': None,
                        'status': 'empty',
                        'location': f'{report_as.capitalize()} {slot_num}',
                        'location_type': report_as,
                        'has_barcode': False,
                        'is_placeholder': True,
                    })
                continue

        return tapes
    
    def clean_drive(self, cleaning_tape_barcode: str = None, drive: int = 0) -> bool:
        """
        Clean tape drive using cleaning tape
        
        Args:
            cleaning_tape_barcode: Barcode of cleaning tape (optional, will find one if not specified)
            drive: Drive number to clean
        
        Returns:
            True if successful
        """
        try:
            # Find cleaning tape if not specified
            if not cleaning_tape_barcode:
                tapes = self.scan_barcodes()
                cleaning_tapes = [t for t in tapes if t.get('type') == 'cleaning' and t['status'] == 'available']
                
                if not cleaning_tapes:
                    raise TapeLoadError("No cleaning tape found in library")
                
                cleaning_tape_barcode = cleaning_tapes[0]['barcode']
            
            print(f"Cleaning drive {drive} with tape {cleaning_tape_barcode}...")
            
            # Load cleaning tape
            self.load_tape(cleaning_tape_barcode, drive)
            
            # Wait for cleaning to complete (typically 10-30 seconds)
            print("Cleaning in progress...")
            time.sleep(30)
            
            # Unload cleaning tape
            self.unload_tape(drive)
            
            if self.db:
                try:
                    self.db.decrement_cleaning_uses(cleaning_tape_barcode)
                except Exception as e:
                    print(f"⚠ Failed to decrement cleaning tape usage: {e}")
            
            print(f"✓ Drive {drive} cleaned successfully")
            
            print(f"✓ Drive {drive} cleaned successfully")
            return True
            
        except Exception as e:
            raise HardwareCommunicationError(f"Failed to clean drive: {e}")
    
    def _parse_generation(self, barcode: str) -> str:
        """Parse LTO generation from barcode (e.g., '000001L6' -> 'LTO-6')"""
        if barcode and len(barcode) >= 2:
            suffix = barcode[-2:]
            gen_map = {
                'L5': 'LTO-5', 'L6': 'LTO-6', 'L7': 'LTO-7',
                'M8': 'LTO-8', 'L8': 'LTO-8', 'L9': 'LTO-9',
                'LA': 'LTO-10'
            }
            return gen_map.get(suffix, 'Unknown')
        return 'Unknown'
    
    def _is_ltfs_mounted(self, drive: int = 0, mount_point: Optional[str] = None) -> bool:
        """Check if LTFS is currently mounted"""
        try:
            if mount_point is None:
                mount_point = self._get_mount_point(drive)
            result = self.command_runner.run(
                ['mountpoint', '-q', mount_point],
                timeout=5,
                name="mountpoint_probe",
                allow_retry=True
            )
            return result.returncode == 0
        except Exception:
            return False

    def is_ltfs_mounted(self, drive: int = 0, mount_point: Optional[str] = None) -> bool:
        """Public wrapper for LTFS mount check."""
        return self._is_ltfs_mounted(drive=drive, mount_point=mount_point)
    
    def _parse_capacity(self, output: str) -> str:
        """Parse capacity from ltfs output"""
        # Look for capacity in output
        match = re.search(r'capacity[:\s]+(\d+\.?\d*)\s*([KMGT]?B)', output, re.IGNORECASE)
        if match:
            return f"{match.group(1)} {match.group(2)}"
        # Return default based on loaded tape generation
        if self.mounted_tape:
            return self.get_capacity_for_generation(self._parse_generation(self.mounted_tape))
        return "Unknown"
    
    def get_capacity_for_generation(self, generation: str) -> str:
        """Get native capacity string for LTO generation"""
        capacities = {
            'LTO-5': '1.5 TB',
            'LTO-6': '2.5 TB',
            'LTO-7': '6.0 TB',
            'LTO-8': '12.0 TB',
            'LTO-9': '18.0 TB',
            'LTO-10': '36.0 TB',  # Expected
        }
        return capacities.get(generation, 'Unknown')
    
    def get_capacity_bytes_for_generation(self, generation: str) -> int:
        """Get native capacity in bytes for LTO generation"""
        capacities = {
            'LTO-5': 1500000000000,    # 1.5 TB
            'LTO-6': 2500000000000,    # 2.5 TB
            'LTO-7': 6000000000000,    # 6.0 TB
            'LTO-8': 12000000000000,   # 12.0 TB
            'LTO-9': 18000000000000,   # 18.0 TB
            'LTO-10': 36000000000000,  # 36.0 TB (expected)
        }
        return capacities.get(generation, 2500000000000)  # Default to LTO-6 if unknown
    
    def _get_used_space(self, drive: int = 0) -> str:
        """Get used space on mounted tape"""
        try:
            mount_point = self._get_mount_point(drive)
            if self._is_ltfs_mounted(drive=drive, mount_point=mount_point):
                result = subprocess.run(
                    ['df', '-h', mount_point],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                # Parse df output
                lines = result.stdout.strip().split('\n')
                if len(lines) > 1:
                    fields = lines[1].split()
                    if len(fields) >= 3:
                        return fields[2]  # Used space
            return "0 B"
        except Exception:
            return "0 B"

    # --- Hardware Encryption Support (LTO-4+) ---

    def get_encryption_status(self, drive: int = 0) -> Dict:
        """
        Query encryption status of the drive.
        Returns: { 'supported': bool, 'active': bool, 'method': str }
        """
        device = self._get_device(drive)
        status = {
            'supported': False,
            'active': False,
            'method': 'none',
            'tool': None
        }

        # 1. Try tapeinfo first (reliable for 'supported' check)
        try:
            result = self.command_runner.run(
                ['tapeinfo', '-f', device],
                name="tapeinfo",
                allow_retry=True,
                lock=self._changer_lock
            )
            if result.returncode == 0:
                if 'Encryption:' in result.stdout:
                    status['supported'] = 'supported' in result.stdout.lower()
                    status['active'] = 'active' in result.stdout.lower()
        except Exception:
            pass

        # 2. Try stenc for detailed status if available
        try:
            stenc_result = self.command_runner.run(
                ['stenc', '-f', device, '-s'],
                name="stenc_status",
                lock=self._changer_lock
            )
            if stenc_result.returncode == 0:
                status['tool'] = 'stenc'
                # stenc output parsing (example: "Encryption is enabled")
                if 'Encryption is enabled' in stenc_result.stdout:
                    status['active'] = True
                    status['method'] = 'AES-256'
                elif 'Encryption is disabled' in stenc_result.stdout:
                    status['active'] = False
        except Exception:
            pass

        return status

    def enable_hardware_encryption(self, drive: int = 0) -> bool:
        """
        Convenience method to enable hardware encryption.
        Fetches a key from the KMS and sets it on the drive.
        """
        try:
            from backend.kms_provider import KMSProvider
            kms = KMSProvider()
            key_hex = kms.get_active_key_hex() # New helper needed in KMSProvider
            if not key_hex:
                 self._log_event("error", f"No active encryption key found in KMS")
                 return False
            return self.set_hardware_encryption(key_hex, drive=drive)
        except Exception as e:
            self._log_event("error", f"Failed to enable hardware encryption: {e}")
            return False

    def set_hardware_encryption(self, key_hex: str, drive: int = 0) -> bool:
        """
        Enable hardware encryption on the drive with the provided hex key.
        Requires 'stenc' utility.
        """
        device = self._get_device(drive)
        
        # Validate hex key length (LTO-4+ requires 256-bit / 64 hex chars)
        if len(key_hex) != 64:
             raise ValueError("LTO hardware encryption requires a 256-bit key (64 hex characters)")

        self._log_event("info", f"Enabling hardware encryption on drive {drive}")
        
        try:
            # Use stenc to set the key
            # We pass the key via stdin to avoid exposing it in process list/logs
            result = self.command_runner.run(
                ['stenc', '-f', device, '-e', '-k', '-'],
                input_data=key_hex,
                name="stenc_enable",
                lock=self._changer_lock
            )
            
            if result.returncode == 0:
                self._log_event("info", f"Hardware encryption enabled on drive {drive}")
                return True
            else:
                error = result.stderr.strip() or f"stenc returned {result.returncode}"
                self._log_event("error", f"Failed to enable hardware encryption: {error}")
                return False
                
        except Exception as e:
            self._log_event("error", f"Exception during hardware encryption setup: {e}")
            return False

    def disable_hardware_encryption(self, drive: int = 0) -> bool:
        """Disable hardware encryption on the drive."""
        device = self._get_device(drive)
        self._log_event("info", f"Disabling hardware encryption on drive {drive}")
        
        try:
            result = self.command_runner.run(
                ['stenc', '-f', device, '-d'],
                name="stenc_disable",
                lock=self._changer_lock
            )
            if result.returncode == 0:
                self._log_event("info", f"Hardware encryption disabled on drive {drive}")
                return True
            return False
        except Exception as e:
            self._log_event("error", f"Failed to disable hardware encryption: {e}")
            return False
