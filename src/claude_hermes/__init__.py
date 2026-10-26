import structlog

__version__ = "0.1.0"

# Our modules bind `structlog.get_logger(__name__)` at import time, but only an
# entry point runs `core.logging.configure()` — a test, a script or `python -m`
# never does. structlog's own defaults print straight to stdout, which would put
# those records outside the logging tree that carries the handlers, `caplog` and
# the token redaction. Routing structlog at stdlib from the first import keeps
# them in it; `configure()` later replaces the rendering, not the destination.
structlog.stdlib.recreate_defaults(log_level=None)
