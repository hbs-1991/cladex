"""One logging setup for the process: structlog for our code, stdlib for
everything else, both rendered by the same formatter — and the same token
redaction (logsafe) wrapped around it.

Our loggers gain structured key-values without cutting third-party libraries out
of the log: `ProcessorFormatter` runs stdlib records through the same chain, so
`httpx` lines keep arriving and keep being redacted."""
from __future__ import annotations

import logging
import sys

import structlog

from . import logsafe

_SHARED = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.stdlib.PositionalArgumentsFormatter(),
]


def configure(level: int = logging.INFO) -> None:
    structlog.configure(
        processors=[*_SHARED, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    # repr_native_str=False: a log line is read by a human, so
                    # `lane=tg:1` beats `lane='tg:1'`.
                    structlog.processors.KeyValueRenderer(
                        key_order=["timestamp", "level", "logger", "event"],
                        repr_native_str=False)],
    )
    handler = logging.StreamHandler(sys.stdout)   # stdout: the container log driver reads it
    handler.setFormatter(logsafe.RedactingFormatter(formatter))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
