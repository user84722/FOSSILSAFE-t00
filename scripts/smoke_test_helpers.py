#!/usr/bin/env python3
import argparse
import json
import os
import shutil
from dataclasses import dataclass
from typing import List, Optional, Tuple


EXIT_BACKEND_UNREACHABLE = 10
EXIT_UI_UNREACHABLE = 11
EXIT_AUTH_REJECTED = 12
EXIT_API_KEY_MISSING = 13
EXIT_WRONG_BASE_URL = 14
EXIT_UNEXPECTED = 99


@dataclass
class SmokeClassification:
    ok: bool
    reason: str
    exit_code: int


@dataclass
class ToolCheckResult:
    ok: bool
    missing_required: List[str]
    missing_optional: List[str]


def evaluate_tool_requirements(
    required: List[str],
    optional: Optional[List[str]] = None,
    strict_optional: bool = False,
) -> ToolCheckResult:
    optional = optional or []
    missing_required = [tool for tool in required if not shutil.which(tool)]
    missing_optional = [tool for tool in optional if not shutil.which(tool)]
    ok = not missing_required and (not strict_optional or not missing_optional)
    return ToolCheckResult(ok=ok, missing_required=missing_required, missing_optional=missing_optional)


def load_api_key(config_path: str, env_key: Optional[str], env_set: bool) -> Tuple[Optional[str], Optional[str]]:
    if env_set:
        if env_key and env_key.strip():
            return env_key.strip(), None
        return None, "API_KEY environment variable is set but empty."
    if not os.path.exists(config_path):
        return None, f"Config file not found at {config_path}."
    try:
        with open(config_path, "r") as handle:
            data = json.load(handle) or {}
    except json.JSONDecodeError:
        return None, f"Config file at {config_path} is not valid JSON."
    except OSError as exc:
        return None, f"Config file at {config_path} is not readable: {exc}"
    key = data.get("api_key") or data.get("API_KEY") or ""
    if not isinstance(key, str) or not key.strip():
        return None, "API key missing in config."
    return key.strip(), None


def classify_status_code(status_code: int, target: str) -> SmokeClassification:
    if status_code == 200:
        return SmokeClassification(ok=True, reason="ok", exit_code=0)
    if status_code in (401, 403):
        return SmokeClassification(ok=False, reason="auth_rejected", exit_code=EXIT_AUTH_REJECTED)
    if status_code == 404:
        return SmokeClassification(ok=False, reason="wrong_base_url", exit_code=EXIT_WRONG_BASE_URL)
    if status_code == 0:
        if target == "ui":
            return SmokeClassification(ok=False, reason="ui_unreachable", exit_code=EXIT_UI_UNREACHABLE)
        return SmokeClassification(ok=False, reason="backend_unreachable", exit_code=EXIT_BACKEND_UNREACHABLE)
    return SmokeClassification(ok=False, reason="unexpected_status", exit_code=EXIT_UNEXPECTED)


def _cmd_load_api_key(args: argparse.Namespace) -> int:
    key, error = load_api_key(args.config, args.env, bool(args.env_set))
    print(key or "")
    print(error or "")
    return 0


def _cmd_classify(args: argparse.Namespace) -> int:
    try:
        status = int(args.status)
    except (TypeError, ValueError):
        status = 0
    result = classify_status_code(status, args.target)
    print(f"{int(result.ok)}|{result.exit_code}|{result.reason}")
    return 0


def _cmd_check_tools(args: argparse.Namespace) -> int:
    result = evaluate_tool_requirements(
        required=args.required,
        optional=args.optional,
        strict_optional=bool(args.strict_optional),
    )
    missing_required = ",".join(result.missing_required)
    missing_optional = ",".join(result.missing_optional)
    print(f"{int(result.ok)}|{missing_required}|{missing_optional}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    load_key = subparsers.add_parser("load-api-key")
    load_key.add_argument("--config", required=True)
    load_key.add_argument("--env")
    load_key.add_argument("--env-set", type=int, default=0)
    load_key.set_defaults(func=_cmd_load_api_key)

    classify = subparsers.add_parser("classify")
    classify.add_argument("--status", required=True)
    classify.add_argument("--target", choices=["backend", "ui"], required=True)
    classify.set_defaults(func=_cmd_classify)

    tools = subparsers.add_parser("check-tools")
    tools.add_argument("--required", nargs="*", default=[])
    tools.add_argument("--optional", nargs="*", default=[])
    tools.add_argument("--strict-optional", type=int, default=0)
    tools.set_defaults(func=_cmd_check_tools)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
