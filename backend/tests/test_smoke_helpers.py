import os
import tempfile
import unittest
from unittest import mock

from scripts import smoke_test_helpers


class SmokeTestHelperTests(unittest.TestCase):
    def test_load_api_key_from_env(self):
        key, error = smoke_test_helpers.load_api_key("/missing.json", "env-key", True)
        self.assertEqual(key, "env-key")
        self.assertIsNone(error)

    def test_load_api_key_from_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.json")
            with open(config_path, "w") as handle:
                handle.write('{"api_key": "file-key"}')
            key, error = smoke_test_helpers.load_api_key(config_path, None, False)
            self.assertEqual(key, "file-key")
            self.assertIsNone(error)

    def test_load_api_key_missing_config(self):
        key, error = smoke_test_helpers.load_api_key("/missing.json", None, False)
        self.assertIsNone(key)
        self.assertIn("Config file not found", error)

    def test_classify_status_code(self):
        ok = smoke_test_helpers.classify_status_code(200, "backend")
        self.assertTrue(ok.ok)
        missing = smoke_test_helpers.classify_status_code(404, "backend")
        self.assertEqual(missing.reason, "wrong_base_url")
        unreachable = smoke_test_helpers.classify_status_code(0, "ui")
        self.assertEqual(unreachable.reason, "ui_unreachable")

    def test_tool_requirements_optional(self):
        with mock.patch("scripts.smoke_test_helpers.shutil.which") as which_mock:
            which_mock.side_effect = lambda tool: None if tool == "ltfsck" else f"/usr/bin/{tool}"
            result = smoke_test_helpers.evaluate_tool_requirements(
                required=["mkltfs", "ltfs"],
                optional=["ltfsck"],
                strict_optional=False,
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.missing_required, [])
            self.assertEqual(result.missing_optional, ["ltfsck"])

    def test_tool_requirements_strict_optional(self):
        with mock.patch("scripts.smoke_test_helpers.shutil.which", return_value=None):
            result = smoke_test_helpers.evaluate_tool_requirements(
                required=["mkltfs"],
                optional=["ltfsck"],
                strict_optional=True,
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.missing_required, ["mkltfs"])


if __name__ == "__main__":
    unittest.main()
