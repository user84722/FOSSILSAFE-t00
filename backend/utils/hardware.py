import subprocess
import logging
import re
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# TapeAlert codes (1-indexed) as defined in LTO specifications.
# This is a subset of the most critical alerts.
TAPE_ALERT_MAPPING = {
    1: {"name": "Read Warning", "severity": "warning", "desc": "The drive is having problems reading from the tape."},
    2: {"name": "Write Warning", "severity": "warning", "desc": "The drive is having problems writing to the tape."},
    3: {"name": "Hard Error", "severity": "critical", "desc": "The operation has failed and cannot be retried."},
    4: {"name": "Media Error", "severity": "critical", "desc": "The tape media is faulty or worn out."},
    5: {"name": "Read Failure", "severity": "critical", "desc": "The drive cannot read data from the tape."},
    6: {"name": "Write Failure", "severity": "critical", "desc": "The drive cannot write data to the tape."},
    7: {"name": "Media Life", "severity": "warning", "desc": "The tape has reached the end of its calculated life."},
    8: {"name": "Not Data Grade", "severity": "warning", "desc": "The tape is not a data grade tape."},
    9: {"name": "Write Protect", "severity": "info", "desc": "The tape is write-protected."},
    12: {"name": "Unsupported Format", "severity": "critical", "desc": "The tape format is not supported by this drive."},
    14: {"name": "Unrecoverable Snapped Tape", "severity": "critical", "desc": "The tape has snapped and cannot be recovered."},
    20: {"name": "Clean Now", "severity": "critical", "desc": "The drive needs cleaning immediately."},
    21: {"name": "Clean Periodic", "severity": "warning", "desc": "The drive will need cleaning soon."},
    30: {"name": "Hardware Configuration", "severity": "critical", "desc": "Tape drive hardware configuration changed/faulty."},
    31: {"name": "Hardware Failure", "severity": "critical", "desc": "The tape drive has a hardware failure."},
    32: {"name": "Interface Failure", "severity": "critical", "desc": "The host interface has failed."},
}

def get_tape_alerts(device_sg: str) -> List[Dict]:
    """
    Extract TapeAlerts from an LTO drive using sg_logs.
    :param device_sg: The scsi generic device path (e.g., /dev/sg3)
    :return: List of active alerts with name, severity, and description.
    """
    if not device_sg:
        return []

    try:
        # -p 0x2e is the TapeAlert log page
        result = subprocess.run(
            ["sg_logs", "-p", "0x2e", device_sg],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode != 0:
            logger.error(f"sg_logs failed for {device_sg}: {result.stderr}")
            return []

        return parse_tape_alerts(result.stdout)
    except Exception as e:
        logger.error(f"Failed to fetch TapeAlerts for {device_sg}: {e}")
        return []

def parse_tape_alerts(output: str) -> List[Dict]:
    """
    Parse the output of sg_logs -p 0x2e.
    Expected format: "TapeAlert [0x1]: Read warning" (or similar)
    """
    alerts = []
    # Regex to find [0xXX] codes
    # sg_logs output varies but usually includes the hex code in brackets
    matches = re.finditer(r"\[0x([0-9a-fA-F]+)\]", output)
    
    seen_codes = set()
    for match in matches:
        try:
            code = int(match.group(1), 16)
            if code == 0 or code in seen_codes:
                continue
            
            seen_codes.add(code)
            alert_info = TAPE_ALERT_MAPPING.get(code, {
                "name": f"Unknown Alert ({code})",
                "severity": "warning",
                "desc": "An undocumented TapeAlert was reported by the drive."
            })
            alerts.append({
                "code": code,
                **alert_info
            })
        except ValueError:
            continue
            
    return alerts
