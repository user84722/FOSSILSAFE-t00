import time
import json
from typing import List, Dict, Optional, Tuple
from backend.config_store import load_config
from backend.utils.naming import get_random_name

class TapeService:
    def __init__(self, db, tape_controller=None, library_manager=None):
        self.db = db
        self.tape_controller = tape_controller
        self.library_manager = library_manager
        self._last_deep_scan_at: Dict[str, float] = {}

    def _get_controller(self, library_id: Optional[str] = None):
        if self.library_manager:
            return self.library_manager.get_library(library_id) or self.tape_controller
        return self.tape_controller

    def auto_alias_tapes(self, overwrite: bool = False) -> int:
        """Assign random dinosaur names to tapes."""
        tapes = self.db.get_tape_inventory()
        count = 0
        for tape in tapes:
            barcode = tape.get("barcode")
            current_alias = tape.get("alias")
            
            if not barcode:
                continue
                
            if not current_alias or (overwrite and current_alias):
                new_alias = get_random_name()
                # Ensure uniqueness could be added here, but low collision prob for now
                self.db.update_tape_alias(barcode, new_alias)
                count += 1
        return count

    def get_scan_settings(self) -> Dict[str, object]:
        tape_settings = (load_config().get("tape", {}) or {})
        mode = (tape_settings.get("scan_mode_default") or "deep").lower()
        if mode not in ("fast", "deep"):
            mode = "deep"
        try:
            deep_scan_interval = int(tape_settings.get("deep_scan_interval_seconds", 60))
        except (TypeError, ValueError):
            deep_scan_interval = 60
        return {
            "default_mode": mode,
            "deep_scan_interval_seconds": max(0, deep_scan_interval),
        }

    def get_deep_scan_blocking_job(self) -> Optional[Dict[str, object]]:
        active_jobs = self.db.get_active_jobs()
        if not active_jobs:
            return None
        
        blocking_types = {
            "backup",
            "restore",
            "health_check",
            "tape_wipe",
            "tape_initialize",
            "library_load",
            "library_unload",
            "library_force_unload",
            "tape_move",
        }
        for job in active_jobs:
            job_type = (job.get("job_type") or "").lower()
            if job_type in blocking_types and job.get("status") == "running":
                return job
            progress_state = (job.get("progress_state") or "").lower()
            if progress_state in ("erasing", "writing", "restoring", "formatting"):
                return job
        return None

    def guard_deep_scan(self, settings: Dict[str, object], library_id: Optional[str] = None) -> Optional[Tuple[dict, int]]:
        controller = self._get_controller(library_id)
        if controller and controller.is_busy():
            return {'success': False, 'error': 'Library is busy; try fast scan or retry later'}, 409
        
        blocking_job = self.get_deep_scan_blocking_job()
        if blocking_job:
            # TODO: Filter blocking jobs by library_id if job tracks it
            job_name = blocking_job.get("name") or f"job {blocking_job.get('id')}"
            return {'success': False, 'error': f'Library busy with {job_name}; retry deep scan later'}, 409
        
        interval = settings.get("deep_scan_interval_seconds", 0)
        if interval:
            now = time.time()
            key = library_id or 'default'
            last_scan = self._last_deep_scan_at.get(key, 0.0)
            elapsed = now - last_scan
            if last_scan and elapsed < interval:
                remaining = int(interval - elapsed)
                return {
                    'success': False,
                    'error': f'Deep scan rate-limited; retry in {remaining}s',
                    'retry_after_seconds': remaining,
                }, 429
            self._last_deep_scan_at[key] = now
        return None

    def apply_active_tape_job_state(self, tapes: List[Dict], active_jobs: List[Dict]) -> List[Dict]:
        """Overlay active job state onto inventory to avoid scan overwrites."""
        tape_map = {t.get("barcode"): dict(t) for t in tapes if t.get("barcode")}
        # Preserve empty drives (placeholders) in the overlay
        for t in tapes:
            if not t.get("barcode") and t.get("location_type") == "drive":
                d_idx = t.get("drive_index")
                if d_idx is not None:
                    tape_map[f"__drive_{d_idx}__"] = dict(t)

        # Inject missing empty drives into the inventory so they always appear as drop targets
        if self.tape_controller:
            try:
                drive_count = len(getattr(self.tape_controller, 'drive_devices', {0: None}))
                for i in range(drive_count):
                    if f"__drive_{i}__" not in tape_map and not any(t.get('drive_index') == i for t in tape_map.values()):
                         tape_map[f"__drive_{i}__"] = {
                            'barcode': '',
                            'status': 'empty',
                            'location': f'Drive {i}',
                            'location_type': 'drive',
                            'drive_index': i,
                            'drive_full': False,
                            'is_placeholder': True,
                            'has_barcode': False
                        }
            except Exception:
                pass

        for job in active_jobs:
            job_type = job.get("job_type") or ""
            if job_type not in ("tape_wipe", "tape_initialize", "library_load", "library_unload", "tape_move"):
                continue
            
            tapes_list = job.get("tapes") or []
            if isinstance(tapes_list, str):
                try:
                    tapes_list = json.loads(tapes_list)
                except Exception:
                    tapes_list = []
            
            if job.get("current_tape"):
                tapes_list = list({*tapes_list, job.get("current_tape")})

            for barcode in tapes_list:
                if not barcode:
                    continue
                drive = int(job.get("drive") or 0)
                entry = tape_map.get(barcode, {
                    "barcode": barcode,
                    "slot": None,
                    "status": "busy",
                })
                progress_state = (job.get("progress_state") or "").lower()
                if job_type == "tape_wipe":
                    if progress_state == "erasing":
                        status = "erasing"
                    elif progress_state in ("unloading", "finalizing", "formatting"):
                        status = "wiping"
                    else:
                        status = "wiping"
                elif job_type == "tape_initialize":
                    status = "initializing"
                elif job_type == "library_load":
                    status = "loaded"
                else:
                    status = "busy"

                entry.update({
                    "status": status,
                    "location": f"Drive {drive}",
                    "location_type": "drive",
                    "drive_index": drive,
                    "active_job_id": job.get("id"),
                    "active_job_type": job_type,
                })
                tape_map[barcode] = entry
        
        # Final pass to ensure all tapes have a synthesized location field if missing
        result = []
        for tape in tape_map.values():
            if not tape.get("location"):
                loc_type = tape.get("location_type")
                d_idx = tape.get("drive_index")
                slot = tape.get("slot")
                if loc_type == 'drive' and d_idx is not None:
                    tape["location"] = f"Drive {d_idx}"
                elif loc_type == 'slot' and slot is not None:
                    tape["location"] = f"Slot {slot}"
                elif loc_type == 'mailslot' and slot is not None:
                    tape["location"] = f"Mailslot {slot}"
            result.append(tape)
            
        return result

    def scan_library_and_update(self, mode: str, library_id: Optional[str] = None) -> List[Dict[str, object]]:
        controller = self._get_controller(library_id)
        if not controller:
            raise RuntimeError("No controller available")
        tapes = controller.scan_library(mode=mode)
        
        # Determine library ID
        actual_lib_id = library_id
        if not actual_lib_id and hasattr(controller, 'library_id'):
             actual_lib_id = controller.library_id
        
        if actual_lib_id:
            for tape in tapes:
                tape['library_id'] = actual_lib_id

        self.db.update_tape_inventory(tapes)
        aliases = self.db.get_tape_aliases()
        db_tapes = {tape["barcode"]: tape for tape in self.db.get_tape_inventory() if tape.get("barcode")}
        
        has_mail_slots = False
        for tape in tapes:
            if tape.get('location_type') == 'mailslot':
                has_mail_slots = True
            barcode = tape.get("barcode")
            tape['alias'] = aliases.get(barcode) or tape.get('alias')
            db_tape = db_tapes.get(barcode, {})
            tape.update({
                "capacity_bytes": db_tape.get("capacity_bytes"),
                "used_bytes": db_tape.get("used_bytes"),
                "ltfs_formatted": db_tape.get("ltfs_formatted"),
                "ltfs_verified_at": db_tape.get("ltfs_verified_at"),
                "volume_name": db_tape.get("volume_name"),
            })
            
        # Update config if mail slots were detected to avoid requirement for manual toggle
        if has_mail_slots:
            from backend.config_store import update_config
            update_config({"tape": {"mail_slot_detected": True}})

        active_jobs = self.db.get_active_jobs()
        return self.apply_active_tape_job_state(tapes, active_jobs)
