import asyncio

import pytest

from claude_hermes.modules.turns.domain.value_objects import CANCELLED
from claude_hermes.modules.turns.infrastructure.ask_broker import AskBroker


async def test_open_hands_out_distinct_ids():
    # async, not sync: open() needs a RUNNING loop to create its future.
    b = AskBroker()
    q1, _ = await b.open(["a", "b"], user_id=7)
    q2, _ = await b.open(["a", "b"], user_id=7)
    assert q1 != q2


async def test_resolve_delivers_the_chosen_option():
    b = AskBroker()
    qid, fut = await b.open(["Redis", "in-memory"], user_id=7)
    assert await b.resolve(qid, "1", user_id=7) is True
    assert await b.wait(qid, fut, timeout=1.0) == "in-memory"


async def test_resolve_delivers_the_cancel_sentinel():
    b = AskBroker()
    qid, fut = await b.open(["Redis"], user_id=7)
    assert await b.resolve(qid, "c", user_id=7) is True
    assert await b.wait(qid, fut, timeout=1.0) is CANCELLED


async def test_a_stranger_cannot_answer_and_cannot_destroy_the_question():
    """Peek before popping. If the ownership check came after the pop, a
    stranger's click would delete a question its owner is still looking at."""
    b = AskBroker()
    qid, fut = await b.open(["Redis"], user_id=7)
    assert await b.resolve(qid, "0", user_id=999) is False
    assert not fut.done()                       # still live
    assert await b.resolve(qid, "0", user_id=7) is True   # owner still can answer
    assert await b.wait(qid, fut, timeout=1.0) == "Redis"


async def test_a_question_answers_only_once():
    b = AskBroker()
    qid, fut = await b.open(["Redis", "in-memory"], user_id=7)
    assert await b.resolve(qid, "0", user_id=7) is True
    assert await b.resolve(qid, "1", user_id=7) is False
    assert await b.wait(qid, fut, timeout=1.0) == "Redis"


async def test_an_unknown_or_malformed_token_is_refused_not_raised():
    b = AskBroker()
    qid, fut = await b.open(["Redis"], user_id=7)
    assert await b.resolve("nosuch", "0", user_id=7) is False
    assert await b.resolve(qid, "not-a-number", user_id=7) is False
    assert await b.resolve(qid, "99", user_id=7) is False      # out of range
    assert not fut.done()


async def test_wait_times_out_to_none_and_deregisters():
    b = AskBroker()
    qid, fut = await b.open(["Redis"], user_id=7)
    assert await b.wait(qid, fut, timeout=0.01) is None
    assert await b.resolve(qid, "0", user_id=7) is False       # entry is gone


async def test_the_timed_out_future_is_never_cancelled():
    """asyncio.wait_for would cancel this future while the broker still held it,
    and a click landing in that window would hit InvalidStateError. asyncio.wait
    does not cancel — this test is what stops someone 'simplifying' it back."""
    b = AskBroker()
    qid, fut = await b.open(["Redis"], user_id=7)
    await b.wait(qid, fut, timeout=0.01)
    assert not fut.cancelled()


async def test_discard_after_wait_already_popped_is_a_safe_noop():
    """The ask_user closure in main.py runs `finally: ask_broker.discard(qid)`
    unconditionally after `await ask_broker.wait(...)`, which has already popped
    the entry on the normal path. discard() must tolerate that double-pop
    silently and must not resurrect or otherwise disturb the (already
    delivered) result."""
    b = AskBroker()
    qid, fut = await b.open(["Redis"], user_id=7)
    assert await b.resolve(qid, "0", user_id=7) is True
    assert await b.wait(qid, fut, timeout=1.0) == "Redis"

    b.discard(qid)  # must not raise

    assert await b.resolve(qid, "0", user_id=7) is False  # still gone, not reopened


async def test_a_cancelled_send_does_not_leak_the_pending_entry():
    """Reproduces the shape of the ask_user closure's fix: `sendMessage` and
    `wait` both run inside one `try`, with `ask_broker.discard(qid)` in a
    synchronous `finally`. asyncio.CancelledError is a BaseException, so an
    `except Exception` around only the send (the pre-fix shape) would skip
    that cleanup and leave the entry in `_pending` forever when the task is
    cancelled mid-send — this guards that the try/finally shape does not."""
    b = AskBroker()
    qid, fut = await b.open(["Redis"], user_id=7)

    async def send_then_wait():
        try:
            await asyncio.sleep(10)  # stand-in for the in-flight sendMessage call
            await b.wait(qid, fut, timeout=1.0)
        finally:
            b.discard(qid)

    task = asyncio.create_task(send_then_wait())
    await asyncio.sleep(0)  # let the task reach the sleep (i.e. the "in-flight send")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert qid not in b._pending  # the entry did not leak
    assert await b.resolve(qid, "0", user_id=7) is False  # and can't be answered into the void


@pytest.mark.parametrize("delay", [0.001, 0.02, 0.05])
async def test_a_click_and_the_wait_never_disagree(delay):
    """The bug this guards: the user's popup says 'accepted' while the model is
    told nobody answered. Whoever pops first wins, and both sides read that same
    decision."""
    for _ in range(50):
        b = AskBroker()
        qid, fut = await b.open(["Redis"], user_id=7)

        async def click():
            await asyncio.sleep(delay)
            return await b.resolve(qid, "0", user_id=7)

        answer, accepted = await asyncio.gather(
            b.wait(qid, fut, timeout=0.02), click()
        )
        assert accepted == (answer == "Redis")


async def test_options_for_reads_an_open_question_without_disturbing_it():
    """The web router validates a browser's answer against the labels and turns
    the chosen one back into the index resolve() expects, so the accessor must
    hand out a copy (a caller must not be able to edit the pending question)
    and must go None the moment the question is no longer answerable."""
    b = AskBroker()
    qid, fut = await b.open(["Redis", "in-memory"], user_id=7)

    assert await b.options_for(qid) == ["Redis", "in-memory"]
    (await b.options_for(qid)).append("sqlite")                  # a caller mutating what it got
    assert await b.options_for(qid) == ["Redis", "in-memory"]   # cannot reach the broker's copy
    assert await b.options_for("no-such-question") is None

    assert await b.resolve(qid, "0", user_id=7) is True
    assert await b.wait(qid, fut, timeout=1.0) == "Redis"
    assert await b.options_for(qid) is None                     # gone once answered
