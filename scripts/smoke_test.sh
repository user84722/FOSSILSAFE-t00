#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${FOSSILSAFE_CONFIG_PATH:-/etc/fossilsafe/config.json}"
BACKEND_PORT="${BACKEND_PORT:-5000}"
UI_PORT="${UI_PORT:-8080}"
HEADLESS="${HEADLESS:-0}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CURL_BIN="${CURL_BIN:-curl}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EXIT_BACKEND_UNREACHABLE=10
EXIT_UI_UNREACHABLE=11
EXIT_AUTH_REJECTED=12
EXIT_API_KEY_MISSING=13
EXIT_WRONG_BASE_URL=14
EXIT_UNEXPECTED=99

echo "Smoke tests: parsing JSON payloads with python json.load."

fail_with() {
  local exit_code="$1"
  local reason="$2"
  local detail="$3"
  echo "Smoke test failed (${reason}) [exit ${exit_code}]: ${detail}" >&2
  exit "${exit_code}"
}

warn_or_fail() {
  local reason="$1"
  local detail="$2"
  if [[ "${FOSSILSAFE_STRICT_INSTALL_CHECKS:-0}" == "1" ]]; then
    fail_with "${EXIT_UNEXPECTED}" "${reason}" "${detail}"
  fi
  echo "Warning: ${detail}" >&2
}

sleep_backoff() {
  local attempt="$1"
  local delay
  if [[ "${attempt}" -le 1 ]]; then
    delay=1
  elif [[ "${attempt}" -le 3 ]]; then
    delay=2
  elif [[ "${attempt}" -le 6 ]]; then
    delay=3
  else
    delay=5
  fi
  sleep "${delay}"
}

ltfs_required=(mkltfs ltfs)
ltfs_optional=(ltfsck)
ltfs_check_result="$("${PYTHON_BIN}" "${SCRIPT_DIR}/smoke_test_helpers.py" check-tools \
  --required "${ltfs_required[@]}" \
  --optional "${ltfs_optional[@]}" \
  --strict-optional "${FOSSILSAFE_STRICT_LTFS_TOOLS:-0}")"
IFS='|' read -r ltfs_ok ltfs_missing_required ltfs_missing_optional <<<"${ltfs_check_result}"
if [[ "${ltfs_ok}" != "1" ]]; then
  fail_with "${EXIT_UNEXPECTED}" "missing ltfs tools" "LTFS missing — required for core operation. Missing: ${ltfs_missing_required}. Install vendor LTFS packages or build from source."
fi
if [[ -n "${ltfs_missing_optional}" ]]; then
  echo "Warning: optional LTFS tools missing: ${ltfs_missing_optional}. Some verification checks will be skipped." >&2
fi

changer_detected=0
config_changer_path="$("${PYTHON_BIN}" - <<'PY'
import json
import os
import sys

path = os.environ.get("FOSSILSAFE_CONFIG_PATH", "/etc/fossilsafe/config.json")
if not os.path.exists(path):
    sys.exit(0)
try:
    with open(path, "r") as handle:
        data = json.load(handle) or {}
    changer = data.get("changer_path")
    if isinstance(changer, str) and changer.strip():
        print(changer.strip())
except Exception:
    pass
PY
)"
if [[ -n "${config_changer_path}" ]]; then
  changer_detected=1
fi
if command -v lsscsi >/dev/null 2>&1; then
  if lsscsi -g 2>/dev/null | grep -qiE 'mediumx|medium changer'; then
    changer_detected=1
  fi
fi
if [[ "${changer_detected}" -eq 1 ]]; then
  if ! command -v mtx >/dev/null 2>&1 && [[ ! -x /usr/sbin/mtx ]]; then
    warn_or_fail "missing mtx" "mtx not found; install package 'mtx' (binary at /usr/sbin/mtx)"
  fi
fi

if [[ ! -e /dev/fuse ]]; then
  warn_or_fail "missing fuse" "/dev/fuse missing — FUSE kernel support is required."
fi

if ! command -v fusermount >/dev/null 2>&1 && ! command -v fusermount3 >/dev/null 2>&1; then
  warn_or_fail "missing fusermount" "fusermount or fusermount3 missing — install fuse/fuse3 packages that provide the fusermount binary."
fi

status_code() {
  local url="$1"
  shift
  local output_file
  output_file="$(mktemp)"
  local code
  code="$("${CURL_BIN}" -sS --max-time 5 -o "${output_file}" -w "%{http_code}" "$@" "${url}" || echo "000")"
  echo "${code}" "${output_file}"
}

backend_health_ok=0
backend_health_status="000"
backend_health_reason=""
backend_health_file=""
for attempt in {1..15}; do
  read -r backend_health_status backend_health_file < <(status_code "http://127.0.0.1:${BACKEND_PORT}/api/healthz")
  backend_health_status="${backend_health_status:-000}"
  backend_health_result="$("${PYTHON_BIN}" "${SCRIPT_DIR}/smoke_test_helpers.py" classify \
    --status "${backend_health_status}" --target backend)"
  IFS='|' read -r backend_health_ok backend_health_exit backend_health_reason <<<"${backend_health_result}"
  if [[ "${backend_health_ok}" == "1" ]]; then
    break
  fi
  echo "Waiting for backend health (attempt ${attempt}/15)..." >&2
  sleep_backoff "${attempt}"
done
if [[ "${backend_health_ok}" != "1" ]]; then
  case "${backend_health_reason}" in
    auth_rejected)
      fail_with "${EXIT_AUTH_REJECTED}" "auth rejected" "Backend /api/healthz returned ${backend_health_status}."
      ;;
    wrong_base_url)
      fail_with "${EXIT_WRONG_BASE_URL}" "wrong base URL/proxy path" "Backend /api/healthz returned 404."
      ;;
    backend_unreachable)
      fail_with "${EXIT_BACKEND_UNREACHABLE}" "backend unreachable" "Backend /api/healthz unreachable (curl code ${backend_health_status})."
      ;;
    *)
      fail_with "${EXIT_UNEXPECTED}" "unexpected status" "Backend /api/healthz returned ${backend_health_status}."
      ;;
  esac
fi

api_key_env_set=0
api_key_env_value=""
if [[ -v API_KEY ]]; then
  api_key_env_set=1
  api_key_env_value="${API_KEY}"
fi
readarray -t api_key_lines < <("${PYTHON_BIN}" "${SCRIPT_DIR}/smoke_test_helpers.py" load-api-key \
  --config "${CONFIG_PATH}" --env "${api_key_env_value}" --env-set "${api_key_env_set}")
API_KEY="${api_key_lines[0]:-}"
API_KEY_ERROR="${api_key_lines[1]:-}"
export API_KEY BACKEND_PORT UI_PORT HEADLESS
socketio_base_url="http://127.0.0.1:${BACKEND_PORT}"
if [[ "${HEADLESS}" -eq 0 ]]; then
  read -r socketio_probe_status _ < <(status_code "http://127.0.0.1:${UI_PORT}/socket.io/?EIO=4&transport=polling" --max-time 5)
  if [[ "${socketio_probe_status}" != "404" && "${socketio_probe_status}" != "000" ]]; then
    socketio_base_url="http://127.0.0.1:${UI_PORT}"
  fi
fi
export SOCKETIO_BASE_URL="${socketio_base_url}"
api_header=()
if [[ -n "${API_KEY}" ]]; then
  api_header=(-H "X-API-Key: ${API_KEY}")
fi

status_no_key="000"
status_no_key_file=""
for attempt in {1..10}; do
  read -r status_no_key status_no_key_file < <(status_code "http://127.0.0.1:${BACKEND_PORT}/api/status")
  status_no_key="${status_no_key:-000}"
  if [[ "${status_no_key}" != "000" ]]; then
    break
  fi
  echo "Waiting for backend /api/status (attempt ${attempt}/10)..." >&2
  sleep_backoff "${attempt}"
done
api_key_required=0
case "${status_no_key}" in
  200)
    echo "Backend API key enforcement disabled; /api/status accessible without key."
    ;;
  401|403)
    api_key_required=1
    ;;
  404)
    fail_with "${EXIT_WRONG_BASE_URL}" "wrong base URL/proxy path" "Backend /api/status returned 404."
    ;;
  500)
    api_key_required=1
    ;;
  000)
    fail_with "${EXIT_BACKEND_UNREACHABLE}" "backend unreachable" "Backend /api/status unreachable."
    ;;
  *)
    fail_with "${EXIT_UNEXPECTED}" "unexpected status" "Backend /api/status returned ${status_no_key}."
    ;;
esac

status_with_key=""
if [[ -n "${API_KEY}" ]]; then
  status_with_key="000"
  status_with_key_file=""
  for attempt in {1..10}; do
    read -r status_with_key status_with_key_file < <(status_code "http://127.0.0.1:${BACKEND_PORT}/api/status" \
      -H "X-API-Key: ${API_KEY}")
    status_with_key="${status_with_key:-000}"
    if [[ "${status_with_key}" != "000" ]]; then
      break
    fi
    echo "Waiting for backend /api/status with API key (attempt ${attempt}/10)..." >&2
    sleep_backoff "${attempt}"
  done
  if [[ "${status_with_key}" != "200" ]]; then
    if status_payload="$("${CURL_BIN}" -fsS --max-time 5 -H "X-API-Key: ${API_KEY}" "http://127.0.0.1:${BACKEND_PORT}/api/status")"; then
      echo "Backend status payload:" >&2
      echo "${status_payload}" >&2
    fi
    if [[ "${status_with_key}" == "401" || "${status_with_key}" == "403" ]]; then
      fail_with "${EXIT_AUTH_REJECTED}" "auth rejected" "Backend /api/status rejected API key (HTTP ${status_with_key})."
    fi
    if [[ "${status_with_key}" == "404" ]]; then
      fail_with "${EXIT_WRONG_BASE_URL}" "wrong base URL/proxy path" "Backend /api/status returned 404."
    fi
    fail_with "${EXIT_UNEXPECTED}" "unexpected status" "Backend /api/status returned ${status_with_key}."
  fi
elif [[ "${api_key_required}" -eq 1 ]]; then
  if [[ "${FOSSILSAFE_STRICT_API_KEY:-0}" == "1" ]]; then
    fail_with "${EXIT_API_KEY_MISSING}" "api key missing/misconfigured" "${API_KEY_ERROR:-API key required but missing.}"
  fi
  echo "Warning: API key required to access /api/status; configure API key to enable authenticated checks." >&2
fi

if [[ -n "${API_KEY}" ]]; then
  status_payload="$("${CURL_BIN}" -fsS --max-time 5 "${api_header[@]}" "http://127.0.0.1:${BACKEND_PORT}/api/status")" \
    || fail_with "${EXIT_UNEXPECTED}" "backend unreachable" "Failed to query /api/status with API key"
else
  status_payload="$("${CURL_BIN}" -fsS --max-time 5 "http://127.0.0.1:${BACKEND_PORT}/api/status")" \
    || fail_with "${EXIT_UNEXPECTED}" "backend unreachable" "Failed to query /api/status without API key"
fi
readarray -t library_lines < <("${PYTHON_BIN}" -c '
import json
import sys

data = json.loads(sys.stdin.read())
online = data.get("library_online")
error = data.get("library_error") or data.get("library_info", {}).get("error")
print("online" if online else "offline")
if error:
    print(error)
' <<<"${status_payload}")
library_status="${library_lines[0]:-unknown}"
library_error="${library_lines[1]:-}"
if [[ "${changer_detected}" -eq 1 && "${library_status}" == "offline" ]]; then
  echo "Backend status payload:" >&2
  echo "${status_payload}" >&2
  if [[ -n "${library_error}" ]]; then
    if echo "${library_error}" | grep -qi "permission denied"; then
      fail_with "${EXIT_UNEXPECTED}" "changer permission denied" "Changer permission denied on ${config_changer_path:-/dev/sg1}; run installer fix or apply udev rules; user ${SERVICE_USER:-fossilsafe} must be in fossilsafe-tape group."
    fi
  fi
  echo "Library offline; attempting recovery + force unload..." >&2
  "${CURL_BIN}" -fsS --max-time 15 \
    "${api_header[@]}" \
    -H "Content-Type: application/json" \
    -d '{"force_unload": true}' \
    "http://127.0.0.1:${BACKEND_PORT}/api/library/recover" >/tmp/fossilsafe_recover.out 2>/dev/null || true

  status_payload="$("${CURL_BIN}" -fsS --max-time 5 "${api_header[@]}" "http://127.0.0.1:${BACKEND_PORT}/api/status")" \
    || fail_with "${EXIT_UNEXPECTED}" "backend unreachable" "Failed to query /api/status after recovery"
  readarray -t library_lines < <("${PYTHON_BIN}" -c '
import json
import sys

data = json.loads(sys.stdin.read())
online = data.get("library_online")
error = data.get("library_error") or data.get("library_info", {}).get("error")
print("online" if online else "offline")
if error:
    print(error)
' <<<"${status_payload}")
  library_status="${library_lines[0]:-unknown}"
  library_error="${library_lines[1]:-}"

  if [[ "${library_status}" == "offline" ]]; then
    echo "Backend status payload after recovery:" >&2
    echo "${status_payload}" >&2
    if [[ -n "${library_error}" ]]; then
      if echo "${library_error}" | grep -qi "permission denied"; then
        fail_with "${EXIT_UNEXPECTED}" "changer permission denied" "Changer permission denied on ${config_changer_path:-/dev/sg1}; run installer fix or apply udev rules; user ${SERVICE_USER:-fossilsafe} must be in fossilsafe-tape group."
      fi
    fi
    if [[ "${FOSSILSAFE_STRICT_LIBRARY_CHECK:-0}" == "1" ]]; then
      if [[ -n "${library_error}" ]]; then
        fail_with "${EXIT_UNEXPECTED}" "changer offline" "Changer offline: ${library_error}"
      fi
      fail_with "${EXIT_UNEXPECTED}" "changer offline" "Changer offline: library_online false"
    fi
    echo "Warning: library still offline after recovery; continuing install." >&2
    if [[ -n "${library_error}" ]]; then
      echo "Library error: ${library_error}" >&2
    fi
  fi
fi

socketio_ok=1
if [[ -n "${API_KEY}" ]]; then
if ! "${PYTHON_BIN}" - <<'PY'
import os
import socketio

api_key = os.environ["API_KEY"]
base_url = os.environ.get("SOCKETIO_BASE_URL") or f"http://127.0.0.1:{os.environ.get('BACKEND_PORT', '5000')}"

def unauthenticated_rejected() -> bool:
    sio = socketio.Client(reconnection=False, logger=False, engineio_logger=False)
    try:
        try:
            connect_with_fallback(sio, base_url, wait_timeout=5)
        except Exception:
            return True
        try:
            response = sio.call("auth_ping", timeout=2)
            if isinstance(response, dict) and response.get("ok") is True:
                return False
            return True
        except Exception:
            return True
    finally:
        if sio.connected:
            sio.disconnect()

def authenticated_allowed() -> bool:
    sio = socketio.Client(reconnection=False, logger=False, engineio_logger=False)
    try:
        connect_with_fallback(sio, base_url, wait_timeout=5, auth={"api_key": api_key})
        response = sio.call("auth_ping", timeout=3)
        return isinstance(response, dict) and response.get("ok") is True
    finally:
        if sio.connected:
            sio.disconnect()

def connect_with_fallback(sio_client, url, wait_timeout=5, auth=None):
    last_error = None
    for transports in (["websocket"], ["polling"]):
        try:
            sio_client.connect(url, transports=transports, wait_timeout=wait_timeout, auth=auth)
            return
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error

if not unauthenticated_rejected():
    raise SystemExit("Unauthenticated Socket.IO request unexpectedly succeeded")
if not authenticated_allowed():
    raise SystemExit("Authenticated Socket.IO request failed")
print("Socket.IO auth checks passed.")
PY
then
  socketio_ok=0
fi
else
  echo "Skipping Socket.IO auth checks (API key not available)." >&2
fi
if [[ "${socketio_ok}" -eq 0 ]]; then
  if [[ "${backend_health_ok}" -eq 1 ]]; then
    echo "Socket.IO auth checks failed but backend health is OK." >&2
    echo "If the UI is not reverse-proxying Socket.IO, ensure clients use ${BACKEND_PORT} for Socket.IO." >&2
  else
    fail_with "${EXIT_UNEXPECTED}" "socket.io auth failed" "Socket.IO auth checks failed and backend health is not OK"
  fi
fi

if [[ "${HEADLESS}" -eq 0 ]]; then
  ui_api_status="000"
  ui_api_file=""
  for attempt in {1..10}; do
    read -r ui_api_status ui_api_file < <(status_code "http://127.0.0.1:${UI_PORT}/api/healthz")
    ui_api_status="${ui_api_status:-000}"
    if [[ "${ui_api_status}" != "000" ]]; then
      break
    fi
    echo "Waiting for UI /api/healthz (attempt ${attempt}/10)..." >&2
    sleep_backoff "${attempt}"
  done
  if [[ "${ui_api_status}" == "404" ]]; then
    read -r ui_root_status ui_root_file < <(status_code "http://127.0.0.1:${UI_PORT}/healthz")
    ui_root_status="${ui_root_status:-000}"
    if [[ "${ui_root_status}" == "200" ]]; then
      fail_with "${EXIT_WRONG_BASE_URL}" "wrong base URL/proxy path" "UI is not proxying /api (expected /api/healthz)."
    fi
  fi
  ui_api_result="$("${PYTHON_BIN}" "${SCRIPT_DIR}/smoke_test_helpers.py" classify \
    --status "${ui_api_status}" --target ui)"
  IFS='|' read -r ui_ok ui_exit ui_reason <<<"${ui_api_result}"
  if [[ "${ui_ok}" != "1" ]]; then
    case "${ui_reason}" in
      auth_rejected)
        fail_with "${EXIT_AUTH_REJECTED}" "auth rejected" "UI /api/healthz returned ${ui_api_status}."
        ;;
      wrong_base_url)
        fail_with "${EXIT_WRONG_BASE_URL}" "wrong base URL/proxy path" "UI /api/healthz returned 404."
        ;;
      ui_unreachable)
        fail_with "${EXIT_UI_UNREACHABLE}" "ui unreachable" "UI /api/healthz unreachable."
        ;;
      *)
        fail_with "${EXIT_UNEXPECTED}" "unexpected status" "UI /api/healthz returned ${ui_api_status}."
        ;;
    esac
  fi
fi

if ! compgen -G "/dev/nst*" >/dev/null && ! compgen -G "/dev/st*" >/dev/null; then
  echo "No tape devices detected. LTFS binaries + FUSE checked only."
fi

echo "Smoke tests completed successfully."
