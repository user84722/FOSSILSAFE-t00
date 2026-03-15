"""
Drive Maintenance Manager - Handles cleaning detection and auto-maintenance.
"""
import time
import subprocess
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DriveHealth:
    """Drive health status."""
    cleaning_required: bool = False
    cleaning_suggested: bool = False
    last_cleaned: Optional[datetime] = None
    cleaning_cycles: int = 0
    head_hours: Optional[int] = None
    error_rate: float = 0.0
    last_check: Optional[datetime] = None


@dataclass  
class CleaningPolicy:
    """Auto-cleaning policy configuration."""
    auto_clean_enabled: bool = True
    clean_before_jobs: bool = True  # Check & clean before backup jobs
    clean_on_error: bool = True     # Clean after write errors
    max_cleaning_cycles: int = 50   # Cleaning tape lifespan
    cleaning_tape_slot: Optional[int] = None  # Preferred cleaning tape slot


class DriveMaintenanceManager:
    """Manages drive health monitoring and automated cleaning."""
    
    def __init__(self, tape_controller, config: Dict = None, log_callback=None):
        self.tape_controller = tape_controller
        self.config = config or {}
        self.log_callback = log_callback
        self.policy = CleaningPolicy(**self.config.get('cleaning_policy', {}))
        self._health_cache: Dict[int, DriveHealth] = {}
    
    def _log(self, message: str, level: str = 'info'):
        """Log a message."""
        if self.log_callback:
            self.log_callback(message, level)
        else:
            print(f"[{level.upper()}] {message}")
    
    def check_cleaning_required(self, drive: int = 0) -> Tuple[bool, str]:
        """
        Check if drive requires cleaning by parsing SCSI sense data.
        
        Returns:
            Tuple of (cleaning_required, reason)
        """
        try:
            # Use mtx status via controller's unified/validated run_mt_command
            result = self.tape_controller._run_mt_command('status', drive=drive)
            
            output = result.stdout + result.stderr
            
            # Check for cleaning indicators in mt output
            # Different drives report this differently
            cleaning_indicators = [
                'CLEAN',
                'CLN',
                'cleaning',
                'requires cleaning',
                'needs cleaning',
            ]
            
            for indicator in cleaning_indicators:
                if indicator.lower() in output.lower():
                    return True, f"Drive reports cleaning required: {indicator}"
            
            # Note: sg_logs check removed here and moved to a more appropriate
            # hardware health service if needed, to keep mt commands clean.
            # But we've already checked the standard mt output.
            
            return False, "Drive does not require cleaning"
            
        except subprocess.TimeoutExpired:
            return False, "Timeout checking drive status"
        except Exception as e:
            return False, f"Error checking cleaning status: {e}"
    
    def get_drive_health(self, drive: int = 0) -> DriveHealth:
        """Get comprehensive drive health status."""
        health = DriveHealth()
        health.last_check = datetime.now()
        
        cleaning_required, reason = self.check_cleaning_required(drive)
        health.cleaning_required = cleaning_required
        
        # Check if cleaning is suggested (not required but recommended)
        if 'periodic' in reason.lower() or 'suggested' in reason.lower():
            health.cleaning_suggested = True
            health.cleaning_required = False
        
        # Cache the health status
        self._health_cache[drive] = health
        
        return health
    
    def should_auto_clean(self, drive: int = 0) -> Tuple[bool, str]:
        """
        Determine if auto-cleaning should be triggered.
        
        Returns:
            Tuple of (should_clean, reason)
        """
        if not self.policy.auto_clean_enabled:
            return False, "Auto-cleaning disabled"
        
        health = self.get_drive_health(drive)
        
        if health.cleaning_required:
            # Check if cleaning tape is available
            cleaning_tape = self._find_cleaning_tape()
            if not cleaning_tape:
                self._log("Cleaning required but no cleaning tape available", 'warning')
                return False, "No cleaning tape available"
            
            return True, "Drive requires cleaning"
        
        return False, "No cleaning needed"
    
    def _find_cleaning_tape(self) -> Optional[Dict]:
        """Find an available cleaning tape in the library."""
        try:
            tapes = self.tape_controller.scan_barcodes()
            cleaning_tapes = [
                t for t in tapes 
                if t.get('type') == 'cleaning' and t.get('status') == 'available'
            ]
            
            # Prefer configured slot if set
            if self.policy.cleaning_tape_slot is not None:
                for tape in cleaning_tapes:
                    if tape.get('slot') == self.policy.cleaning_tape_slot:
                        return tape
            
            # Return first available cleaning tape
            return cleaning_tapes[0] if cleaning_tapes else None
            
        except Exception as e:
            self._log(f"Error finding cleaning tape: {e}", 'error')
            return None
    
    def run_cleaning(self, drive: int = 0, force: bool = False) -> Tuple[bool, str]:
        """
        Run drive cleaning operation.
        
        Args:
            drive: Drive number to clean
            force: Force cleaning even if not required
            
        Returns:
            Tuple of (success, message)
        """
        if not force:
            should_clean, reason = self.should_auto_clean(drive)
            if not should_clean:
                return False, reason
        
        cleaning_tape = self._find_cleaning_tape()
        if not cleaning_tape:
            return False, "No cleaning tape available in library"
        
        barcode = cleaning_tape['barcode']
        self._log(f"Starting cleaning cycle for drive {drive} with tape {barcode}")
        
        try:
            success = self.tape_controller.clean_drive(barcode, drive)
            
            if success:
                self._log(f"Cleaning completed successfully for drive {drive}")
                # Update health cache
                if drive in self._health_cache:
                    self._health_cache[drive].cleaning_required = False
                    self._health_cache[drive].last_cleaned = datetime.now()
                    self._health_cache[drive].cleaning_cycles += 1
                
                return True, "Cleaning completed successfully"
            else:
                return False, "Cleaning operation failed"
                
        except Exception as e:
            self._log(f"Cleaning failed: {e}", 'error')
            return False, f"Cleaning failed: {e}"
    
    def pre_job_check(self, drive: int = 0) -> Tuple[bool, str]:
        """
        Pre-job cleaning check. Run before backup jobs if policy allows.
        
        Returns:
            Tuple of (job_can_proceed, message)
        """
        if not self.policy.clean_before_jobs:
            return True, "Pre-job cleaning check disabled"
        
        should_clean, reason = self.should_auto_clean(drive)
        
        if should_clean:
            self._log(f"Drive needs cleaning before job: {reason}")
            success, msg = self.run_cleaning(drive)
            
            if success:
                return True, "Drive cleaned, ready for job"
            else:
                # Cleaning failed but job can still proceed with warning
                self._log(f"Pre-job cleaning failed: {msg}", 'warning')
                return True, f"Warning: Cleaning failed ({msg}), proceeding with job"
        
        return True, "Drive ready"
    
    def get_cleaning_tape_usage(self) -> List[Dict]:
        """Get usage statistics for cleaning tapes."""
        try:
            tapes = self.tape_controller.scan_barcodes()
            cleaning_tapes = [t for t in tapes if t.get('type') == 'cleaning']
            
            # TODO: Track cleaning cycles per tape in database
            for tape in cleaning_tapes:
                tape['cycles_remaining'] = self.policy.max_cleaning_cycles - tape.get('usage_cycles', 0)
                tape['status_text'] = 'OK' if tape['cycles_remaining'] > 5 else 'Replace Soon'
            
            return cleaning_tapes
            
        except Exception:
            return []
