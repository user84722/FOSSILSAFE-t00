# FossilSafe Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Web Browser                            │
│                    (React/Vite UI)                          │
└─────────────────┬───────────────────────────────────────────┘
                  │ HTTP/WebSocket (port 8080)
┌─────────────────▼───────────────────────────────────────────┐
│                       Nginx                                 │
│              (Reverse proxy, static files)                  │
└─────────────────┬───────────────────────────────────────────┘
                  │ Proxy to localhost:5000
┌─────────────────▼───────────────────────────────────────────┐
│                   Flask Backend                             │
│  ┌─────────────┬─────────────┬─────────────┬──────────────┐│
│  │ Routes      │ Services    │ Core        │ Extensions   ││
│  ├─────────────┼─────────────┼─────────────┼──────────────┤│
│  │ auth        │ tape_service│ database    │ kms_provider ││
│  │ tapes       │ job_service │ backup_eng  │ audit        ││
│  │ jobs        │ metrics_svc │ streaming   │ webhooks     ││
│  │ files       │ diagnostic  │ encryption  │              ││
│  │ restore     │             │ log_manager │              ││
│  │ setup       │             │             │              ││
│  └─────────────┴─────────────┴─────────────┴──────────────┘│
└─────────────────┬───────────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────────┐
│                   SQLite Database                           │
│              (/var/lib/fossilsafe/lto_backup.db)            │
└─────────────────────────────────────────────────────────────┘
```

## Frontend Architecture

### Server state vs. draft state

- **Server state**: Data fetched from the backend (status, jobs, tapes, logs). Managed by `@tanstack/react-query` and the lock-aware helper in `frontend/src/state/useLockAwareQuery.ts`.
- **Draft state**: Any editable form data (sources, archive wizard, restore destination). Stored locally within feature components.

### Update policy while editing

- `UiLockProvider` marks the UI as locked whenever a modal or wizard is open.
- `useLockAwareQuery` pauses refetches during locks to prevent overwriting drafts or scrolling.
- On unlock, queries refetch once on their next interval; no storms or aggressive retries.

### Live update strategy

- Polling intervals are conservative (8–30 seconds depending on resource).
- Refetch on focus is disabled to avoid sudden UI shifts.
- Background errors do not trigger toasts; user actions do.
- Connectivity state is tracked in `frontend/src/state/connectivity.ts`.

### Accessibility

- Skip-to-main link for keyboard navigation
- `aria-live` region for screen reader announcements
- `prefers-reduced-motion` respected for all animations

### Error boundaries

A top-level `ErrorBoundary` in `frontend/src/components/ErrorBoundary.tsx` ensures the UI never white-screens. Includes in-place Retry button.

## Backend Architecture

### Key Components

| Layer | Components |
|-------|------------|
| **API** | Flask blueprints in `routes/` |
| **Services** | Business logic in `services/` |
| **Data** | SQLite via `database.py` |
| **Security** | Auth, encryption, KMS providers |
| **Observability** | Metrics, logging, audit trail, IOStat, Webhooks |
| **Streaming** | High-performance multi-consumer pipeline |

### Security & Integrity

- **KMS Provider Abstraction**: `kms_provider.py` supports Local (file-based) and HashiCorp Vault.
- **Role-Based Access Control (RBAC)**: Fine-grained permissions (Admin/Operator/Viewer) enforced via Flask decorators.
- **Cryptographic Signatures**: Every tape and catalog backup is signed with Ed25519; manifests are protected with SHA-256 HMACs.
- **Immutable Audit Log**: Hash-chained entries in `database.py` with verification.
- **Compliance Alarms**: Real-time webhook triggers for WORM violations and tampering.
- **Prometheus Metrics**: `/api/system/metrics/prometheus` endpoint.

### Technical Hardening

- **Config Lifecycle**: Non-destructive JSON merge on upgrades (`debian/postinst`).
- **Package Mode**: `install.sh` skips apt-get when running in .deb context.
- **Automated Log Rotation**: Configurable retention via `/api/logs/cleanup`.
- **Streaming Safety**: Automated "Stage-Ahead" logic to prevent tape shoe-shining.
