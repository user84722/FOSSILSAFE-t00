import unittest
import time
import threading
import logging
from backend.tape.simulators import MockTapeController
from backend.exceptions import TapeLoadError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TestResilience(unittest.TestCase):
    def setUp(self):
        self.controller = MockTapeController()

    def test_fault_injection_load_tape(self):
        """Verify load_tape fails when fault is injected."""
        self.controller.inject_fault('load_tape', 1.0, TapeLoadError, "Simulated mechanical failure")
        
        with self.assertRaises(TapeLoadError) as cm:
            self.controller.load_tape('TEST001')
        
        self.assertIn("Simulated mechanical failure", str(cm.exception))

    def test_intermittent_failure(self):
        """Verify faults can be probabilistic."""
        # This is a statistical test, so we inject 50% failure rate
        # and run enough times to be reasonably sure we see both outcomes.
        self.controller.inject_fault('mount_ltfs', 0.5, Exception, "Random I/O Error")
        
        successes = 0
        failures = 0
        
        for _ in range(20):
            try:
                self.controller.mount_ltfs('TEST002')
                successes += 1
            except Exception:
                failures += 1
        
        logger.info(f"Intermittent Test: {successes} successes, {failures} failures")
        self.assertGreater(successes, 0)
        self.assertGreater(failures, 0)

if __name__ == '__main__':
    unittest.main()
