"""`cost_report`: per-day API spend, straight off the turn cost ledger."""
from __future__ import annotations

from claude_hermes.modules.turns.application.ports import TurnCostRepository


def make_cost_report(costs: TurnCostRepository, ctx=None):
    """Spend per day, scoped to the lane's owner (Wave 4a, D7).

    The tool is offered to every lane, so unscoped it would tell an allowlisted
    user what the whole deploy spends. `ctx.owner_id` is the lane's owner; `None`
    or `0` (a fork with no lane) reports every owner, which is what this did
    before the wave and what the owner's own lane still gets — their id owns the
    group and cron rows too.
    """
    owner_id = getattr(ctx, "owner_id", 0) or None

    async def cost_report(args: dict) -> dict:
        rows = await costs.by_day(int(args.get("days", 7)), owner_id=owner_id)
        if not rows:
            text = "No cost data yet."
        else:
            total = sum(r["cost_usd"] for r in rows)
            lines = [f"{r['day']}: ${r['cost_usd']:.2f}" for r in rows]
            text = "\n".join(lines) + f"\nTotal: ${total:.2f}"
        return {"content": [{"type": "text", "text": text}]}

    return cost_report
