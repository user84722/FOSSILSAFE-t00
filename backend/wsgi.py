"""WSGI entrypoint for production servers."""

import eventlet
eventlet.monkey_patch()

from backend.lto_backend_main import initialize_backend

app = initialize_backend()
