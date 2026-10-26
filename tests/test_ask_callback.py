from claude_hermes.modules.telegram.application.services.callbacks import handle_callback
from claude_hermes.modules.turns.infrastructure.ask_broker import AskBroker


async def test_a_valid_click_is_accepted():
    b = AskBroker()
    qid, _ = await b.open(["Redis", "in-memory"], user_id=7)
    assert "Got it" in await handle_callback(f"q:{qid}:1", 7, b)


async def test_a_cancel_click_is_accepted():
    b = AskBroker()
    qid, _ = await b.open(["Redis"], user_id=7)
    assert "Got it" in await handle_callback(f"q:{qid}:c", 7, b)


async def test_a_stale_click_says_so_instead_of_raising():
    """Covers both a second click and a button left in the chat across a restart —
    deliberately the same branch, so there is one path to keep correct, not two."""
    b = AskBroker()
    assert "no longer active" in await handle_callback("q:deadbeef:0", 7, b)


async def test_a_click_from_the_wrong_user_is_refused():
    b = AskBroker()
    qid, fut = await b.open(["Redis"], user_id=7)
    assert "no longer active" in await handle_callback(f"q:{qid}:0", 999, b)
    assert not fut.done()


async def test_malformed_data_never_raises():
    b = AskBroker()
    for data in ("", "garbage", "q:", "q:abc", "x:abc:0", "q:abc:0:extra", None):
        assert await handle_callback(data, 7, b)     # returns text, does not raise
