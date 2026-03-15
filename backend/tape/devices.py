import glob
import logging
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from backend.config_store import load_config, load_state

FOSSILSAFE_CHANGER_SYMLINK = "/dev/fossilsafe-changer"
FOSSILSAFE_DRIVE_SG_SYMLINK = "/dev/fossilsafe-drive-sg"
FOSSILSAFE_DRIVE_NST_SYMLINK = "/dev/fossilsafe-drive-nst"

logger = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    path: str
    sg_path: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    scsi_type: Optional[str] = None
    serial: Optional[str] = None


def _normalize_device_path(value: Optional[str]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _read_sysfs(path: str) -> Optional[str]:
    try:
        with open(path, "r") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _is_char_device(path: str) -> bool:
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return False
    return stat.S_ISCHR(mode)


def _is_valid_char_device(path: str) -> bool:
    return bool(path) and os.path.exists(path) and _is_char_device(path)


def _scsi_generic_type(sg_path: str) -> Optional[str]:
    sg_name = os.path.basename(os.path.realpath(sg_path))
    sysfs_path = f"/sys/class/scsi_generic/{sg_name}/device/type"
    return _read_sysfs(sysfs_path)


def is_medium_changer_device(sg_path: str) -> bool:
    return _scsi_generic_type(sg_path) == "8"


def _scsi_generic_vendor_model(sg_path: str) -> Tuple[Optional[str], Optional[str]]:
    sg_name = os.path.basename(os.path.realpath(sg_path))
    vendor = _read_sysfs(f"/sys/class/scsi_generic/{sg_name}/device/vendor")
    model = _read_sysfs(f"/sys/class/scsi_generic/{sg_name}/device/model")
    return vendor, model


def _scsi_generic_serial(sg_path: str) -> Optional[str]:
    sg_name = os.path.basename(os.path.realpath(sg_path))
    return _read_sysfs(f"/sys/class/scsi_generic/{sg_name}/device/serial")


def _scsi_tape_info(st_path: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    st_name = os.path.basename(st_path)
    vendor = _read_sysfs(f"/sys/class/scsi_tape/{st_name}/device/vendor")
    model = _read_sysfs(f"/sys/class/scsi_tape/{st_name}/device/model")
    serial = _read_sysfs(f"/sys/class/scsi_tape/{st_name}/device/serial")
    return vendor, model, serial


def _scsi_tape_sg_path(st_path: str) -> Optional[str]:
    st_name = os.path.basename(st_path)
    sg_link = f"/sys/class/scsi_tape/{st_name}/device/scsi_generic"
    try:
        target = os.path.basename(os.path.realpath(sg_link))
    except OSError:
        return None
    if target.startswith("sg"):
        sg_path = f"/dev/{target}"
        if os.path.exists(sg_path):
            return sg_path
    return None


def parse_lsscsi_output(output: str) -> Tuple[List[DeviceInfo], List[DeviceInfo]]:
    drives: List[DeviceInfo] = []
    changers: List[DeviceInfo] = []

    for line in (output or "").splitlines():
        if not line.strip():
            continue

        parts = line.split()
        if len(parts) < 4:
            continue

        device_type = parts[1] if len(parts) > 1 else ''
        dev_path = None
        sg_path = None
        for part in parts:
            if part.startswith('/dev/st') or part.startswith('/dev/nst'):
                dev_path = part
            elif part.startswith('/dev/sg'):
                sg_path = part

        vendor_model = ' '.join(parts[2:-2]) if len(parts) > 4 else 'Unknown'
        vendor_model = re.sub(r'\s+', ' ', vendor_model).strip()
        vendor_parts = vendor_model.split()
        vendor = vendor_parts[0] if vendor_parts else 'Unknown'
        model = ' '.join(vendor_parts[1:]) if len(vendor_parts) > 1 else ''

        if device_type == 'tape' and (dev_path or sg_path):
            tape_path = dev_path or sg_path
            if tape_path and tape_path.startswith('/dev/st'):
                candidate = tape_path.replace('/dev/st', '/dev/nst', 1)
                if os.path.exists(candidate):
                    tape_path = candidate
            drives.append(DeviceInfo(path=tape_path, sg_path=sg_path, vendor=vendor, model=model))
        elif device_type == 'mediumx' and sg_path:
            changers.append(DeviceInfo(path=sg_path, vendor=vendor, model=model))

    return drives, changers


def _run_lsscsi() -> str:
    try:
        result = subprocess.run(['lsscsi', '-g'], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout or ""


def _discover_scsi_generic() -> List[DeviceInfo]:
    devices: List[DeviceInfo] = []
    for sg_path in sorted(glob.glob('/dev/sg*')):
        if not os.path.exists(sg_path):
            continue
        scsi_type = _scsi_generic_type(sg_path)
        vendor, model = _scsi_generic_vendor_model(sg_path)
        serial = _scsi_generic_serial(sg_path)
        devices.append(DeviceInfo(path=sg_path, vendor=vendor, model=model, scsi_type=scsi_type, serial=serial))
    return devices


def _discover_tape_drives_sysfs() -> List[DeviceInfo]:
    drives: List[DeviceInfo] = []
    for st_name in sorted(glob.glob('/sys/class/scsi_tape/st*')):
        base = os.path.basename(st_name)
        numeric = re.sub(r'\D', '', base)
        if not numeric:
            continue
        vendor, model, serial = _scsi_tape_info(base)
        sg_path = _scsi_tape_sg_path(base)
        nst_path = f"/dev/nst{numeric}"
        st_path = f"/dev/st{numeric}"
        path = nst_path if os.path.exists(nst_path) else st_path
        if os.path.exists(path):
            drives.append(DeviceInfo(path=path, sg_path=sg_path, vendor=vendor, model=model, serial=serial))
    return drives


def discover_devices() -> Tuple[List[DeviceInfo], List[DeviceInfo]]:
    lsscsi_output = _run_lsscsi()
    sysfs_drives = _discover_tape_drives_sysfs()
    sysfs_drive_map = {drive.path: drive for drive in sysfs_drives}
    sysfs_sg_map = {drive.sg_path: drive for drive in sysfs_drives if drive.sg_path}
    if lsscsi_output:
        drives, changers = parse_lsscsi_output(lsscsi_output)
        for drive in drives:
            if drive.path in sysfs_drive_map:
                sysfs_drive = sysfs_drive_map[drive.path]
                drive.sg_path = drive.sg_path or sysfs_drive.sg_path
                drive.vendor = drive.vendor or sysfs_drive.vendor
                drive.model = drive.model or sysfs_drive.model
                drive.serial = drive.serial or sysfs_drive.serial
            elif drive.sg_path and drive.sg_path in sysfs_sg_map:
                sysfs_drive = sysfs_sg_map[drive.sg_path]
                drive.vendor = drive.vendor or sysfs_drive.vendor
                drive.model = drive.model or sysfs_drive.model
                drive.serial = drive.serial or sysfs_drive.serial
        if drives or changers:
            return drives, changers

    drives = sysfs_drives
    changers: List[DeviceInfo] = []
    for device in _discover_scsi_generic():
        if device.scsi_type == "8":
            changers.append(device)
    return drives, changers


def _resolve_drive_path(
    configured_drive: Optional[str],
    drives: List[DeviceInfo],
) -> Tuple[Optional[str], Optional[str]]:
    if _is_valid_char_device(FOSSILSAFE_DRIVE_NST_SYMLINK):
        return FOSSILSAFE_DRIVE_NST_SYMLINK, "symlink"
    if configured_drive and _is_valid_char_device(configured_drive):
        if configured_drive.startswith("/dev/st"):
            nst_path = configured_drive.replace("/dev/st", "/dev/nst", 1)
            if os.path.exists(nst_path):
                return nst_path, "configured_nst_fallback"
        return configured_drive, "configured"
    if drives:
        return drives[0].path, "discovered"
    return None, None


def _resolve_changer_path(
    configured_changer: Optional[str],
    changers: List[DeviceInfo],
) -> Tuple[Optional[str], Optional[str]]:
    if _is_valid_char_device(FOSSILSAFE_CHANGER_SYMLINK):
        scsi_type = _scsi_generic_type(FOSSILSAFE_CHANGER_SYMLINK)
        if scsi_type == "8":
            return FOSSILSAFE_CHANGER_SYMLINK, "symlink"
        logger.warning(
            "FossilSafe changer symlink %s is not a medium changer (SCSI type=%s); ignoring",
            FOSSILSAFE_CHANGER_SYMLINK,
            scsi_type,
        )
    if configured_changer and _is_valid_char_device(configured_changer):
        scsi_type = _scsi_generic_type(configured_changer)
        if scsi_type == "8":
            return configured_changer, "configured"
        if changers:
            return changers[0].path, "discovered_fallback"
        return configured_changer, "configured_unverified"
    if changers:
        return changers[0].path, "discovered"
    return None, None


def _resolve_drive_sg(drives: List[DeviceInfo]) -> Tuple[Optional[str], Optional[str]]:
    if _is_valid_char_device(FOSSILSAFE_DRIVE_SG_SYMLINK):
        scsi_type = _scsi_generic_type(FOSSILSAFE_DRIVE_SG_SYMLINK)
        if scsi_type == "1":
            return FOSSILSAFE_DRIVE_SG_SYMLINK, "symlink"
        logger.warning(
            "FossilSafe drive sg symlink %s is not a tape drive (SCSI type=%s); ignoring",
            FOSSILSAFE_DRIVE_SG_SYMLINK,
            scsi_type,
        )
    if drives and drives[0].sg_path and os.path.exists(drives[0].sg_path):
        return drives[0].sg_path, "discovered"
    return None, None


def _build_health(devices: Dict[str, Optional[str]]) -> Dict[str, object]:
    errors: List[str] = []
    warnings: List[str] = []
    if not devices.get('drive_nst'):
        errors.append("Tape drive not found.")
    if not devices.get('changer_sg'):
        warnings.append("Medium changer not found; drive-only mode expected.")
    changer_path = devices.get('changer_sg')
    if changer_path and os.path.exists(changer_path):
        scsi_type = _scsi_generic_type(changer_path)
        if scsi_type == "1":
            warnings.append(f"Configured changer {changer_path} appears to be a tape drive (SCSI type 1).")
    drive_sg = devices.get('drive_sg')
    if changer_path and drive_sg:
        try:
            if os.path.realpath(changer_path) == os.path.realpath(drive_sg):
                errors.append(
                    f"Changer path {changer_path} resolves to the tape drive sg device {drive_sg}."
                )
        except OSError:
            pass
    status = "ok"
    if errors:
        status = "error"
    elif warnings:
        status = "warning"
    return {"status": status, "errors": errors, "warnings": warnings}


def get_devices(config: Optional[Dict] = None, state: Optional[Dict] = None) -> Tuple[Dict[str, Optional[str]], Dict[str, object]]:
    """
    Resolve tape drive/changer devices with symlink preference and validation.
    Returns (devices, health).
    """
    config = config or load_config()
    state = state or load_state()

    tape_config = config.get('tape') if isinstance(config, dict) else {}
    if not isinstance(tape_config, dict):
        tape_config = {}
    state_tape = state.get('tape') if isinstance(state, dict) else {}
    if not isinstance(state_tape, dict):
        state_tape = {}

    configured_changer = _normalize_device_path(
        tape_config.get('changer_device') or tape_config.get('changer') or state_tape.get('changer_device')
    )
    drive_devices = tape_config.get('drive_devices')
    configured_drive = _normalize_device_path(
        tape_config.get('drive_device') or tape_config.get('drive') or state_tape.get('drive_device')
    )
    if isinstance(drive_devices, list) and drive_devices:
        configured_drive = _normalize_device_path(drive_devices[0])

    drives, changers = discover_devices()

    drive_nst, drive_nst_source = _resolve_drive_path(configured_drive, drives)
    changer_sg, changer_source = _resolve_changer_path(configured_changer, changers)
    drive_sg, drive_sg_source = _resolve_drive_sg(drives)

    if changer_sg and drive_sg:
        try:
            if os.path.realpath(changer_sg) == os.path.realpath(drive_sg):
                alternative = next(
                    (changer.path for changer in changers if changer.path and os.path.realpath(changer.path) != os.path.realpath(drive_sg)),
                    None,
                )
                if alternative:
                    logger.warning(
                        "Changer device %s resolves to drive sg %s; switching to detected changer %s.",
                        changer_sg,
                        drive_sg,
                        alternative,
                    )
                    changer_sg = alternative
                    changer_source = "discovered_deduped"
        except OSError:
            pass

    drive_info = drives[0] if drives else DeviceInfo(path=drive_nst or "")
    changer_info = None
    if changer_sg:
        for changer in changers:
            if changer.path == changer_sg:
                changer_info = changer
                break

    devices = {
        "changer_sg": changer_sg,
        "drive_nst": drive_nst,
        "drive_sg": drive_sg,
        "drive_vendor": drive_info.vendor,
        "drive_model": drive_info.model,
        "drive_serial": drive_info.serial,
        "changer_vendor": changer_info.vendor if changer_info else None,
        "changer_model": changer_info.model if changer_info else None,
        "changer_serial": changer_info.serial if changer_info else None,
        "probe": {
            "drive": {"status": "unknown" if drive_nst else "missing"},
            "changer": {"status": "unknown" if changer_sg else "missing"},
        },
        "resolution": {
            "drive_nst": {"path": drive_nst, "source": drive_nst_source},
            "drive_sg": {"path": drive_sg, "source": drive_sg_source},
            "changer_sg": {"path": changer_sg, "source": changer_source},
        },
    }
    health = _build_health(devices)
    logger.info(
        "Resolved tape devices: drive_nst=%s (%s) drive_sg=%s (%s) changer_sg=%s (%s)",
        drive_nst,
        drive_nst_source,
        drive_sg,
        drive_sg_source,
        changer_sg,
        changer_source,
    )
    return devices, health


def choose_devices_from_lsscsi_output(output: str) -> Tuple[Optional[str], Optional[str], Dict[str, List[Dict[str, str]]]]:
    drives, changers = parse_lsscsi_output(output)
    drive_path = drives[0].path if drives else None
    changer_path = changers[0].path if changers else None
    return drive_path, changer_path, {
        'drives': [drive.__dict__ for drive in drives],
        'changers': [changer.__dict__ for changer in changers],
    }
