# Compatibility Notes

> **Tested hardware limitation**: Verified only on Fujitsu ETERNUS LT40 S2 with IBM LTO-6 HH drive; other hardware untested.

This document summarizes what FossilSafe expects from tape libraries, what is configurable, and what is unknown/unverified. Statements are derived from code paths under `backend/` and sample configuration in `docs/CONFIGURATION.md`.

## Expected general compatibility

FossilSafe is designed for **LTFS-capable LTO drives** and **SCSI medium changers** that expose `mtx`-compatible status and inventory output. Some vendor-specific behavior may require configuration, and untested hardware can differ in subtle ways.

## Vendor-specific notes

- `mtx` semantics vary across libraries, especially `mtx unload` argument ordering and error messages.
- Set `tape.mtx_unload_order` as needed and validate behavior with a non-destructive scan.
- Untested libraries may report different `VolumeTag` or status formats.

## Compatibility matrix

| Area | What should work universally | What is configurable | Unknown / unverified |
| --- | --- | --- | --- |
| `mtx` command usage | `mtx status`, `mtx inventory`, `mtx load`, `mtx unload`, `mtx transfer` are used for library operations. | `tape.mtx_unload_order` controls whether `mtx unload` uses `drive slot` vs `slot drive` argument order. | Vendor-specific `mtx` error text and edge cases in `mtx status` parsing outside the supported patterns. |
| Slot/drive numbering | FossilSafe uses the slot and drive numbers exactly as reported by `mtx status`. | None. | Whether a vendor uses zero-based numbering for any element (library/drive) is unknown/unverified. |
| Barcodes / VolumeTag | Inventory parses `VolumeTag` when present and falls back to “no barcode” placeholders. | None. | Barcode format variations beyond `VolumeTag` output are unknown/unverified. |
| Device paths | `/dev/nst*` for drives and `/dev/sg*` for changers are used; stable symlinks are preferred when configured. | `tape.drive_device`, `tape.drive_devices`, and `tape.changer_device` let you override the detected paths. | Vendor-specific udev naming conventions are unknown/unverified. |
| `mt` output | `mt` is used for drive operations (status/erase) with no strict parsing assumptions in the scanning flow. | `tape.erase_timeout_seconds` and `tape.timeouts` override command timeouts. | `mt` status output formats across vendors are unknown/unverified. |
| LTFS toolchain | `mkltfs`, `ltfs`, and `ltfsck` are invoked with device paths and must be available in PATH. | None. | Vendor-specific LTFS options and device requirements beyond what the code calls are unknown/unverified. |
| Hardware Encryption | Supports native **LTO Hardware Encryption**. Keys are managed via KMS. | KMS Provider (Local or HashiCorp Vault) is configurable. | Behavior with non-LTO drives or very old LTO (pre-LTO-4) hardware encryption is unverified. |
| Predictive Health | **TapeAlerts** (Log Page 0x2E) monitored during operations. | Reporting interval and alert thresholds. | Reliability of TapeAlert implementation on third-party SAS controllers or entry-level libraries. |

## Required device types

- **Changer**: a medium changer `sg` device (SCSI type 8). FossilSafe validates the changer and warns if you point `mtx` at a drive device.
- **Drive**: a tape drive device (`/dev/nst*` for streaming, `/dev/sg*` for some LTFS commands).

## Scanning behavior

- **Fast scan** (status-only) uses `mtx status` and parses `VolumeTag` fields.
- **Deep scan** uses `mtx inventory` followed by `mtx status`.

See `docs/SCANNING.md` for verification steps and log locations.

## Community testing

Hardware testing is limited by what I can physically access. If you have a different tape library or drive and are willing to help test compatibility (remotely, via logs, or by loaning hardware), I’d be glad to work with you.
