"""WSGI entrypoint that keeps the service running even if hardware is missing."""
from __future__ import annotations

import os
import threading

from backend import lto_backend_main


def _defer_hardware_init() -> None:
    def _init() -> None:
        try:
            lto_backend_main.create_socketio()
            lto_backend_main.initialize_smb_client()
            if os.environ.get("FOSSILSAFE_SKIP_HARDWARE_INIT", "").lower() in ("1", "true", "yes"):
                lto_backend_main._set_hardware_init_status(
                    False,
                    "Hardware initialization skipped (FOSSILSAFE_SKIP_HARDWARE_INIT=1).",
                )
                return
            if lto_backend_main.db is None:
                lto_backend_main._set_hardware_init_status(
                    False,
                    lto_backend_main.db_unavailable_reason or "Database not initialized",
                )
                return
            tape_status = lto_backend_main.initialize_tape_controller()
            if not tape_status.get("hardware_available"):
                reason = tape_status.get("hardware_reason") or "no hardware"
                lto_backend_main.db.log_entry('warning', 'system', f'Deferred hardware init unavailable ({reason})')
                return
            lto_backend_main.db.log_entry('info', 'system', 'Deferred hardware init started')
            try:
                lto_backend_main.tape_controller.initialize()
                lto_backend_main.db.log_entry('info', 'system', 'Tape library initialized')
                lto_backend_main._set_hardware_init_status(True, None)
            except Exception as exc:
                lto_backend_main.db.log_entry('warning', 'system', f'Tape library initialization failed: {exc}')
                lto_backend_main._set_hardware_init_status(False, str(exc))
            finally:
                lto_backend_main._apply_startup_recovery()

            if lto_backend_main.scheduler and not lto_backend_main.scheduler.is_running():
                lto_backend_main.scheduler.start()
            if lto_backend_main.autopilot and not lto_backend_main.autopilot.running:
                lto_backend_main.autopilot.start()
        except Exception as exc:
            lto_backend_main.app.logger.exception("Deferred hardware initialization failed")
            lto_backend_main.log_warning(
                f"Deferred hardware init failed: {exc}",
                "system",
            )
            lto_backend_main._set_hardware_init_status(False, str(exc))

    threading.Thread(target=_init, daemon=True).start()


app = lto_backend_main.create_app(autostart_services=False)
lto_backend_main.create_socketio()
# _defer_hardware_init() will be called by Gunicorn's post_fork hook in gunicorn.conf.py
