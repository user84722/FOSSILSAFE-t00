import importlib
import os
import sys
import tempfile
import unittest

try:
    import flask  # noqa: F401
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

# Eventlet is incompatible with Python 3.14+ (missing thread.start_joinable_thread)
PYTHON_314_PLUS = sys.version_info >= (3, 14)


@unittest.skipIf(not FLASK_AVAILABLE, "Flask not installed")
@unittest.skipIf(PYTHON_314_PLUS, "Eventlet incompatible with Python 3.14+")
class WsgiSafeImportTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        os.environ["FOSSILSAFE_SKIP_DEP_CHECK"] = "1"
        os.environ["FOSSILSAFE_AUTOSTART_SERVICES"] = "0"
        os.environ["FOSSILSAFE_REQUIRE_API_KEY"] = "false"
        os.environ["FOSSILSAFE_SKIP_HARDWARE_INIT"] = "1"
        os.environ["FOSSILSAFE_DATA_DIR"] = self.tmpdir.name

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_wsgi_safe_imports_without_raising(self):
        from backend import wsgi_safe

        importlib.reload(wsgi_safe)
        self.assertIsNotNone(wsgi_safe.app)
        self.assertTrue(hasattr(wsgi_safe.app, "route"))


if __name__ == "__main__":
    unittest.main()
