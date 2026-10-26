"""Content errors, rendered centrally by the web app's ApplicationException handler.

Each carries its OWN code, not a module-wide `bad_request`: the item dialog has to
know which field to point at (`bad_channel`, `bad_media`, `bad_time`, …), and the
tool answers the same message to the agent. The message is shown verbatim.
"""
from __future__ import annotations

from claude_hermes.core.exceptions import ApplicationException


class ContentRejected(ApplicationException):
    """A request the content plan refuses as malformed or not allowed from here:
    400 with the field's own code."""

    status = 400

    def __init__(self, code: str, message: str = ""):
        super().__init__(None, code, message)


class ContentConflict(ApplicationException):
    """A request that is well-formed but collides with the item's state: an edit of
    an item the publisher holds (`locked`), a delete of a published one
    (`published_item`). 409, so the dialog can say "not now" rather than "wrong"."""

    status = 409

    def __init__(self, code: str, message: str = ""):
        super().__init__(None, code, message)
