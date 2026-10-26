from claude_hermes.modules.turns.infrastructure.hermes_server import make_handlers
from claude_hermes.modules.turns.infrastructure.repositories import TurnCostRepository


async def test_cost_ledger_roundtrip(engine):
    costs = TurnCostRepository(engine)
    await costs.record("2026-07-11T10:00:00+00:00", "tg:private:1", 0.05)
    await costs.record("2026-07-12T09:00:00+00:00", "tg:private:1", 0.10)
    await costs.record("2026-07-12T10:00:00+00:00", "cron:1", 0.02)
    days = await costs.by_day(7)
    assert days[0]["day"] == "2026-07-12" and abs(days[0]["cost_usd"] - 0.12) < 1e-9
    assert days[1]["day"] == "2026-07-11"


async def test_cost_report_tool(engine):
    costs = TurnCostRepository(engine)
    await costs.record("2026-07-12T10:00:00+00:00", "tg:private:1", 0.10)
    handlers = make_handlers(search=None, costs=costs)
    out = await handlers["cost_report"]({"days": 7})
    text = out["content"][0]["text"]
    assert "2026-07-12" in text and "0.10" in text
