# FossilSafe E2E Test Suite (High-Density)

Comprehensive end-to-end test suite for FossilSafe, consolidated into 3 high-density suites for a streamlined "bulletproof appliance" repository.

## Unified Test Architecture

The E2E suite consists of exactly 3 core logical files:

### 1. `test_system.py`
Validates the complete lifecycle:
- UI and Headless mode installation.
- Prerequisite and environment validation.
- Service health and directory structure.
- Sudoers and systemd hardening verification.

### 2. `test_operations.py`
Validates primary appliance behavior:
- API Contracts (REST/Socket.IO) and RBAC.
- Backup/Restore golden paths.
- Multi-source workflows (SMB, S3) and browser uploads.

### 3. `test_resilience.py`
Validates system stability and recovery:
- Service resilience and state persistence.
- Hardware concurrency and edge cases.
- Disaster recovery and catalog rebuilds.
- Automatic database maintenance (stuck job repair).

## Quick Start

```bash
# Run all consolidated E2E tests
pytest scripts/e2e/

# Run via official runner
./scripts/e2e/run_e2e.sh
```

## Prerequisites

### Python Dependencies
```bash
pip3 install pytest playwright pytest-playwright python-socketio requests
playwright install chromium
```
