# FossilSafe Backend

This directory contains the Flask/Socket.IO backend services for FossilSafe.

## Architecture

### Core Modules

| Module | Description |
|--------|-------------|
| `lto_backend_main.py` | Main Flask application, route registration, app factory |
| `database.py` | SQLite database layer, migrations, all data access |
| `backup_engine.py` | Backup job execution, tar/compression pipeline |
| `encryption.py` | GPG-AES256 tape encryption, key management |
| `kms_provider.py` | External KMS abstraction (Local, Vault, AWS KMS) |
| `auth.py` | Authentication, RBAC, 2FA, session management |
| `log_manager.py` | Structured logging, cleanup, WebSocket emission |
| `catalog_security.py` | Ed25519 catalog signing/verification |
| `catalog_rebuild.py` | Disaster recovery catalog reconstruction |
| `external_catalog_backup.py` | External catalog export/import |

### Routes (API Endpoints)

| Route Module | Endpoints |
|--------------|-----------|
| `routes/auth.py` | `/api/auth/*` - Login, logout, 2FA, sessions |
| `routes/tapes.py` | `/api/tapes/*` - Tape operations, library control |
| `routes/jobs.py` | `/api/jobs/*` - Backup job management |
| `routes/files.py` | `/api/files/*` - File catalog, search |
| `routes/restore.py` | `/api/restore/*` - Restoration workflow |
| `routes/recovery.py` | `/api/recovery/*` - Catalog recovery wizard |
| `routes/audit.py` | `/api/audit/*` - Immutable audit log |
| `routes/logs.py` | `/api/logs/*` - Log viewing, cleanup |
| `routes/system.py` | `/api/system/*` - Metrics, diagnostics, Prometheus |
| `routes/external_catalog.py` | `/api/external-catalog/*` - Catalog backup |

### Services

| Service | Description |
|---------|-------------|
| `services/tape_service.py` | Tape drive/changer operations |
| `services/job_service.py` | Job queue, scheduling, execution |
| `services/metrics_service.py` | System metrics, Prometheus export |
| `services/license_service.py` | License validation, tier features |
| `services/diagnostic_service.py` | Hardware diagnostics, troubleshooting |

## Development

### Run the API locally

```bash
FOSSILSAFE_REQUIRE_API_KEY=false python lto_backend_main.py --host 0.0.0.0 --port 5000
```

### Run unit tests

```bash
python -m unittest discover -s tests
```

## Configuration

### Command-line flags

- `--host` (default `0.0.0.0`)
- `--port` (default `5000`)
- `--db-path` (default `${FOSSILSAFE_DATA_DIR}/lto_backup.db`)
- `--debug`

### Environment variables

- API key comes from `/etc/fossilsafe/config.json` (key: `api_key`).
- `FOSSILSAFE_REQUIRE_API_KEY`: set to `false` to disable API key enforcement (dev only).
- `FOSSILSAFE_CORS_ORIGINS`: comma-separated allowlist for `/api/*` CORS.
- `FOSSILSAFE_CONFIG_PATH`: JSON config path (used by installer).
- `FOSSILSAFE_DATA_DIR`: base directory for FossilSafe data (default `/var/lib/fossilsafe`).
- `FOSSILSAFE_DB_PATH`: override database path.
- `FOSSILSAFE_TIER`: Environment tier (`community`, etc).

### KMS Provider Configuration

External key management can be configured in `/etc/fossilsafe/config.json`:

```json
{
  "kms": {
    "type": "vault",
    "vault_addr": "https://vault.example.com:8200",
    "vault_token": "s.xxxxx",
    "mount_path": "secret"
  }
}
```

Supported providers: `local` (default), `vault` (HashiCorp Vault).

## API Features

### Prometheus Metrics

```
GET /api/system/metrics/prometheus
```

Returns metrics in OpenMetrics format for Prometheus scraping:
- `fossilsafe_tapes_total{status}`
- `fossilsafe_jobs_total{status}`
- `fossilsafe_data_written_bytes`
- `fossilsafe_api_latency_seconds`

### Immutable Audit Log

```
GET /api/audit          # View audit log (admin)
GET /api/audit/verify   # Verify hash chain integrity
GET /api/audit/export   # Export with signature
```

Audit entries are cryptographically chained (SHA-256) for tamper detection.

## Notes

- Sources are stored encrypted (where applicable) and referenced by `source_id`.
- Scheduling uses a 6-field cron expression (`sec min hour day month day_of_week`).
- Production installs front the backend with nginx (see `scripts/install.sh`).
- Configuration merges non-destructively on upgrades (new keys added, existing preserved).

