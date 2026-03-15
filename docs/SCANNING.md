# Library Scanning

> **Tested hardware limitation**: Verified only on Fujitsu ETERNUS LT40 S2 changer + IBM LTO-6 HH drive. Other libraries/drives untested; behavior may vary.

FossilSafe supports two scan modes backed by `mtx`:

- **Fast scan** (`mode: "fast"`): runs `mtx status` only.
- **Deep scan** (`mode: "deep"`): runs `mtx inventory`, then `mtx status`.

Deep scans are rate-limited and blocked when the library is busy with active jobs that write/erase tape.

## Discovery Scans (Calibration)

Calibration is typically perform once during onboarding or hardware replacement.

## Tape Topology

Scanning data is utilized by the **Tape Topology** engine to build a relational graph of your tape library. 
- Fast scans update the "Online/Offline" status of volumes.
- Deep scans verify barcodes against the catalog to ensure multi-volume spanning sets are complete.
- Visual mapping can be viewed on the **Tape Sets** page.

## API endpoint

```
POST /api/library/scan
{
  "mode": "fast" | "deep"
}
```

- Default mode comes from `tape.scan_mode_default` (see `docs/CONFIGURATION.md`).
- Deep scans respect `tape.deep_scan_interval_seconds`.

## UI behavior

The UI “Scan/Rescan” actions call the deep scan endpoint by default.

## How to prove it’s scanning barcodes

1. **Look for the scan request log** (inventory requested or not):
   - Log entry category: `tape`
   - Message: `Library scan requested`
   - Details include `mode` and `inventory_requested`.

2. **Look for the command logs** for `mtx`:
   - Log entry category: `tape_cmd`
   - Message: `Tape command executed`
   - Details include the exact `command` array and `duration`.

3. **Run a manual comparison** on the host:
   ```bash
   mtx -f /dev/sgX inventory
   mtx -f /dev/sgX status
   ```
   Use the same changer device path configured in `tape.changer_device`.

If the `tape_cmd` logs show `mtx inventory` followed by `mtx status`, a deep scan is triggering hardware inventory in the library.
