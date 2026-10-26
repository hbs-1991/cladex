"""GET /api/commands — the composer's `/` menu.

Dependencies: `get_caller_home` (where the CALLER's skills live), `get_slash_sources`
(the enabled plugins, which is `extensions`' knowledge arriving through a port) and
`get_sdk_commands` for the names the CLI itself reported in the last turn. Any
logged-in user may read the list — it is what they can type, not what they own.

Whose list, though, is the caller's: the skills come out of the tree the
`WorkspaceResolver` gives the requesting user, and the plugins out of that user's
own enabled rows. `deps.home` and the deploy owner's plugins would be one person's
answer handed to everybody.

The third source is a port and not an attribute of the local `TurnService` because
of where the list is learned: the CLI reports its commands in a turn's `init`
message, and since Wave 3b the process that receives that message is a worker, not
whichever instance serves this request. `SlashCommandsPort` (bound to
`MetaSlashCommands`) reads them from where the worker recorded them.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..application.commands_service import SlashCommandsService
from ..application.ports import SlashCommandsPort, SlashSourcePort
from .dependencies import get_caller_home, get_sdk_commands, get_slash_sources

router = APIRouter(tags=["turns"])


@router.get("/api/commands", operation_id="turns_list_commands")
async def list_commands(request: Request,
                        home: Path | None = Depends(get_caller_home),
                        sources: SlashSourcePort | None = Depends(get_slash_sources),
                        sdk_commands: SlashCommandsPort | None = Depends(get_sdk_commands),
                        ) -> JSONResponse:
    """The caller's skills, then their plugins, then the CLI's own names; first to
    claim a name keeps it."""
    service = SlashCommandsService(home=home, sources=sources,
                                   owner_id=request.state.user_id)
    # Empty before the first turn ever runs: the CLI reports its commands in the
    # `init` message, so nothing knows them until one has. A bundle with no port
    # bound answers the same empty list.
    sdk = await sdk_commands.sdk_commands() if sdk_commands is not None else []
    return JSONResponse({"commands": await service.collect(sdk)})
