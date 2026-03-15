import unittest
from pathlib import Path

from backend.smb_fixture import build_fixture_paths, build_smb_fixture_config


class SMBFixtureConfigTests(unittest.TestCase):
    def test_build_fixture_config_includes_paths(self):
        base_dir = Path("/tmp/fossilsafe_fixture")
        paths = build_fixture_paths(base_dir)
        config = build_smb_fixture_config(paths, port=4455)

        self.assertIn("smb ports = 4455", config)
        self.assertIn(f"path = {paths.share_dir}", config)
        self.assertIn(f"log file = {paths.log_file}", config)
        self.assertIn(f"pid directory = {paths.run_dir}", config)
        self.assertIn("read only = no", config)


if __name__ == "__main__":
    unittest.main()
