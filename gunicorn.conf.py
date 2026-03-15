import os

bind = f"{os.getenv('FOSSILSAFE_BACKEND_BIND', '127.0.0.1')}:{os.getenv('FOSSILSAFE_BACKEND_PORT', '5000')}"
worker_class = "gthread"
threads = 4
workers = 1
timeout = 120
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("FOSSILSAFE_GUNICORN_LOGLEVEL", "info")
# Reduce access log spam from unauthorized UI polling while keeping real errors visible.
logger_class = "backend.gunicorn_logger.FossilSafeLogger"

def post_fork(server, worker):
    server.log.info("Worker spawned (pid: %s), initializing services...", worker.pid)
    try:
        from backend.lto_backend_main import initialize_backend
        initialize_backend()
    except Exception as e:
        server.log.error(f"Worker {worker.pid} failed to initialize: {e}")
        import traceback
        server.log.error(traceback.format_exc())
    except ImportError:
        # Fallback for wsgi.py if used
        from backend.lto_backend_main import controllers
        if controllers:
            if 'scheduler' in controllers: controllers['scheduler'].start()
            if 'autopilot' in controllers: controllers['autopilot'].start()
