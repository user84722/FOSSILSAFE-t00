"""
Property-Based Fuzzing Tests - FossilSafe Validation Logic
Uses Hypothesis to stress-test input validation functions.
"""
import unittest
import os
import sys
from hypothesis import given, strategies as st, settings, HealthCheck

# Ensure we can import backend
sys.path.append(os.getcwd())

from backend.utils.validation import (
    validate_job_name,
    validate_barcode,
    validate_smb_path,
    validate_local_path,
    validate_slot,
    validate_drive
)

class TestValidationFuzzing(unittest.TestCase):
    """Stress tests for validation functions using property-based generation"""

    @given(st.text())
    def test_fuzz_job_name(self, s):
        """Ensure validate_job_name never crashes on any string input"""
        res, msg = validate_job_name(s)
        self.assertIsInstance(res, bool)
        if not res:
            self.assertIsInstance(msg, str)

    @given(st.text())
    def test_fuzz_barcode(self, s):
        """Ensure validate_barcode never crashes on any string input"""
        res, msg = validate_barcode(s)
        self.assertIsInstance(res, bool)

    @given(st.text())
    def test_fuzz_smb_path(self, s):
        """Ensure validate_smb_path never crashes on any string input"""
        # We don't care if it fails validation, only that it handles the input safely
        try:
            res, msg = validate_smb_path(s)
            self.assertIsInstance(res, bool)
        except Exception as e:
            self.fail(f"validate_smb_path crashed with {type(e).__name__}: {e} on input {repr(s)}")

    @given(st.text())
    @settings(suppress_health_check=[HealthCheck.filter_too_much])
    def test_fuzz_local_path(self, s):
        """Ensure validate_local_path never crashes on any string input"""
        res, msg = validate_local_path(s)
        self.assertIsInstance(res, bool)

    @given(st.one_of(st.text(), st.integers(), st.none()))
    def test_fuzz_slot(self, s):
        """Ensure validate_slot never crashes on various types"""
        res, msg = validate_slot(s)
        self.assertIsInstance(res, bool)

    @given(st.one_of(st.text(), st.integers(), st.none()))
    def test_fuzz_drive(self, s):
        """Ensure validate_drive never crashes on various types"""
        res, msg = validate_drive(s)
        self.assertIsInstance(res, bool)

if __name__ == '__main__':
    unittest.main()
