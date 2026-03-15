# Configuration

FossilSafe uses a single JSON file for canonical configuration:

- **Path:** `/etc/fossilsafe/config.json`

The installer always writes **lowercase** keys and removes any legacy uppercase keys.

## `/etc/fossilsafe/config.json` schema (canonical keys)

```json
{
  "api_key": "<required>",
  "backend_bind": "127.0.0.1",
  "backend_port": 5000,
  "ui_port": 8080,
  "headless": false,
  "db_path": "/var/lib/fossilsafe/lto_backup.db",
  "credential_key_path": "/var/lib/fossilsafe/credential_key.bin",
  "catalog_backup_dir": "/var/lib/fossilsafe/catalog-backups",
  "diagnostics_dir": "/var/lib/fossilsafe/diagnostics",
  "allowed_origins": ["https://example.local"],
  "tape": {
    "changer_device": "/dev/fossilsafe-changer",
    "drive_device": "/dev/nst0",
    "drive_devices": ["/dev/nst0"],
    "timeouts": {
      "mtx_status": 15,
      "mtx_load": 180,
      "mtx_unload": 180,
      "mtx_transfer": 180,
      "mtx_inventory": 300,
      "mt_status": 15,
      "mt_erase": 7200,
      "ltfs": 300,
      "mkltfs": 3600
    },
    "erase_timeout_seconds": 7200,
    "mtx_unload_order": "drive_slot",
    "scan_mode_default": "deep",
    "deep_scan_interval_seconds": 60
  },
  "hooks": {
    "enabled": false,
    "pre_backup": "/opt/fossilsafe/scripts/pre_backup.sh",
    "post_backup": "/opt/fossilsafe/scripts/post_backup.sh"
  },
  "license": {
    "key": "COMMUNITY-0000-0000-0000",
    "tier": "community"
  }
}
```

## Dynamic Settings (Database)

Tuning parameters for the **Streaming Pipeline** are stored in the database to allow real-time adjustment:

- **`max_queue_size_gb`**: Maximum size of the staging buffer.
- **`max_queue_files`**: Maximum number of files in the staging queue.
- **`producer_threads`**: Number of parallel threads fetching data from sources.

## Integration & Automation

### Consistency Hooks
Available in the **Settings > Automation** menu or `/etc/fossilsafe/config.json`.
- **`pre_backup`**: Script executed before the drive is mounted. Useful for database snapshots or quiescing services.
- **`post_backup`**: Script executed after the job completes and media is unmounted. Useful for sending custom reports or triggering downstream workflows.

### Advanced Webhooks
Configure multiple endpoints in **Settings > Notifications**. 
- Supports HMAC-SHA256 signature verification.
- Events: `JOB_START`, `JOB_COMPLETED`, `JOB_FAILED`, `COMPLIANCE_ALARM`, `TAPE_ALERT`.

Key details:

- **`api_key`**: Required for all `/api` and `/socket.io` calls.
- **`backend_bind` / `backend_port`**: Where the backend listens.
- **`ui_port`**: Port nginx listens on for the UI. `null` in headless mode.
- **`headless`**: `true` for API-only installs (no UI, no nginx).
- **`db_path`**: SQLite database path.
- **`credential_key_path`**: Encryption key file for source secrets at rest.
- **`catalog_backup_dir`**: Backup directory for catalog export/import safety copies.
- **`diagnostics_dir`**: Directory for diagnostics reports and artifacts.
- **`allowed_origins`**: CORS allowlist (array of strings).
- **`tape.*`**: Optional tape device and timeout configuration.
  - **`tape.mtx_unload_order`**: `drive_slot` (default) or `slot_drive` for libraries that invert `mtx unload` argument order.
  - **`tape.scan_mode_default`**: Default scan mode for `/api/library/scan` (`fast` or `deep`).
  - **`tape.deep_scan_interval_seconds`**: Minimum seconds between deep scans (inventory + status).

### Tape device configuration

FossilSafe prefers stable udev symlinks when available:

- **`/dev/fossilsafe-changer`** — medium changer (SCSI type 8).
- **`/dev/fossilsafe-drive-sg`** — tape drive sg device (optional).

If a configured device path does not exist, FossilSafe will fall back to these symlinks
or auto-discovered devices. The service auto-detects common Fujitsu/IBM libraries and
validates that the changer is a real medium changer (not the drive).

### Tape timeouts

Timeouts are per-command and configurable under `tape.timeouts`. Defaults:

- `mtx_status`/`mtx_inquiry`: 15s
- `mtx_load`/`mtx_unload`/`mtx_transfer`: 180s
- `mtx_inventory`: 300s
- `mt_status`: 15s
- `mt_erase`: 7200s
- `ltfs`: 300s
- `mkltfs`: 3600s

You can also override the erase timeout globally using `tape.erase_timeout_seconds`,
which takes precedence over `tape.timeouts.mt_erase` when set.

## State directory

Default state directory: **`/var/lib/fossilsafe`**

Contents:

- `lto_backup.db` — SQLite catalog database.
- `state.json` — runtime state and DB path overrides.
- `credential_key.bin` — encryption key for stored source secrets.
- `staging/` — staging area for restore/backup workflows.
- `catalog-backups/` — catalog backups created during DB operations.
- `backup-snapshots/` — incremental snapshot manifests per backup set/job.
- `diagnostics/` — diagnostics reports and summaries from test harness runs.

## Changing ports safely

The systemd unit and nginx config are generated at install time. To change ports safely:

### Recommended (re-run installer)

```bash
cd /path/to/FOSSIL-SAFE/scripts
sudo FOSSILSAFE_UI_PORT=8081 FOSSILSAFE_BACKEND_PORT=5001 ./install.sh
```

This rewrites `/etc/systemd/system/fossilsafe.service`, nginx config, and `config.json`.

### Manual (advanced)

1. Update `/etc/systemd/system/fossilsafe.service` (backend bind/port).
2. Update `/etc/nginx/sites-available/fossilsafe.conf` (UI port + proxy target) if UI mode.
3. Update `/etc/fossilsafe/config.json` to match.
4. Reload services:

```bash
sudo systemctl daemon-reload
sudo systemctl restart fossilsafe.service
sudo systemctl restart nginx
```
