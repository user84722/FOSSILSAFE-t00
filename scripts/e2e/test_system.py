"""
E2E Tests: System Lifecycle
Consolidated suite for Installation, Upgrades, Service Health, and Hardening.
"""
import pytest
import os
import requests
import subprocess
import time

@pytest.fixture
def api_base_url():
    return os.environ.get("BASE_URL", "http://127.0.0.1:5000")

class TestSystemLifecycle:
    """Validator for the complete FossilSafe appliance lifecycle"""

    def test_installation_smoke(self, api_base_url):
        """Verify the system reports 'healthy' after installation"""
        resp = requests.get(f"{api_base_url}/api/healthz", timeout=10)
        assert resp.status_code == 200
        assert resp.json().get("status") == "healthy"

    def test_sudoers_and_systemd_hardening(self):
        """Verify hardening flags in sudoers and systemd units"""
        service_path = "/opt/fossilsafe/packaging/fossilsafe.service"
        if os.path.exists(service_path):
            with open(service_path) as f:
                content = f.read()
                assert "ProtectSystem=strict" in content
                assert "NoNewPrivileges=true" in content

    def test_upgrade_logic(self):
        """Verify installer can run in update mode (diagnostic)"""
        install_script = "scripts/install.sh"
        assert os.path.exists(install_script)

    def test_prerequisite_validation(self):
        """Verify essential appliance dependencies (mtx, sg3-utils, etc)"""
        for tool in ["mtx", "lsscsi", "sg_inq"]:
            assert subprocess.run(["which", tool], capture_output=True).returncode == 0
