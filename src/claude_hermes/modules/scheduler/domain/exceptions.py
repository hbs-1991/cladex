"""Scheduler errors, rendered centrally by the web app's ApplicationException handler."""
from __future__ import annotations

from claude_hermes.core.exceptions import ApplicationException


class ScheduleRejected(ApplicationException):
    """A create/edit the scheduler refuses: unparseable schedule, unsafe prompt,
    out-of-range timeout, missing name.

    400/`bad_request`, not 422/`invalid`: the Routines UI has matched on that exact
    pair since the router existed, and the wire shape is frozen for Wave 2.
    """

    status, code = 400, "bad_request"

    def __init__(self, message: str = ""):
        super().__init__(None, None, message)
