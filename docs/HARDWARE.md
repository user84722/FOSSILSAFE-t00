# Hardware guide

## Library mode vs drive-only mode

- **Library mode**: Requires a tape library/changer device (e.g., `/dev/sg1`) and a drive device (e.g., `/dev/nst0`). FossilSafe can load/unload tapes via `mtx`.
- **Drive-only mode**: Uses a single tape drive with **no changer**. Users insert/eject tapes manually and provide tape names/barcodes through the UI/API. The backend handles manual tape naming and does not require a changer device.

## Device paths

Common defaults used by the backend:

- Tape drive: `/dev/nst0` (non-rewind) or `/dev/st0`
- Changer: `/dev/sg1` (or `/dev/sg0`, `/dev/sch0` depending on hardware)

## Drive Path Calibration (Logical-to-Physical)

In complex multi-drive libraries, the operating system's device assignment (`/dev/nst0`, `/dev/nst1`) often does not match the physical slot order reported by `mtx`. 

FossilSafe provides an **Automated Calibration Wizard**:
- **Mechanism**: The system moves a tape with a known barcode to a physical drive index and then scans all logical tape paths to find where that barcode appears.
- **Persistence**: These mappings are saved to `config.json` under `tape.drive_devices`.
- **Usage**: Run the wizard from the **Equipment** page when setting up a new library or after major hardware changes.

## Tape Topology

FossilSafe provides a relational visualization of your tape estate:
- **Mapping**: Correlates logical Backup Sets with physical Tape Volumes.
- **Visual Graph**: Accessible via the **Tape Sets** page (`#/backup-sets`).
- **Use Case**: Quickly identify which tapes belong to a spanning set and their sequence for restoration.

Discovery helper:

```bash
lsscsi -g
```

## Required tooling

These packages are installed by the installer:

- `mtx` (changer control)
- `mt-st` (drive control)
- `sg3-utils` (e.g., `sg_inq`)
- `lsscsi`

## Predictive Health (TapeAlert)

FossilSafe proactively monitors native LTO **TapeAlerts** via SCSI Log Page 0x2E. 
- **Tooling**: Uses `sg_logs` (from `sg3-utils`) to interrogate drive health during operation.
- **Events**: Critical alerts (impending failure) and Warning alerts (cleaning needed, media degradation) are recorded in the system audit logs and displayed in the tape details panel.
- **Appliance Self-Test**: A comprehensive diagnostic suite that performs I/O stress tests, parity checks, and drive calibration. Results are stored as **Self-Test Reports** for historical health auditing.

> **LTFS requirement:** FossilSafe requires `mkltfs`, `ltfs`, and `ltfsck`. The installer will attempt apt packages, then prompt for vendor LTFS packages (recommended) or an optional build-from-source fallback.

## Fujitsu ETERNUS LT40 S2 and similar libraries

General guidance (applies to most SAS/SCSI libraries):

- Confirm the changer shows up as a **Medium Changer** device in `lsscsi -g`.
- Verify `mtx -f /dev/sgX status` returns slots and drives.
- Use the **non-rewind** device (`/dev/nstX`) for backups and restores.

## Troubleshooting LTFS + detection

- **Missing LTFS tools**: Verify with `command -v mkltfs && command -v ltfs && command -v ltfsck`.
- **FUSE not available**: Check `/dev/fuse` and `fusermount3` (`sudo modprobe fuse` if needed).
- **Identify drives/changers**:
  - `lsscsi -g` for vendor/product strings and device nodes.
  - `sg_inq /dev/sgX` for SCSI inquiry data.
  - `mtx -f /dev/sgX status` for changer inventory.

### Identifying mappings manually
If the calibration wizard is unavailable, you can manually identify a drive:
1. Load a tape: `mtx -f /dev/sgX load <slot> <drive>`
2. Check for the barcode: `tapeinfo -f /dev/nstY` (look for MAM data)
3. Repeat for all logical `/dev/nstY` until you find the tape.

## Barcode caveats and manual naming

- If the barcode reader is missing or unreadable, FossilSafe allows manual tape naming.
- In library mode, missing barcodes are assigned placeholders like `SLT###`/`DRV##LD` and can be aliased in the UI.
- In drive-only mode, **manual tape names are required** for each insertion.
