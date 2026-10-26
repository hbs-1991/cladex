"""Keep the bot token out of the logs.

Every Telegram API call is `https://api.telegram.org/bot<token>/<method>`, and
`httpx` logs that URL at INFO. So `docker logs claude-hermes-agent-1` handed anyone
who could read it a token that drives the bot — reads every message the bot can see
and sends as it. The container's logs are not a secret store and should not have to
be treated as one.

Two ways to fix it, and the obvious one is worse. Silencing `httpx` to WARNING would
work today and only today: it loses the request lines for the calls that carry no
secret (OAuth discovery, a connector's probe), and it protects nothing the next
library to print one of those URLs might do. Redacting at the **formatter** covers
the message, its arguments and the traceback of an exception whose URL is in the
string, for every logger that reaches the handler.
"""
from __future__ import annotations

import logging
import re

# `/bot<digits>:<secret>` — the shape Telegram's API uses, anchored on the literal
# "/bot" so a bare number in a message is never mangled. The id half stays: it is
# public, it identifies which bot a line is about, and keeping it makes a redacted
# log still readable.
TOKEN_RE = re.compile(r"/bot(\d+):[A-Za-z0-9_\-]{10,}")
PLACEHOLDER = r"/bot\1:<redacted>"


def redact(text: str) -> str:
    return TOKEN_RE.sub(PLACEHOLDER, text)


class RedactingFormatter(logging.Formatter):
    """Wraps another formatter's output. Subclassing the *formatter* rather than
    filtering records is what makes this cover a traceback: `exc_text` is rendered
    inside `Formatter.format`, after any filter has already seen the record."""

    def __init__(self, inner: logging.Formatter | None = None):
        super().__init__()
        self._inner = inner or logging.Formatter()

    def format(self, record: logging.LogRecord) -> str:
        return redact(self._inner.format(record))


def install(level: int = logging.INFO) -> None:
    """`logging.basicConfig`, then wrap every root handler's formatter.

    Idempotent: a handler already wrapped is left alone, so calling this twice (a
    test, a re-entered entry point) does not nest formatters.
    """
    logging.basicConfig(level=level)
    for handler in logging.getLogger().handlers:
        if not isinstance(handler.formatter, RedactingFormatter):
            handler.setFormatter(RedactingFormatter(handler.formatter))
