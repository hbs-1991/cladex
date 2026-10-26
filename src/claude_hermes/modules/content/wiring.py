"""The content module's composition, assembled once into a `ContentBundle`.

It sits outside the four layers on purpose: presentation and infrastructure are
siblings and may not import each other, and the tool object (presentation) is
built over the service that the repository and the media resolver
(infrastructure) feed. `main.build_app` calls `build(...)` once, and only when
`content_enabled` — a disabled plan is `None` everywhere, which is how the tool,
the router and the tick all disappear.

What goes where in `main.py`:

* `bundle.service` — `ContentService`: the HTTP bundle's `content` attribute, the
  tick (`await bundle.service.tick(now)`, after `turns.reap`);
* `bundle.tool` — `ContentPlanTool`: turns' `ContentPlanPort` (`SdkTurnExecutor(content=)`);
* `bundle.routine_tools` — `ContentRoutineTools`: the scheduler's `RoutineTools`
  port (`SdkJobExecutor(routine_tools=)`);
* `bundle.decisions` — `ContentCardDecisions`: telegram's `ContentDecisions` port
  (the card buttons, the reply redraft, the card-topic titler skip);
* `bundle.cards` — the late-bound holder of the `ApprovalCards` port:
  `build_delivery` sets its `.target` to telegram's card sender, in the agent and
  in every worker. Unbound (a web-only process), the card outbox waits.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .application.service import DEFAULT_CHANNELS, ContentService
from .infrastructure.media import WorkspaceMedia
from .infrastructure.repository import ContentRepository
from .presentation.agent_tool import ContentPlanTool
from .presentation.decisions import ContentCardDecisions
from .presentation.routine_server import ContentRoutineTools

__all__ = ["ApprovalCardsSlot", "ContentBundle", "build"]


class ApprovalCardsSlot:
    """The `cards` holder: `.target` is the `ApprovalCards` port once a process
    has a Bot, `None` until then (the `LateDocumentDelivery` pattern)."""

    def __init__(self, target: Any = None):
        self.target = target


@dataclass(frozen=True)
class ContentBundle:
    service: ContentService
    tool: ContentPlanTool
    routine_tools: ContentRoutineTools
    decisions: ContentCardDecisions
    cards: Any


def build(engine: Any, *, workspaces: Any, publisher: Any, meta: Any,
          timezone: str = "UTC", channels: Iterable[str] = DEFAULT_CHANNELS,
          publisher_timeout_seconds: float = 180.0, drafter: Any = None,
          cards: Any = None, draft_lead_hours: float = 24.0,
          calendar_origin: str = "") -> ContentBundle:
    """`engine` the app's `AsyncEngine`; `workspaces` the `WorkspaceResolver`;
    `publisher` the adapter over `SchedulerService` satisfying the `Publisher`
    port, and `drafter` the same adapter instance for the `Drafter` port (the
    publisher's when omitted); `cards` a holder whose `.target` is bound later to
    the `ApprovalCards` port (a fresh `ApprovalCardsSlot` when omitted, exposed as
    `bundle.cards`); `meta` the `core.meta.MetaStore`; the rest from `Config`
    (`timezone`, `content_channels`, `cron_timeout_seconds`,
    `content_draft_lead_hours`, `web_public_origin`)."""
    holder = cards if cards is not None else ApprovalCardsSlot()
    service = ContentService(
        ContentRepository(engine), WorkspaceMedia(workspaces), publisher, meta,
        timezone=timezone, channels=channels,
        publisher_timeout_seconds=publisher_timeout_seconds,
        drafter=drafter if drafter is not None else publisher, cards=holder,
        draft_lead_hours=draft_lead_hours, calendar_origin=calendar_origin,
    )
    tool = ContentPlanTool(service)
    return ContentBundle(service=service, tool=tool,
                         routine_tools=ContentRoutineTools(tool),
                         decisions=ContentCardDecisions(service), cards=holder)
