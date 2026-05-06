# FossilSafe Installation Fixes

## Fixed Issues

### Issue #4: Missing `packaging/fossilsafe.service`
**Status:** ✅ FIXED

The systemd service unit file was missing, preventing installation. 

**Solution:**
- Created `/packaging/fossilsafe.service` with full systemd configuration
- Added security hardening (NoNewPrivileges, ProtectSystem, etc.)
- Configured resource limits and restart policies
- Template-based substitution for dynamic paths

### Issue #5: Install Failed on Debian 13
**Status:** ✅ IMPROVED

Installation script now includes:
- Better error messages with diagnostic info
- Fallback options for missing tools
- Support for both Debian 12 and 13
- Optional LTFS build-from-source

**Key Improvements:**
1. `generate-cert.sh` - Automatic SSL certificate generation
2. `sudoers-fossilsafe` - Secure sudo configuration for tape operations
3. Enhanced installer error handling
4. Support for headless and UI modes

## Installation Steps

### Fresh Installation

```bash
# Clone your fork
git clone https://github.com/user84722/FOSSILSAFE-t00.git
cd FOSSILSAFE-t00

# Run installer (requires sudo)
sudo ./scripts/install.sh
```

### Interactive Mode (Recommended)
The installer will prompt you for:
- UI mode vs headless
- Backend port (default: 5000)
- UI port (default: 8080)
- Database path
- Domain name (for SSL certs)

### Non-Interactive Mode
```bash
sudo ./scripts/install.sh --yes \
  --backend-port 5000 \
  --ui-port 8080
```

### Headless Mode (API Only)
```bash
sudo ./scripts/install.sh --headless
```

## Post-Installation Verification

```bash
# Check service status
systemctl status fossilsafe

# View logs
journalctl -u fossilsafe -f

# Test API health endpoint
curl http://localhost:5000/api/healthz

# Access Web UI (if not headless)
https://localhost:8080
```

## Troubleshooting

### Package Installation Fails
```bash
# Update package lists
sudo apt-get update

# Retry installer
sudo ./scripts/install.sh --yes
```

### LTFS Tools Missing
The installer now offers two options:
1. **Vendor packages** (recommended): Install from your distro or vendor
2. **Build from source**: Automated fallback (takes ~15 minutes)

```bash
# Install vendor LTFS on Debian
sudo apt-get install ltfs ltfs-tools
```

### Port Already in Use
The installer detects and handles port conflicts automatically:
```bash
sudo ./scripts/install.sh --backend-port 5001 --ui-port 8081
```

### Permission Issues
If you get permission errors:
```bash
# Check service user exists
id fossilsafe

# Fix permissions
sudo chown -R fossilsafe:fossilsafe /var/lib/fossilsafe
sudo chown -R fossilsafe:fossilsafe /opt/fossilsafe
```

## New Files in This Update

- `packaging/fossilsafe.service` - systemd unit with security hardening
- `scripts/setup/generate-cert.sh` - SSL certificate generation
- `scripts/setup/sudoers-fossilsafe` - Sudo permissions for tape ops
- `INSTALL_FIXES.md` - This file

## Testing on Debian 12/13

### Recommended Test Environment
```bash
# VM with 2+ cores, 4GB RAM
# Debian 12 or 13 minimal install
# Tape devices optional (simulator works too)
```

### Validation Checklist
- [ ] Installation completes without errors
- [ ] Service starts and stays running
- [ ] API responds to health checks
- [ ] Web UI loads (if not headless)
- [ ] Logs are clean (no critical errors)
- [ ] Port binding is correct

## Known Limitations

- Requires systemd (no SysVinit support)
- Debian 12+ only (PEP 668 venv compatibility)
- LTFS tools needed (vendor packages recommended)
- FUSE kernel module required

## Next Steps

After installation:
1. Open Web UI at `https://your-ip:8080`
2. Complete onboarding wizard
3. Configure tape hardware
4. Create backup jobs
5. Monitor logs: `journalctl -u fossilsafe -f`
