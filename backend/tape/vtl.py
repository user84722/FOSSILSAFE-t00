
import os
import json
import shutil
import time
import logging
from typing import Dict, List, Optional
from datetime import datetime

from backend.tape_controller import TapeLibraryController
from backend.exceptions import HardwareError, TapeLoadError, TapeUnloadError

logger = logging.getLogger(__name__)

class VirtualTapeController(TapeLibraryController):
    """
    Simulates a Tape Library using local filesystem.
    Supports dynamic configuration of drives and slots.
    """

    def __init__(self, *args, **kwargs):
        # Capture VTL specific config before super init if possible,
        # but TapeLibraryController init is simple.
        super().__init__(*args, **kwargs)
        
        # VTL Configuration
        self.vtl_root = os.environ.get('VTL_ROOT', '/tmp/fossil_safe_vtl')
        self.num_drives = int(os.environ.get('VTL_DRIVES', '2'))
        self.num_slots = int(os.environ.get('VTL_SLOTS', '24'))
        self.drive_only_mode = os.environ.get('VTL_DRIVE_ONLY', '0') == '1'
        
        self.state_file = os.path.join(self.vtl_root, 'state.json')
        self.tape_dir = os.path.join(self.vtl_root, 'tapes')
        self.mount_dir = os.path.join(self.vtl_root, 'mnt')
        
        # Override parent mount points
        self.mount_points = {i: os.path.join(self.mount_dir, f'drive{i}') for i in range(self.num_drives)}
        
        self._ensure_vtl_dirs()
        self._load_state()

    def _ensure_vtl_dirs(self):
        os.makedirs(self.vtl_root, exist_ok=True)
        os.makedirs(self.tape_dir, exist_ok=True)
        os.makedirs(self.mount_dir, exist_ok=True)
        for mp in self.mount_points.values():
            os.makedirs(mp, exist_ok=True)

    def _load_state(self):
        """Load inventory state from disk or initialize default."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    self.vtl_state = json.load(f)
            except Exception:
                self._init_default_state()
        else:
            self._init_default_state()
            
    def _init_default_state(self):
        """Populate random tapes if empty."""
        self.vtl_state = {
            'slots': {}, # index -> barcode
            'drives': {}, # index -> barcode
            'tapes': {}  # barcode -> {formatted: bool, label: str}
        }
        # Pre-fill some slots
        for i in range(1, 6):
            barcode = f"VTL{i:03d}L8"
            self.vtl_state['slots'][str(i)] = barcode
            self.vtl_state['tapes'][barcode] = {'formatted': False, 'label': ''}
        self._save_state()

    def _save_state(self):
        with open(self.state_file, 'w') as f:
            json.dump(self.vtl_state, f, indent=2)

    def initialize(self):
        logger.info(f"Initialized VTL at {self.vtl_root} with {self.num_drives} drives")
        return True

    def scan_barcodes(self) -> List[Dict]:
        """Return inventory based on VTL state."""
        inventory = []
        
        # Slots
        for slot_idx, barcode in self.vtl_state['slots'].items():
            if barcode:
                inventory.append({
                    'slot': int(slot_idx),
                    'barcode': barcode,
                    'type': 'slot'
                })
                
                
        # Drives
        for drive_idx in range(self.num_drives):
            barcode = self.vtl_state['drives'].get(str(drive_idx))
            inventory.append({
                'slot': None,
                'drive_index': drive_idx,
                'barcode': barcode,
                'type': 'drive'
            })
                
        self._last_inventory = inventory
        return inventory

    def load_tape(self, barcode: str, drive: int = 0) -> bool:
        """Move tape from slot to drive."""
        if self.drive_only_mode:
            # In drive-only, we just pretend we inserted it
            self.vtl_state['drives'][str(drive)] = barcode
            self._save_state()
            return True
            
        # Find where the tape is
        source_slot = None
        for slot, bc in self.vtl_state['slots'].items():
            if bc == barcode:
                source_slot = slot
                break
        
        if not source_slot:
            # Check if already in drive
            for d, bc in self.vtl_state['drives'].items():
                if bc == barcode:
                    if int(d) == drive:
                        return True # Already loaded
                    else:
                        raise TapeLoadError(f"Tape {barcode} is in drive {d}, cannot load to {drive}")
            raise TapeLoadError(f"Tape {barcode} not found in slots")
            
        # Check if drive is empty
        if self.vtl_state['drives'].get(str(drive)):
            raise TapeLoadError(f"Drive {drive} is not empty")
            
        # Move
        self.vtl_state['slots'][source_slot] = None
        self.vtl_state['drives'][str(drive)] = barcode
        self._save_state()
        time.sleep(1) # Simulate robot movement
        return True

    def unload_tape(self, drive: int = 0, slot: Optional[int] = None) -> bool:
        """Move tape from drive to slot."""
        barcode = self.vtl_state['drives'].get(str(drive))
        if not barcode:
            return True # Already empty
            
        # If no slot specified, find home or first empty
        target_slot = str(slot) if slot else None
        
        if not target_slot:
            # Find first empty
            for i in range(1, self.num_slots + 1):
                if not self.vtl_state['slots'].get(str(i)):
                    target_slot = str(i)
                    break
                    
        if not target_slot:
            raise TapeUnloadError("No free slots available")
            
        self.vtl_state['drives'][str(drive)] = None
        self.vtl_state['slots'][target_slot] = barcode
        self._save_state()
        time.sleep(1)
        return True

    def format_tape(self, drive: int = 0, label: str = None) -> bool:
        barcode = self.vtl_state['drives'].get(str(drive))
        if not barcode:
            raise HardwareError(f"No tape in drive {drive}")
            
        tape_data_path = os.path.join(self.tape_dir, barcode)
        # Create empty directory to simulate formatted tape
        if os.path.exists(tape_data_path):
            shutil.rmtree(tape_data_path)
        os.makedirs(tape_data_path)
        
        self.vtl_state['tapes'][barcode]['formatted'] = True
        self.vtl_state['tapes'][barcode]['label'] = label or barcode
        self._save_state()
        time.sleep(2) # Simulate formatting
        return True

    def mount_ltfs(self, barcode: str, drive: int = 0) -> str:
        # Check if loaded
        if self.vtl_state['drives'].get(str(drive)) != barcode:
            # Auto load if found
            try:
                self.load_tape(barcode, drive)
            except Exception:
                raise TapeLoadError(f"Tape {barcode} not in drive {drive} and could not be loaded")
                
        tape_data_path = os.path.join(self.tape_dir, barcode)
        if not os.path.exists(tape_data_path):
             raise HardwareError(f"Tape {barcode} is not formatted")
             
        mount_point = self.mount_points[drive]
        
        # Symlink tape dir to mount point to simulate mount
        # If mount point exists and is a dir, remove it first if empty or check?
        # Ideally mount point is an empty dir.
        # We can't symlink a dir to an existing dir easily without removing target first?
        # Or we act as if we mounted by copying or bind mounting. 
        # Simpler: The "mount point" IS the tape_data_path? 
        # No, the application expects to write to self.mount_points[drive].
        # So we clear mount point and symlink tape_data_path to it.
        
        if os.path.islink(mount_point):
            os.unlink(mount_point)
        elif os.path.isdir(mount_point):
            shutil.rmtree(mount_point)
            
        os.symlink(tape_data_path, mount_point)
        self.mounted_tapes[drive] = barcode
        return mount_point

    def unmount_ltfs(self, drive: int = 0) -> bool:
        mount_point = self.mount_points[drive]
        if os.path.islink(mount_point):
            os.unlink(mount_point)
            os.makedirs(mount_point) # Recreate empty mount dir
        self.mounted_tapes.pop(drive, None)
        return True
        
    def is_online(self) -> bool:
        return True
    
    def get_drive_status(self, drive: int = 0) -> Dict:
        barcode = self.vtl_state['drives'].get(str(drive))
        return {
            'online': True,
            'tape_loaded': bool(barcode),
            'barcode': barcode
        }
    
    def mkltfs(self, drive: int = 0, barcode: str = None, **kwargs):
        return self.format_tape(drive, barcode)
