"""The content item's lifecycle, and who may move it where.

    idea --(owner approves the theme)--> planned --(drafter claims)--> drafting
      --(drafter submits)--> review --(owner approves, web or Telegram)--> scheduled
      --(tick)--> queued --(publisher claims)--> publishing --> published | failed
                                   \\--(tick: timeout + grace)--> stuck
    `drafting` goes back to `planned` on `draft_failed` or the drafting timeout (the
    third time to `failed`); `draft` is the hand-written way into `review`; and
    `skipped` is for anything the owner or the agent drops.

Three actors, three rule sets, all here so the router, the tool and the tick
physically share them:

* **the agent** (the `content_plan` tool) edits the plan but never approves it: it
  may move an item OUT of `AGENT_FROM` and INTO `AGENT_TO`, and it may not touch an
  item in `AGENT_LOCKED` at all. An agent content edit of a `scheduled` item lands
  in `review` with the approval cleared — what the owner approved is no longer
  what would be published.
* **the owner** (the web routes) approves (`APPROVABLE` -> `scheduled`), retries
  (`RETRYABLE`) and edits anything the publisher does not hold (`WEB_LOCKED`).
* **the publisher** (a run of the configured routine) is the only actor that moves
  `queued -> publishing -> published | failed`;
* **the drafter** (a run of ANOTHER configured routine) is the only actor that moves
  `planned -> drafting -> review | planned`. Theme approval (`idea -> planned`) is
  the owner's, and it never schedules anything: the drafted post still waits in
  `review` for the owner's content approval.

Nothing automatic ever re-posts: a `publishing` item is left to the owner.
"""
from __future__ import annotations

IDEA = "idea"
PLANNED = "planned"
DRAFTING = "drafting"
DRAFT = "draft"
REVIEW = "review"
SCHEDULED = "scheduled"
QUEUED = "queued"
PUBLISHING = "publishing"
PUBLISHED = "published"
FAILED = "failed"
STUCK = "stuck"
SKIPPED = "skipped"

STATUSES = (IDEA, PLANNED, DRAFTING, DRAFT, REVIEW, SCHEDULED, QUEUED, PUBLISHING,
            PUBLISHED, FAILED, STUCK, SKIPPED)

# What a new item may start as, from the web and from the tool alike. Anything
# later than `review` is reached by an act, never by a field.
CREATE_STATUSES = frozenset({IDEA, DRAFT, REVIEW})

# The agent's reach. Leaving `scheduled` or `planned` is allowed (and clears the
# approval); entering either is not — both approvals are human acts.
AGENT_FROM = frozenset({IDEA, PLANNED, DRAFT, REVIEW, SCHEDULED, FAILED})
AGENT_TO = frozenset({IDEA, DRAFT, REVIEW, SKIPPED})
# Items the agent may neither edit nor re-status: the publisher or the drafter holds
# them, or they are history. Only the routine that holds them moves them on.
AGENT_LOCKED = frozenset({DRAFTING, QUEUED, PUBLISHING, STUCK, PUBLISHED})
# An agent edit of any of these on a `scheduled` item sends it back to `review`.
APPROVED_FIELDS = ("body", "media", "channel", "format", "publish_at")
# An agent edit of any of these on a `planned` item sends it back to `idea`: the
# owner approved THIS theme, on this channel, at this time.
THEME_FIELDS = ("title", "channel", "format", "publish_at")

# The owner's reach through the web routes. `drafting` is the drafter's until it
# submits or times out: an edit would be overwritten by the submit anyway.
WEB_LOCKED = frozenset({DRAFTING, QUEUED, PUBLISHING, PUBLISHED})
WEB_SET_STATUSES = frozenset({IDEA, DRAFT, REVIEW, SKIPPED})
APPROVABLE = frozenset({IDEA, DRAFT, REVIEW, FAILED})
RETRYABLE = frozenset({FAILED, STUCK, PUBLISHING})

# What the calendar's backlog never lists: nothing left to plan.
BACKLOG_EXCLUDED = frozenset({PUBLISHED, SKIPPED})


def agent_refusal(current: str, target: str) -> str | None:
    """Why the agent may not move an item from `current` to `target`, or `None`.

    The sentences are the tool's answers, so they say what to do instead.
    """
    if target not in STATUSES:
        return f"Unknown status '{target}'."
    if current == DRAFTING:
        return ("Refused: the item is drafting; only the drafter routine moves it "
                "from here.")
    if current in AGENT_LOCKED:
        return (f"Refused: the item is {current}; only the publisher routine and the "
                "owner move it from here.")
    if target not in AGENT_TO:
        if target == SCHEDULED:
            return ("Refused: only the owner schedules an item, by approving it in the "
                    "calendar. Set it to 'review' and tell them it is ready.")
        if target == PLANNED:
            return ("Refused: only the owner approves a theme, in the calendar. Leave "
                    "it as an idea with a publish time and tell them.")
        return f"Refused: you may set idea, draft, review or skipped, not {target}."
    if current not in AGENT_FROM:
        return f"Refused: an item in {current} cannot be moved by you."
    return None


def needs_attention(status: str, error: str | None, *, publish_at: str | None = None,
                    approved: bool = False, card_state: str | None = None,
                    now_iso: str | None = None) -> bool:
    """The calendar's badge: something went wrong that only the owner can settle —

    * a failure or a stuck publish;
    * a queued item the tick could not hand on (no publisher, a paused one);
    * a planned item the drafter could not take (no drafter, or woken three times
      for nothing), which "Retry drafting" settles;
    * an approval card Telegram refused three times, while the post still waits
      in `review` for an approval that card was meant to carry — once the owner
      approved, edited or dropped it, the dead card explains nothing;
    * a post whose time came before anybody approved it: still `planned`,
      `drafting` or an unapproved `review` when `publish_at` passed (`now_iso` and
      `publish_at` are both `iso_utc`, compared as strings like the tick does).

    `status` and `error` alone keep v1's answer, for a caller with no clock.
    """
    if status in (FAILED, STUCK):
        return True
    if status in (QUEUED, PLANNED) and error is not None:
        return True
    if card_state == "failed" and status == REVIEW and not approved:
        return True
    if (now_iso is not None and publish_at and publish_at <= now_iso and not approved
            and status in (PLANNED, DRAFTING, REVIEW)):
        return True
    return False


def item_needs_attention(item, now_iso: str) -> bool:
    """`needs_attention` over a whole row (a mapping with the table's columns)."""
    return needs_attention(
        item["status"], item.get("error"), publish_at=item.get("publish_at"),
        approved=item.get("approved_at") is not None,
        card_state=item.get("card_state"), now_iso=now_iso)
