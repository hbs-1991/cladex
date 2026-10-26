"""The bot token must not survive into a log line.

The leak this closes was real: `httpx` logs `POST https://api.telegram.org/bot<token>/…`
at INFO, so `docker logs` on the production host printed a credential that drives the
bot. These tests pin the three ways it could come back — through the message, through
lazy `%s` arguments, and through a traceback.
"""
import io
import logging

from claude_hermes.core import logsafe

TOKEN = "8890275799:AAGtZFDmyxDhUj7k1PVg3my7hmT4Nl5uwn0"
URL = f"https://api.telegram.org/bot{TOKEN}/setChatMenuButton"


def capture(fn) -> str:
    """Run `fn` against a throwaway root handler wrapped the way `install` wraps one."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logsafe.RedactingFormatter(logging.Formatter("%(message)s")))
    log = logging.getLogger("test_logsafe")
    log.handlers = [handler]
    log.propagate = False
    log.setLevel(logging.INFO)
    try:
        fn(log)
    finally:
        log.handlers = []
    return stream.getvalue()


def test_the_secret_half_goes_and_the_bot_id_stays():
    out = logsafe.redact(f"HTTP Request: POST {URL} 200 OK")
    assert TOKEN not in out
    # The id is public and says which bot the line is about: a log that redacted it
    # too would be harder to read for no gain.
    assert "/bot8890275799:<redacted>" in out


def test_a_logged_message_is_redacted():
    out = capture(lambda log: log.info("HTTP Request: POST %s \"HTTP/1.1 200 OK\"", URL))
    assert TOKEN not in out and "<redacted>" in out


def test_lazy_arguments_are_redacted_too():
    """httpx logs with %s arguments, so the URL is not in `record.msg` at all until
    the formatter renders it."""
    out = capture(lambda log: log.info("%s %s", "calling", URL))
    assert TOKEN not in out and "<redacted>" in out


def test_a_traceback_carrying_the_url_is_redacted():
    """The reason this is a formatter and not a filter: `exc_text` is rendered inside
    `Formatter.format`, after every filter has already seen the record."""

    def boom(log):
        try:
            raise RuntimeError(f"connect failed for {URL}")
        except RuntimeError:
            log.exception("telegram call failed")

    out = capture(boom)
    assert TOKEN not in out
    assert "<redacted>" in out and "RuntimeError" in out


def test_text_that_is_not_a_token_is_left_alone():
    for keep in ("/bot123:short", "bot8890275799:AAGtZFDmyxDhUj7k1PVg3my7hmT4", "8890275799"):
        assert logsafe.redact(keep) == keep


def test_install_is_idempotent_and_wraps_the_root_handler():
    root = logging.getLogger()
    saved = list(root.handlers)
    try:
        root.handlers = [logging.StreamHandler(io.StringIO())]
        logsafe.install()
        logsafe.install()
        formatter = root.handlers[0].formatter
        assert isinstance(formatter, logsafe.RedactingFormatter)
        # wrapped once, not nested: the inner formatter is the plain one
        assert not isinstance(formatter._inner, logsafe.RedactingFormatter)
    finally:
        root.handlers = saved
