import io
import logging

import structlog

from claude_hermes.core import logging as core_logging

TOKEN = "8890275799:AAGtZFDmyxDhUj7k1PVg3my7hmT4Nl5uwn0"


def test_configure_renders_key_values_and_redacts(monkeypatch):
    root = logging.getLogger()
    saved, saved_level = list(root.handlers), root.level
    try:
        core_logging.configure(logging.INFO)
        stream = io.StringIO()
        root.handlers[0].stream = stream
        structlog.get_logger("t").info(
            "calling %s", f"https://api.telegram.org/bot{TOKEN}/x", lane="tg:1")
        logging.getLogger("third_party").warning("plain %s", "record")
        out = stream.getvalue()
        assert "lane=tg:1" in out and "<redacted>" in out and TOKEN not in out
        assert "plain record" in out
    finally:
        # Leave the process as we found it: later tests rely on caplog seeing plain
        # stdlib records, not the event dicts wrap_for_formatter hands the formatter.
        structlog.stdlib.recreate_defaults(log_level=None)
        root.handlers, root.level = saved, saved_level
