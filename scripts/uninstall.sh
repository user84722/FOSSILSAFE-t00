#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${FOSSILSAFE_INSTALL_DIR:-/opt/fossilsafe}"
CONFIG_PATH="${FOSSILSAFE_CONFIG_PATH:-/etc/fossilsafe/config.json}"
DATA_DIR="${FOSSILSAFE_DATA_DIR:-/var/lib/fossilsafe}"
SERVICE_FILE="/etc/systemd/system/fossilsafe.service"

confirm=0
for arg in "$@"; do
  case "${arg}" in
    --yes|-y)
      confirm=1
      ;;
  esac
done

if [[ "${confirm}" -ne 1 ]]; then
  echo "This will remove FossilSafe data and configuration:"
  echo "  - ${INSTALL_DIR}"
  echo "  - ${CONFIG_PATH}"
  echo "  - ${DATA_DIR}"
  echo "  - ${SERVICE_FILE}"
  read -r -p "Continue? [y/N]: " reply
  if [[ ! "${reply:-}" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
  fi
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl stop fossilsafe.service >/dev/null 2>&1 || true
  systemctl disable fossilsafe.service >/dev/null 2>&1 || true
  systemctl daemon-reload >/dev/null 2>&1 || true
fi

rm -f "${SERVICE_FILE}"
rm -rf "${INSTALL_DIR}"
rm -f "${CONFIG_PATH}"
rm -rf "${DATA_DIR}"

echo "FossilSafe removed."
