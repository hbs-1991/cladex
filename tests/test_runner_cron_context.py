"""The cron → topic context bridge.

A scheduled job's report is delivered to its Telegram topic by the ticker via
RichSender — it never passes through a turn, so the topic's SDK session has not
seen it. Seen live 01.08.2026: the daily brief ended "reply with a number", the
user replied "3", and the model — whose transcript contained nothing at all —
asked what "3" meant. The runner closes the gap by quoting the latest report
into the first user message after each delivery.

The three cases a fake job port can pin (the quote, the no-reinject, the lookup
failure) live in `tests/modules/turns/test_turns_service.py`; what is here runs
over the real `JobRepository` and `MessageRepository`, because the bridge reads
the delivery's timestamp off the messages table and a second delivery has to
move it.
"""

from datetime import datetime, timezone

from claude_agent_sdk import AssistantMessage, TextBlock

from claude_hermes.core.app_context import JobLookup
from claude_hermes.modules.scheduler.infrastructure.repository import JobRepository
# The SDK seam moved with the loop in Wave 2: `query` is read off the module
# that calls it, so this is where it has to be patched.
from claude_hermes.modules.turns.infrastructure import sdk_runner

from tests.test_runner import FakeResult, make_stack

KEY = "tg:private:1:t57074"


def make_prompt_capturing_query(prompts, reply="ok"):
    async def fake_query(*, prompt, options):
        prompts.append(prompt)
        yield AssistantMessage(content=[TextBlock(text=reply)], model="m")
        yield FakeResult("sdk-xyz", reply)

    return fake_query


async def make_stack_with_job(engine, tmp_path):
    jobs = JobRepository(engine)
    job_id = await jobs.create(
        name="content-radar", kind="cron", value="0 5 * * *", prompt="sweep",
        deliver_chat_id=1, origin_session_key="tg:private:1",
        next_run_at="2099-01-01T00:00:00+00:00", deliver_thread_id=57074,
    )
    # A constructor argument since Phase C: the job lookup is what wires the cron
    # context, and it is resolved once rather than re-read per turn. `JobLookup`
    # is the composition root's adapter over `by_thread`.
    stack = await make_stack(engine, tmp_path, jobs=JobLookup(jobs))
    return stack, jobs, job_id


async def deliver(stack, job_id, text):
    ts = datetime.now(timezone.utc).isoformat()
    await stack.messages.record(f"cron:{job_id}", "user", "sweep", ts)
    await stack.messages.record(f"cron:{job_id}", "assistant", text, ts)


async def test_first_reply_after_delivery_sees_the_report(engine, tmp_path, monkeypatch):
    stack, _, job_id = await make_stack_with_job(engine, tmp_path)
    await deliver(stack, job_id, "1. topic one\n2. topic two\n3. topic three")
    prompts = []
    monkeypatch.setattr(sdk_runner, "query", make_prompt_capturing_query(prompts))

    await stack.runner.run_turn(KEY, "3")

    assert "topic three" in prompts[0]           # the report is quoted in
    assert 'scheduled job "content-radar"' in prompts[0]
    assert prompts[0].rstrip().endswith("3")     # the user's reply comes last
    # the searchable history equals what the model saw
    hits = await stack.messages.search('"topic three"')
    assert any(h["session_key"] == KEY for h in hits)


async def test_a_fresh_delivery_injects_again(engine, tmp_path, monkeypatch):
    stack, _, job_id = await make_stack_with_job(engine, tmp_path)
    await deliver(stack, job_id, "monday brief")
    prompts = []
    monkeypatch.setattr(sdk_runner, "query", make_prompt_capturing_query(prompts))

    await stack.runner.run_turn(KEY, "1")
    await deliver(stack, job_id, "tuesday brief")
    await stack.runner.run_turn(KEY, "2")

    assert "monday brief" in prompts[0]
    assert "tuesday brief" in prompts[1]
    assert "monday brief" not in prompts[1]  # only the latest report is quoted


async def test_threads_without_a_job_are_untouched(engine, tmp_path, monkeypatch):
    stack, _, job_id = await make_stack_with_job(engine, tmp_path)
    await deliver(stack, job_id, "the brief")
    prompts = []
    monkeypatch.setattr(sdk_runner, "query", make_prompt_capturing_query(prompts))

    await stack.runner.run_turn("tg:private:1:t999", "hi")   # some other topic
    await stack.runner.run_turn("tg:private:1", "hi")        # the flat chat

    assert prompts[0] == "hi" and prompts[1] == "hi"


async def test_job_topic_with_no_delivery_yet_is_untouched(engine, tmp_path, monkeypatch):
    stack, _, _ = await make_stack_with_job(engine, tmp_path)
    prompts = []
    monkeypatch.setattr(sdk_runner, "query", make_prompt_capturing_query(prompts))

    await stack.runner.run_turn(KEY, "когда будет бриф?")

    assert prompts[0] == "когда будет бриф?"
