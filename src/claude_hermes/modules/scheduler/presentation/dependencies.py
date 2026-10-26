"""What the Routines router asks for, and where it comes from.

One provider, because the router has one collaborator. Built once at module scope
out of `core.di.provide`: FastAPI keys `dependency_overrides` by the callable
*object*, so a provider rebuilt per request could not be overridden.

Bundle attributes read by this module: **`scheduler`** (`SchedulerService`) and
**`cfg`** (the deploy config, for the offered `models:` a routine's pair is
validated against). The service holds the topics port itself, and the owner gate
that used to live here is `core.auth.require_owner`.
"""
from __future__ import annotations

from typing import Any

from starlette.requests import Request

from claude_hermes.core.di import provide

from ..application.service import SchedulerService

__all__ = ["get_cfg", "get_scheduler"]

_scheduler = provide("scheduler")
_cfg = provide("cfg")


def get_scheduler(request: Request) -> SchedulerService:
    """`deps.scheduler` — the same service `cron_manage` and the ticker call.

    The shell does not mount this router when the attribute is `None`, so `provide`'s
    503 guard is unreachable in a correctly assembled app.
    """
    return _scheduler(request)


def get_cfg(request: Request) -> Any:
    """`deps.cfg` — read for `cfg.models` / `cfg.model` alone.

    Which models exist is `core.model_catalog`'s answer; which of them a deploy
    OFFERS is config, and a routine's pair is validated against that same list the
    composer and `/model` offer, so the three channels cannot disagree.
    """
    return _cfg(request)
