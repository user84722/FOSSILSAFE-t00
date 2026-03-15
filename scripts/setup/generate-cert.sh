#!/bin/bash
# Generate self-signed TLS certificate for FossilSafe
# Run as root or with sudo

set -e

CERT_DIR="/etc/fossilsafe/certs"
HOSTNAME="${1:-fossilsafe.local}"
DAYS=365

echo "Generating self-signed TLS certificate for: $HOSTNAME"

# Create directory
mkdir -p "$CERT_DIR"
chmod 700 "$CERT_DIR"

# Generate private key and certificate
openssl req -x509 -nodes -days $DAYS \
    -newkey rsa:2048 \
    -keyout "$CERT_DIR/server.key" \
    -out "$CERT_DIR/server.crt" \
    -subj "/C=US/ST=State/L=City/O=FossilSafe/OU=Backup/CN=$HOSTNAME" \
    -addext "subjectAltName=DNS:$HOSTNAME,DNS:localhost,IP:127.0.0.1"

# Set permissions
chmod 600 "$CERT_DIR/server.key"
chmod 644 "$CERT_DIR/server.crt"

echo ""
echo "Certificate generated successfully!"
echo "  Certificate: $CERT_DIR/server.crt"
echo "  Private Key: $CERT_DIR/server.key"
echo ""
echo "To use with nginx, update /etc/nginx/sites-available/fossilsafe"
echo "Then run: sudo systemctl reload nginx"
