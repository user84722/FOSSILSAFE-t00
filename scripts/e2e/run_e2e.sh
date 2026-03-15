#!/usr/bin/env bash
# FossilSafe E2E Test Runner
# Usage: ./run_e2e.sh --mode ui|headless --hardware real|sim --fast|--full

set -euo pipefail

# Default configuration
MODE="ui"
HARDWARE="sim"
SUITE="fast"
ARTIFACTS_DIR="/tmp/e2e-artifacts"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Colors
COLOR_RESET=$'\033[0m'
COLOR_BLUE=$'\033[0;34m'
COLOR_GREEN=$'\033[0;32m'
COLOR_YELLOW=$'\033[0;33m'
COLOR_RED=$'\033[0;31m'

info() {
  printf "%b[INFO]%b %s\n" "${COLOR_BLUE}" "${COLOR_RESET}" "$1"
}

ok() {
  printf "%b[OK]%b %s\n" "${COLOR_GREEN}" "${COLOR_RESET}" "$1"
}

warn() {
  printf "%b[WARN]%b %s\n" "${COLOR_YELLOW}" "${COLOR_RESET}" "$1"
}

err() {
  printf "%b[ERROR]%b %s\n" "${COLOR_RED}" "${COLOR_RESET}" "$1"
}

# Parse CLI flags
while [[ $# -gt 0 ]]; do
  case $1 in
    --mode)
      MODE="$2"
      if [[ "$MODE" != "ui" && "$MODE" != "headless" ]]; then
        err "Invalid mode: $MODE. Must be 'ui' or 'headless'"
        exit 1
      fi
      shift 2
      ;;
    --hardware)
      HARDWARE="$2"
      if [[ "$HARDWARE" != "real" && "$HARDWARE" != "sim" ]]; then
        err "Invalid hardware: $HARDWARE. Must be 'real' or 'sim'"
        exit 1
      fi
      shift 2
      ;;
    --fast)
      SUITE="fast"
      shift
      ;;
    --full)
      SUITE="full"
      shift
      ;;
    --help|-h)
      cat <<EOF
FossilSafe E2E Test Runner

Usage: $0 [OPTIONS]

Options:
  --mode ui|headless    Installation mode (default: ui)
  --hardware real|sim   Hardware environment (default: sim)
  --fast                Run smoke subset (Phases 1-3)
  --full                Run complete test suite (all phases)
  --help, -h            Show this help message

Examples:
  $0 --mode ui --hardware sim --fast
  $0 --mode headless --hardware sim --full
  $0 --mode ui --hardware real --full

Artifacts are collected in: $ARTIFACTS_DIR
EOF
      exit 0
      ;;
    *)
      err "Unknown flag: $1"
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done

info "E2E Test Configuration:"
info "  Mode: $MODE"
info "  Hardware: $HARDWARE"
info "  Suite: $SUITE"
info "  Artifacts: $ARTIFACTS_DIR"
echo ""

# Create artifacts directory
mkdir -p "$ARTIFACTS_DIR"

# Check prerequisites
check_prerequisites() {
  info "Checking prerequisites..."
  
  local missing=()
  
  if ! command -v python3 &>/dev/null; then
    missing+=("python3")
  fi
  
  if ! command -v pytest &>/dev/null; then
    missing+=("pytest")
  fi
  
  if [[ "$SUITE" == "full" ]] && ! command -v playwright &>/dev/null; then
    missing+=("playwright")
  fi
  
  if [[ ${#missing[@]} -gt 0 ]]; then
    err "Missing prerequisites: ${missing[*]}"
    err "Install with: pip3 install pytest playwright pytest-playwright python-socketio requests"
    err "Then run: playwright install chromium"
    exit 1
  fi
  
  ok "Prerequisites check passed"
}

# Provision VM (Docker or Vagrant)
provision_vm() {
  info "Provisioning test environment..."
  
  # For now, assume running on host system
  # TODO: Add Docker/Vagrant provisioning for full isolation
  
  # Check if FossilSafe is already installed
  if systemctl is-active fossilsafe &>/dev/null; then
    warn "FossilSafe service already running"
    warn "For clean E2E tests, consider running on fresh VM"
  fi
  
  ok "Environment ready"
}

# Run test suite
run_tests() {
  info "Running high-density E2E test suite..."
  
  local pytest_args=("-v" "--tb=short")
  local test_files=(
    "${SCRIPT_DIR}/test_system.py"
    "${SCRIPT_DIR}/test_operations.py"
    "${SCRIPT_DIR}/test_resilience.py"
  )
  
  # Add artifacts directory for Playwright
  export PLAYWRIGHT_SCREENSHOTS_DIR="$ARTIFACTS_DIR/screenshots"
  export PLAYWRIGHT_VIDEOS_DIR="$ARTIFACTS_DIR/videos"
  mkdir -p "$PLAYWRIGHT_SCREENSHOTS_DIR" "$PLAYWRIGHT_VIDEOS_DIR"
  
  # Run pytest
  local exit_code=0
  for test_file in "${test_files[@]}"; do
    if [[ -f "$test_file" ]]; then
      info "Running Suite: $(basename "$test_file")"
      if ! pytest "${pytest_args[@]}" "$test_file"; then
        err "Suite failed: $(basename "$test_file")"
        exit_code=1
      fi
    else
      warn "Suite file missing: $test_file (skipping)"
    fi
  done
  
  return $exit_code
}

# Collect artifacts
collect_artifacts() {
  info "Collecting test artifacts..."
  
  # Copy logs
  if [[ -d /var/log/fossilsafe ]]; then
    cp -r /var/log/fossilsafe "$ARTIFACTS_DIR/" 2>/dev/null || true
  fi
  
  # Copy systemd journal
  if command -v journalctl &>/dev/null; then
    journalctl -u fossilsafe -n 500 --no-pager > "$ARTIFACTS_DIR/fossilsafe.journal" 2>/dev/null || true
  fi
  
  # Copy config (sanitized)
  if [[ -f /etc/fossilsafe/config.json ]]; then
    # Remove API key before copying
    jq 'del(.api_key)' /etc/fossilsafe/config.json > "$ARTIFACTS_DIR/config.json" 2>/dev/null || true
  fi
  
  # List artifacts
  info "Artifacts collected in: $ARTIFACTS_DIR"
  ls -lh "$ARTIFACTS_DIR" 2>/dev/null || true
}

# Cleanup
cleanup() {
  local exit_code=$?
  
  if [[ $exit_code -ne 0 ]]; then
    err "E2E tests FAILED (exit code: $exit_code)"
    collect_artifacts
  else
    ok "E2E tests PASSED"
  fi
  
  exit $exit_code
}

trap cleanup EXIT

# Main execution
main() {
  check_prerequisites
  provision_vm
  
  if run_tests; then
    ok "All tests passed!"
    collect_artifacts
    return 0
  else
    err "Some tests failed"
    collect_artifacts
    return 1
  fi
}

main
