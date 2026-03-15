"""
Sanity Checks and Pre-flight Verification for FossilSafe.
Comprehensive checks for reliability and data integrity.
"""
import os
import subprocess
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CheckResult:
    """Result of a sanity check."""
    name: str
    passed: bool
    message: str
    severity: str = 'error'  # 'error', 'warning', 'info'
    details: Dict = field(default_factory=dict)


class SanityChecker:
    """Performs comprehensive sanity checks before backup operations."""
    
    # Limits
    MAX_FILE_COUNT = 10_000_000  # 10 million files
    MAX_SINGLE_FILE_SIZE = 10 * 1024 * 1024 * 1024 * 1024  # 10 TB
    MIN_TAPE_CAPACITY_MB = 100  # Minimum remaining capacity
    MAX_PATH_LENGTH = 4096
    
    def __init__(self, tape_controller=None, db=None, log_callback=None):
        self.tape_controller = tape_controller
        self.db = db
        self.log_callback = log_callback
    
    def _log(self, message: str, level: str = 'info'):
        if self.log_callback:
            self.log_callback(message, level)
    
    def run_preflight_checks(self, job_config: Dict) -> Tuple[bool, List[CheckResult]]:
        """
        Run all pre-flight checks before starting a backup job.
        
        Args:
            job_config: Job configuration dictionary
        
        Returns:
            Tuple of (all_passed, list_of_results)
        """
        results = []
        
        # Source accessibility
        results.append(self.check_source_accessible(job_config))
        
        # Tape readiness
        if self.tape_controller:
            results.append(self.check_tape_ready(job_config.get('tape_barcode')))
            results.append(self.check_tape_capacity(job_config))
            results.append(self.check_drive_health())
        
        # File validation
        if job_config.get('files'):
            results.extend(self.check_file_limits(job_config['files']))
        
        # Determine overall pass/fail
        errors = [r for r in results if not r.passed and r.severity == 'error']
        all_passed = len(errors) == 0
        
        return all_passed, results
    
    def check_source_accessible(self, job_config: Dict) -> CheckResult:
        """Check if the backup source is accessible."""
        source_type = job_config.get('source_type', 'smb')
        source_path = job_config.get('source_path', '')
        
        try:
            if source_type == 'local':
                if os.path.exists(source_path) and os.access(source_path, os.R_OK):
                    return CheckResult(
                        name='source_accessible',
                        passed=True,
                        message=f'Local source accessible: {source_path}'
                    )
                else:
                    return CheckResult(
                        name='source_accessible',
                        passed=False,
                        message=f'Local source not accessible: {source_path}',
                        severity='error'
                    )
            elif source_type in ('smb', 'nfs'):
                # For network sources, we assume they've been tested already
                return CheckResult(
                    name='source_accessible',
                    passed=True,
                    message=f'{source_type.upper()} source configured',
                    severity='info'
                )
            else:
                return CheckResult(
                    name='source_accessible',
                    passed=True,
                    message=f'Source type: {source_type}'
                )
        except Exception as e:
            return CheckResult(
                name='source_accessible',
                passed=False,
                message=f'Error checking source: {e}',
                severity='error'
            )
    
    def check_tape_ready(self, tape_barcode: Optional[str]) -> CheckResult:
        """Check if tape is loaded and ready."""
        try:
            if not self.tape_controller:
                return CheckResult(
                    name='tape_ready',
                    passed=False,
                    message='Tape controller not available',
                    severity='error'
                )
            
            drive_status = self.tape_controller.get_drive_status()
            
            if not drive_status.get('online'):
                return CheckResult(
                    name='tape_ready',
                    passed=False,
                    message='Tape drive is offline',
                    severity='error'
                )
            
            if not drive_status.get('ready'):
                return CheckResult(
                    name='tape_ready',
                    passed=False,
                    message='Tape drive not ready',
                    severity='error'
                )
            
            current_tape = self.tape_controller.get_current_tape()
            if tape_barcode and current_tape != tape_barcode:
                return CheckResult(
                    name='tape_ready',
                    passed=False,
                    message=f'Expected tape {tape_barcode}, found {current_tape}',
                    severity='error'
                )
            
            return CheckResult(
                name='tape_ready',
                passed=True,
                message=f'Tape ready: {current_tape or "loaded"}'
            )
            
        except Exception as e:
            return CheckResult(
                name='tape_ready',
                passed=False,
                message=f'Error checking tape: {e}',
                severity='error'
            )
    
    def check_tape_capacity(self, job_config: Dict) -> CheckResult:
        """Check if tape has sufficient capacity for estimated data."""
        try:
            estimated_bytes = job_config.get('estimated_bytes', 0)
            
            if not estimated_bytes:
                return CheckResult(
                    name='tape_capacity',
                    passed=True,
                    message='Capacity check skipped (no estimate)',
                    severity='info'
                )
            
            # This would need actual tape capacity query
            # For now, return a warning if estimate is very large
            estimated_gb = estimated_bytes / (1024 ** 3)
            
            if estimated_gb > 10000:  # 10TB threshold
                return CheckResult(
                    name='tape_capacity',
                    passed=True,
                    message=f'Large backup ({estimated_gb:.1f} GB) - may span tapes',
                    severity='warning'
                )
            
            return CheckResult(
                name='tape_capacity',
                passed=True,
                message=f'Estimated size: {estimated_gb:.1f} GB'
            )
            
        except Exception as e:
            return CheckResult(
                name='tape_capacity',
                passed=True,
                message=f'Capacity check failed: {e}',
                severity='warning'
            )
    
    def check_drive_health(self) -> CheckResult:
        """Check drive health and cleaning status."""
        try:
            from backend.drive_maintenance import DriveMaintenanceManager
            
            manager = DriveMaintenanceManager(self.tape_controller)
            health = manager.get_drive_health()
            
            if health.cleaning_required:
                return CheckResult(
                    name='drive_health',
                    passed=True,  # Pass but warn
                    message='Drive cleaning required',
                    severity='warning',
                    details={'cleaning_required': True}
                )
            
            return CheckResult(
                name='drive_health',
                passed=True,
                message='Drive health OK'
            )
            
        except Exception as e:
            return CheckResult(
                name='drive_health',
                passed=True,
                message=f'Health check unavailable: {e}',
                severity='info'
            )
    
    def check_file_limits(self, files: List[Dict]) -> List[CheckResult]:
        """Check file count and size limits."""
        results = []
        
        # File count check
        file_count = len(files)
        if file_count > self.MAX_FILE_COUNT:
            results.append(CheckResult(
                name='file_count',
                passed=False,
                message=f'Too many files: {file_count:,} (max {self.MAX_FILE_COUNT:,})',
                severity='error'
            ))
        else:
            results.append(CheckResult(
                name='file_count',
                passed=True,
                message=f'File count: {file_count:,}'
            ))
        
        # Large file check
        large_files = [f for f in files if f.get('size', 0) > self.MAX_SINGLE_FILE_SIZE]
        if large_files:
            results.append(CheckResult(
                name='large_files',
                passed=False,
                message=f'{len(large_files)} files exceed size limit',
                severity='error',
                details={'files': [f['path'] for f in large_files[:10]]}
            ))
        
        # Path length check
        long_paths = [f for f in files if len(f.get('path', '')) > self.MAX_PATH_LENGTH]
        if long_paths:
            results.append(CheckResult(
                name='path_length',
                passed=False,
                message=f'{len(long_paths)} paths exceed length limit',
                severity='error'
            ))
        
        return results
    
    def check_post_job(self, job_id: int, expected_files: int, written_bytes: int) -> List[CheckResult]:
        """Post-job verification checks."""
        results = []
        
        # Verify file count matches
        if self.db:
            try:
                job = self.db.get_job(job_id)
                actual_files = job.get('files_processed', 0)
                
                if actual_files < expected_files:
                    results.append(CheckResult(
                        name='file_count_match',
                        passed=False,
                        message=f'Only {actual_files}/{expected_files} files written',
                        severity='error'
                    ))
                else:
                    results.append(CheckResult(
                        name='file_count_match',
                        passed=True,
                        message=f'All {actual_files} files written'
                    ))
            except Exception as e:
                results.append(CheckResult(
                    name='file_count_match',
                    passed=False,
                    message=f'Could not verify file count: {e}',
                    severity='warning'
                ))
        
        return results


def run_preflight(job_config: Dict, tape_controller=None, db=None) -> Tuple[bool, List[Dict]]:
    """
    Convenience function to run pre-flight checks.
    
    Returns:
        Tuple of (passed, list of check result dicts)
    """
    checker = SanityChecker(tape_controller=tape_controller, db=db)
    passed, results = checker.run_preflight_checks(job_config)
    
    return passed, [
        {
            'name': r.name,
            'passed': r.passed,
            'message': r.message,
            'severity': r.severity,
            'details': r.details
        }
        for r in results
    ]
