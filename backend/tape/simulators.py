import random
import time
import logging

logger = logging.getLogger(__name__)

class MockTapeController:
    """
    Simulated Tape Controller for hardware-less testing.
    Supports fault injection to test resilience.
    """
    def __init__(self, device='/dev/nst0', changer='/dev/sg1'):
        self.device = device
        self.changer = changer
        self.mounted_tape = None
        self.faults = {}
        self.library_id = "mock_lib_1"

    def inject_fault(self, operation: str, probability: float, error_class: Exception, error_message: str):
        """
        Inject a fault for a specific operation.
        :param operation: method name to fail (e.g., 'load_tape', 'mount_ltfs')
        :param probability: 0.0 to 1.0
        :param error_class: Exception class to raise
        :param error_message: Message for the exception
        """
        self.faults[operation] = {
            'probability': probability,
            'error': error_class,
            'message': error_message
        }

    def _maybe_fail(self, operation):
        if operation in self.faults:
            fault = self.faults[operation]
            if random.random() < fault['probability']:
                logger.info(f"Injecting fault for {operation}")
                raise fault['error'](fault['message'])

    def load_tape(self, barcode):
        self._maybe_fail('load_tape')
        return True

    def unload_tape(self, drive=0):
        self._maybe_fail('unload_tape')
        self.mounted_tape = None
        return True

    def mount_ltfs(self, barcode, drive=0):
        self._maybe_fail('mount_ltfs')
        self.mounted_tape = barcode
        return True, f"/tmp/mock_mount_{drive}"

    def unmount_ltfs(self, drive=0):
        self._maybe_fail('unmount_ltfs')
        self.mounted_tape = None
        return True

    def scan_barcodes(self):
        self._maybe_fail('scan_barcodes')
        return [
            {'barcode': 'MOCK001', 'slot': 1},
            {'barcode': 'MOCK002', 'slot': 2}
        ]

    def is_online(self):
        return True

    def is_ltfs_mounted(self, drive=0):
        return self.mounted_tape is not None

    def get_drive_status(self):
        return {'available': True, 'loaded_tape': self.mounted_tape}

    def simulate_hardware_error(self, message="Hardware error: Tape drive internal failure"):
        """Simulate a fatal drive hardware error."""
        self.inject_fault('load_tape', 1.0, RuntimeError, message)
        self.inject_fault('mount_ltfs', 1.0, RuntimeError, message)
        self.inject_fault('scan_barcodes', 1.0, RuntimeError, message)

    def simulate_write_protect(self, barcode):
        """Simulate a write-protected tape."""
        def fail_on_write(*args, **kwargs):
            raise RuntimeError(f"Tape {barcode} is write-protected")
        
        # Override mount_ltfs to fail with write-protect error if it involves this barcode
        original_mount = self.mount_ltfs
        def mocked_mount(target_barcode, drive=0):
            if target_barcode == barcode:
                raise RuntimeError(f"Tape {target_barcode} is write-protected")
            return original_mount(target_barcode, drive)
        
        self.mount_ltfs = mocked_mount

    def clear_faults(self):
        """Clear all injected faults."""
        self.faults = {}
        # Restore any overridden methods if necessary (though simple re-init is often easier in tests)
