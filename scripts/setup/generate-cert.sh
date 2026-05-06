#!/usr/bin/env bash
# Generate self-signed SSL certificates for FossilSafe
# Usage: ./generate-cert.sh [DOMAIN_NAME]

set -euo pipefail

DOMAIN="${1:-fossilsafe.local}"
CERT_DIR="/etc/fossilsafe/certs"
KEY_PATH="${CERT_DIR}/server.key"
CERT_PATH="${CERT_DIR}/server.crt"

echo "Generating self-signed SSL certificate for ${DOMAIN}..."

# Create cert directory
mkdir -p "${CERT_DIR}"
chmod 755 "${CERT_DIR}"

# Generate private key (4096-bit RSA)
openssl genrsa -out "${KEY_PATH}" 4096 2>/dev/null
chmod 400 "${KEY_PATH}"

# Generate self-signed certificate (365 days)
openssl req -new -x509 -key "${KEY_PATH}" -out "${CERT_PATH}" \
  -days 365 \
  -subj "/CN=${DOMAIN}/O=FossilSafe/C=US" \
  -addext "subjectAltName=DNS:${DOMAIN},DNS:localhost,IP:127.0.0.1" \
  2>/dev/null
chmod 444 "${CERT_PATH}"

echo "✓ Certificate generated: ${CERT_PATH}"
echo "✓ Private key: ${KEY_PATH}"
echo "Note: Self-signed certificate will show browser warnings. Use Let's Encrypt for production."
