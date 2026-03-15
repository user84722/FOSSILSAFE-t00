"""
FossilSafe Custom Exceptions.
Provides a standard hierarchy for application-specific errors.
"""

class FossilSafeError(Exception):
    """Base exception for all FossilSafe errors."""
    pass


class HardwareError(FossilSafeError):
    """Base exception for hardware-related errors (tape, drive, library)."""
    pass


class CalibrationError(HardwareError):
    """Failed to calibrate drive mappings."""
    pass


class HardwareCommunicationError(HardwareError):
    """Failed to communicate with a hardware device."""
    pass


class TapeError(HardwareError):
    """Tape-specific errors."""
    pass


class TapeLoadError(TapeError):
    """Failed to load a tape."""
    pass


class TapeUnloadError(TapeError):
    """Failed to unload a tape."""
    pass


class TapeMountError(TapeError):
    """Failed to mount a tape (LTFS)."""
    pass


class TapeUnmountError(TapeError):
    """Failed to unmount a tape (LTFS)."""
    pass


class TapeFormatError(TapeError):
    """Failed to format a tape."""
    pass


class VolumeError(FossilSafeError):
    """Volume management logic errors (spanning, naming)."""
    pass


class ConfigError(FossilSafeError):
    """Configuration or validation errors."""
    pass


class AuthError(FossilSafeError):
    """Authentication or authorization errors."""
    pass


class ComplianceError(FossilSafeError):
    """Enterprise compliance/WORM enforcement errors."""
    pass


class NetworkSourceError(FossilSafeError):
    """Error interacting with a network source (SMB, NFS)."""
    pass
