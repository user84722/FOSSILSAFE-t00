# Security Notes

## Threat Model
FossilSafe is designed for LAN and VPN deployments where you control network access. It is not recommended to expose the UI or API directly to the public internet without a VPN or a hardened reverse proxy.

## API Key Authentication
All `/api/*` and `/socket.io/*` endpoints require an API key by default. The key is stored in `/etc/fossilsafe/config.json`.

## Multi-User and Access Control
FossilSafe includes granular permission roles (Admin, Operator, Viewer) and optional TOTP Two-Factor Authentication (2FA) for the web interface.

## Encryption at Rest
FossilSafe encrypts stored source credentials using a local symmetric key located at `/var/lib/fossilsafe/credential_key.bin`. Keep this key backed up securely if you need to restore your configuration on another host. If the key is lost, encrypted source secrets cannot be decrypted.

Zero-Knowledge encryption jobs handle passphrases exclusively in volatile memory (RAM) and are never written to disk. Notice: If you lose your passphrase for an encrypted tape, data recovery is mathematically impossible.

## Reporting Vulnerabilities
If you discover a security vulnerability, please practice responsible disclosure. Do not open a public issue. Instead, please report it privately to the maintainer so it can be addressed safely. You can usually find contact information on the project's GitHub profile. (Details on exact reporting addresses will be updated soon).

Thank you for helping keep the project safe!
