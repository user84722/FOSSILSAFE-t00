from typing import Dict, List, Tuple

from backend.tape.devices import (
    choose_devices_from_lsscsi_output as _choose_devices_from_lsscsi_output,
    parse_lsscsi_output as _parse_lsscsi_output,
)

__all__ = ["parse_lsscsi_output", "choose_devices_from_lsscsi_output"]


def _compat_parse(output: str) -> Tuple[List[Dict], List[Dict]]:
    drives, changers = _parse_lsscsi_output(output)
    return [drive.__dict__ for drive in drives], [changer.__dict__ for changer in changers]


def parse_lsscsi_output(output: str) -> Tuple[List[Dict], List[Dict]]:
    return _compat_parse(output)


def choose_devices_from_lsscsi_output(output: str) -> Tuple[str, str, Dict[str, List[Dict]]]:
    drive_path, changer_path, details = _choose_devices_from_lsscsi_output(output)
    return drive_path, changer_path, details
