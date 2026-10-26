"""`session_search`: full-text recall over past conversations."""
from __future__ import annotations

from claude_hermes.modules.turns.application.ports import MessageSearch


def make_session_search(search: MessageSearch, ctx=None):
    """Recall, scoped to the lane's owner (Wave 4a, D7).

    `ctx.owner_id` is the turn's owner, decided once by the runner — never the
    sender, so a stranger's message in a group lane recalls the deploy owner's
    history and not their own. A `ctx` of `None` (a fork that has no lane) or an
    owner of `0` searches everything, which is what this tool did before the wave.

    The scoping happens in the query, not over its result: the repository applies
    `WHERE owner_id = :uid` inside the same GIN statement as the text match, so
    the `limit=8` is eight of the asker's own hits.
    """
    owner_id = getattr(ctx, "owner_id", 0) or None

    async def session_search(args: dict) -> dict:
        hits = await search.search(args.get("query", ""), limit=8, owner_id=owner_id)
        if not hits:
            text = "No matches in past conversations."
        else:
            lines = [
                f"[{h['ts']}] {h['session_key']} ({h['role']}): {h['snippet']}"
                for h in hits
            ]
            text = "\n".join(lines)
        return {"content": [{"type": "text", "text": text}]}

    return session_search
