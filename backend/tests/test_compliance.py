import pytest
import os
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from backend.database import Database
from backend.tape_controller import TapeLibraryController
from backend.exceptions import ComplianceError

@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "fossil_safe_test.db"
    # Set key path for AuditSigner
    key_path = tmp_path / "audit_key.pem"
    os.environ['FOSSIL_SAFE_AUDIT_KEY_PATH'] = str(key_path)
    db = Database(str(db_path))
    return db

@pytest.fixture
def controller(db):
    return TapeLibraryController(db=db)

def test_worm_locking(db):
    """Test that we can lock a tape and check its status."""
    barcode = "LTO001"
    db.add_tape(barcode, "slot_1", "LIB-1")
    
    # Not locked initially
    assert not db.is_tape_locked(barcode)
    
    # Apply lock
    expiry = (datetime.utcnow() + timedelta(days=365)).isoformat()
    assert db.lock_tape(barcode, expiry)
    
    # Verify locked
    assert db.is_tape_locked(barcode)
    
    # Verify tape details
    tape = db.get_tape(barcode)
    assert bool(tape['worm_lock']) is True
    assert tape['retention_expires_at'] == expiry

def test_worm_lock_expiration(db):
    """Test that an expired lock is no longer enforced."""
    barcode = "LTOEXPIRED"
    db.add_tape(barcode, "slot_2", "LIB-1")
    
    # Lock with past expiration
    expiry = (datetime.utcnow() - timedelta(days=1)).isoformat()
    db.lock_tape(barcode, expiry)
    
    # Should NOT be locked
    assert not db.is_tape_locked(barcode)

def test_compliance_guards(db, controller):
    """Test that TapeLibraryController enforces WORM guards."""
    barcode = "WORM-LOCKED"
    db.add_tape(barcode, "slot_3", "LIB-1")
    
    # Lock the tape
    expiry = (datetime.utcnow() + timedelta(days=30)).isoformat()
    db.lock_tape(barcode, expiry)
    
    # Mock drive availability
    controller._get_drive_device = MagicMock(return_value="/dev/st0")
    
    # Try to format - should raise ComplianceError
    with pytest.raises(ComplianceError) as excinfo:
        controller.format_tape(barcode)
    assert "WORM retention lock" in str(excinfo.value)
    
    # Try to wipe - should raise ComplianceError
    with pytest.raises(ComplianceError) as excinfo:
        controller.wipe_tape(barcode)
    assert "WORM retention lock" in str(excinfo.value)

def test_compliance_report(db):
    """Test compliance report generation."""
    # Add some tapes, some locked
    db.add_tape("T1", "slot_1", "L")
    db.add_tape("T2", "slot_2", "L")
    db.lock_tape("T1", "2030-01-01T00:00:00")
    
    # Add some security events (audit logs)
    db.add_audit_log("SYSTEM_START", message="System started", level="info", category="system")
    db.add_audit_log("SECURITY_ALERT", message="Compliance breach alert", level="warning", category="security", detail={"ip": "1.1.1.1"})
    
    report = db.generate_compliance_report()
    
    assert report['report_id'].startswith("RPT-")
    assert report['generated_at'] is not None
    assert report['status'] == 'COMPLIANT'
    assert report['compliance_stats']['worm_locked_tapes'] == 1
    assert report['compliance_stats']['security_events_summary']['warning'] >= 1
    assert 'signature' in report
