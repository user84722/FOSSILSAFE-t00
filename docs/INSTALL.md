# Install FossilSafe (Debian 12)

This guide documents the exact installer flow and defaults used by `scripts/install.sh`.

## Supported OS

- Debian 12 (bookworm) with `systemd` and `apt`.

## Quick install

```bash
git clone https://github.com/FOSSILSAFE/FOSSIL-SAFE.git
cd FOSSIL-SAFE/scripts
sudo ./install.sh
```

Headless API-only install:

```bash
git clone https://github.com/FOSSILSAFE/FOSSIL-SAFE.git
cd FOSSIL-SAFE/scripts
sudo ./install.sh --headless
```

## Installer prompts (interactive mode)

When run in a TTY, the installer shows defaults and then asks:

1. **Use defaults? [Y/n]**
   - Default mode: **UI (nginx + frontend)**
   - Default UI port: **8080**
   - Default backend bind: **127.0.0.1**
   - Default backend port: **5000**
   - Default DB path: **/var/lib/fossilsafe/lto_backup.db**

2. If you choose **not** to use defaults:
   - **Install in headless mode (API only, no UI)? [y/N]**
   - **Enter UI port** (only if UI mode)
   - **Enter backend port**
   - **Enter backend bind**
   - **Enter database path**

Notes:
- In UI mode the backend is forced to bind `127.0.0.1` (private behind nginx).
- In headless mode you may choose `0.0.0.0` to expose the API on your LAN.

## Environment overrides

The installer also respects environment variables:

- `FOSSILSAFE_INSTALL_DIR` (default `/opt/fossilsafe`)
- `FOSSILSAFE_CONFIG_PATH` (default `/etc/fossilsafe/config.json`)
- `FOSSILSAFE_DATA_DIR` (default `/var/lib/fossilsafe`)
- `FOSSILSAFE_DB_PATH` (default `/var/lib/fossilsafe/lto_backup.db`)
- `FOSSILSAFE_STATE_PATH` (default `/var/lib/fossilsafe/state.json`)
- `FOSSILSAFE_CREDENTIAL_KEY_PATH` (default `/var/lib/fossilsafe/credential_key.bin`)
- `FOSSILSAFE_CATALOG_BACKUP_DIR` (default `/var/lib/fossilsafe/catalog-backups`)
- `FOSSILSAFE_UI_PORT` (default `8080` if UI mode)
- `FOSSILSAFE_BACKEND_PORT` (default `5000`)
- `FOSSILSAFE_BACKEND_BIND` (default `127.0.0.1`)
- `FOSSILSAFE_HEADLESS=1` (skip UI build + nginx)
- `FOSSILSAFE_API_KEY` (supply a specific API key)
- `FOSSILSAFE_CORS_ORIGINS` (comma-separated list for CORS)

## Packages installed by the script

UI mode installs:

- `acl`, `coreutils`, `curl`, `fuse3`, `libfuse3-3`, `gzip`, `lsscsi`, `mtx`, `mt-st`, `nginx`, `python3`, `python3-venv`, `rsync`, `sg3-utils`, `smbclient`, `cifs-utils`, `tar`, `util-linux`, `nodejs`, `npm`

Headless mode installs the same list **minus** `nginx`, `nodejs`, and `npm`.

### LTFS tooling requirement

LTFS tooling (`mkltfs`, `ltfs`, `ltfsck`) is required for core operations. The installer:

1. Installs safe tape diagnostics (lsscsi/sg3-utils/mt-st/mtx).
2. Attempts to install LTFS packages if the OS repo provides them.
3. Prompts you to either install vendor LTFS packages (recommended) or build the open-source LTFS reference implementation from source (advanced).

Verify LTFS readiness:

```bash
command -v mkltfs && command -v ltfs && command -v ltfsck
ls -l /dev/fuse
```

If you choose build-from-source, the installer clones the LTFS reference implementation into
`/opt/fossilsafe/third_party/ltfs-src`, pins the ref in `FOSSILSAFE_LTFS_REF.txt`, and installs
binaries into `/usr/local/bin`. See `THIRD_PARTY_NOTICES.md` for LTFS attribution and license text.

## What the installer does

1. Creates the `fossilsafe` system user.
2. Installs diagnostics and detects tape hardware.
3. Installs system packages (including FUSE).
4. Verifies LTFS tooling and offers vendor/build-from-source options.
5. Creates a Python virtualenv under `/opt/fossilsafe/venv`.
6. Writes `/etc/fossilsafe/config.json` with canonical keys.
7. Installs Python requirements from `requirements.txt`.
8. Copies `backend/` and `frontend/` into `/opt/fossilsafe`.
9. Validates backend imports (`backend.lto_backend_main:app`).
10. Writes `fossilsafe.service` and, in UI mode, nginx config + static assets.
11. Starts and enables the `fossilsafe.service` systemd unit (if systemd is available).

### Frontend build determinism

UI installs always use `npm ci` with the committed `frontend/package-lock.json`. The installer runs
the npm install/build steps as the `fossilsafe` service user and repairs any root-owned
`frontend/node_modules` from prior runs. Frontend build output is captured in
`/var/log/fossilsafe/install-frontend.log`; if the build fails, consult this log for the full error.

## Tape device permissions and symlinks

The installer creates a `fossilsafe-tape` group and udev rules. Data directories use strict `0700` permissions. It also installs stable symlinks:

- `/dev/fossilsafe-changer` for the medium changer (SCSI type 8).
- `/dev/fossilsafe-drive-sg` for the tape drive sg device (SCSI type 1).
- `/dev/nst*` and `/dev/st*` owned by the tape group.

If you change hardware or udev rules, reload and re-trigger udev:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=scsi_generic --subsystem-match=scsi_tape
```

## Post-install summary

At the end, the installer prints a summary that includes:

- **Mode**, backend bind/port, UI URL (if enabled).
- **API URL** (direct and via nginx).
- **Service status** and health endpoint checks.
- **Config path**, **state dir**, **DB path**, and **credential key** presence.
- **API key access** (either the newly generated key or the command to retrieve it).

Keep this output for onboarding users; it is the fastest path to the correct URL and API key.

## First-Run Setup Wizard

After the installer completes, open the UI URL in your browser to complete the **Onboarding Wizard**:
1. **Admin Creation**: Set up the initial administrator account.
2. **Environment Check**: The wizard performs a final sanity check of the SQL database, staging directories, and tape hardware.

## Post-install CLI Access

The installer deploys a unified command-line utility for appliance management:
- **Binary**: `/usr/local/bin/fsafe-cli`
- **Dashboard**: Run `fsafe-cli dashboard` for a real-time interactive TUI.
- **Reference**: See `fsafe-cli --help` for the full command list.

## Service management

On a systemd host, the installer creates and enables `/etc/systemd/system/fossilsafe.service`:

```bash
sudo systemctl status fossilsafe
sudo systemctl restart fossilsafe
sudo journalctl -u fossilsafe -f
```

If systemd is unavailable (containers or minimal images), the installer prints manual start
commands. The minimal backend command is:

```bash
/opt/fossilsafe/venv/bin/gunicorn -c /opt/fossilsafe/gunicorn.conf.py backend.lto_backend_main:app
```

## No hardware mode

If no tape devices or changer are detected, the installer logs a warning and continues.
The UI and API remain available, and the tape tab will indicate that no hardware is present.

## Troubleshooting LTFS

- **Missing mkltfs/ltfs/ltfsck**: Install vendor LTFS packages (IBM LTFS, HPE StoreOpen, Quantum LTFS) or use the installer’s build-from-source option.
- **FUSE not available**: Ensure `/dev/fuse` exists (load the module with `sudo modprobe fuse`) and `fusermount3` is present.
- **Hardware detection**: Use `lsscsi -g` for vendor/product strings and `mtx -f /dev/sgX status` for changer detection.

## Debugging installs and tape/library operations

### Installer smoke test

The installer runs `scripts/smoke_test.sh` and will now fail only on real health issues. When it fails, it prints the
explicit reason and exit code so you can pinpoint what to fix.

Example outputs:

```text
Smoke test failed (backend unreachable) [exit 10]: Backend /api/healthz unreachable (curl code 000).
```

```text
Smoke test failed (ui unreachable) [exit 11]: UI /api/healthz unreachable.
```

```text
Smoke test failed (auth rejected) [exit 12]: Backend /api/status rejected API key (HTTP 403).
```

```text
Smoke test failed (api key missing/misconfigured) [exit 13]: API key required but missing.
```

```text
Smoke test failed (wrong base URL/proxy path) [exit 14]: UI is not proxying /api (expected /api/healthz).
```

If the API key is required, make sure it is set in `/etc/fossilsafe/config.json` or exported as `API_KEY` before
running the smoke test manually.

### Library diagnostics and long operations

Use the diagnostics endpoints to capture library state, device mappings, and recent commands:

* `/api/diag` – system-level diagnostics and command history
* `/api/diag/library` – library/device mapping, busy state, open file descriptor holders (if `lsof` is available),
  recent tape commands, and last error

Long operations (load/unload/wipe/move) run as persistent jobs. If you cancel a job, FossilSafe will mark it as
“cancel requested” and attempt a safe stop/unload when possible. Non-cancellable hardware operations will continue
running, but the job log will record the cancellation request and eventual completion state.

For tape-command troubleshooting, check the server logs for `tape_cmd` entries, which include start/end timestamps,
return codes, and captured stdout/stderr for `mtx`, `mt`, and LTFS commands.

## Removing LTFS built from source

If you chose the build-from-source option and want to revert:

```bash
sudo rm -f /usr/local/bin/mkltfs /usr/local/bin/ltfs /usr/local/bin/ltfsck
sudo ldconfig
sudo rm -rf /opt/fossilsafe/third_party/ltfs-src
```
