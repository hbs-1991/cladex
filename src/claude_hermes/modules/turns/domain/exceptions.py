"""Turn-specific failures. They render centrally through `core.exceptions`, so a
service never needs to know it is behind HTTP.
"""
from __future__ import annotations

from claude_hermes.core.exceptions import Conflict


class LaneBusy(Conflict):
    """A turn is already running on this lane."""
    code = "busy"


class TurnNotRunning(Conflict):
    """Nothing to cancel on this lane."""
    code = "not_running"


class ModelRejected(Exception):
    """The API refused the model this call asked for, before any tool ran.

    Deliberately NOT an `ApplicationException`: nothing renders it over HTTP —
    it is raised by the executor and caught one frame up by `_run_turn_body`,
    which re-runs the turn on the deploy default and says so in the reply.

    Both facts are carried ON THE EXCEPTION rather than recorded on the executor:
    one `SdkTurnExecutor` serves every lane in the process, so a "the last call
    was rejected" attribute would be read by whichever turn happened to fail next.
    `requested` is the model this call was given (a catalog id) and `error_text`
    is what the CLI said about it, which is also `str(self)` — so a rejection that
    reaches nobody's retry still ends up in `turn.end.error` in words a user can
    act on.

    The signature it stands for was recorded live (story Dev Notes, SDK 0.2.158 /
    CLI 2.1.280): no `tool.call` in this call AND either an `AssistantMessage`
    with `error == "model_not_found"` or a `ResultError` whose `terminal_reason`
    is `"api_error"` with `api_error_status` in (400, 404). The exception's own
    text is never the classifier — the SDK's may be just the result subtype.
    """

    def __init__(self, requested: str, error_text: str):
        super().__init__(error_text or f"the model {requested} was rejected")
        self.requested = requested
        self.error_text = error_text


class CliCallFailed(Exception):
    """A failed SDK call, re-raised carrying what the CLI actually said.

    The SDK's own exception text can be nothing but the result subtype
    (`"Claude Code returned an error result: success"`), while the errored
    `AssistantMessage` that preceded it holds the sentence a user needs ("API
    Error: 429 …"). That message never becomes a `text.block` — it is not the
    assistant talking — so this is how its text reaches `turn.end.error`.

    Raised `from` the original, and `is_cli_death` walks `__cause__`: wrapping
    must not change which failures take the reset-and-retry path.
    """


class TurnFailed(Exception):
    """A turn a caller was waiting on ended `error` or `lost`.

    Not an `ApplicationException`: nothing renders it over HTTP. It is what
    `TurnLauncher.wait` raises at the Telegram dispatcher, whose existing
    `except Exception` turns it into "Something went wrong on my side — try
    again." A cancelled turn is `None` from `wait`, not this — the user stopped
    it on purpose and has nothing to be told.
    """
