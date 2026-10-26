"""The three repositories against a real PostgreSQL."""
from conftest import ensure_users

from claude_hermes.modules.sessions.domain.dto import SEARCH_FIELDS
from claude_hermes.modules.sessions.infrastructure.repositories import (
    MessageRepository,
    SessionRepository,
    ThreadRepository,
)

NOW = "2026-07-12T10:00:00+00:00"
LATER = "2026-07-12T11:00:00+00:00"


async def over(engine, *users: int):
    """The engine fixture, once `users` have a `users` row: since 0005
    `threads.owner_user_id` references `users(id)`, so a lane owned by somebody
    with no row is a foreign-key violation rather than a thread.
    """
    await ensure_users(engine, *users)
    return engine


async def test_a_session_round_trips_and_resets(engine):
    eng = await over(engine)
    repo = SessionRepository(eng)
    await repo.upsert("tg:private:1", "sdk-1", "APPEND", NOW)
    assert (await repo.get("tg:private:1"))["sdk_session_id"] == "sdk-1"
    await repo.upsert("tg:private:1", "sdk-2", "APPEND2", LATER)   # conflict branch
    row = await repo.get("tg:private:1")
    assert (row["sdk_session_id"], row["updated_at"], row["created_at"]) == \
        ("sdk-2", LATER, NOW)
    await repo.touch("tg:private:1", "2026-07-12T12:00:00+00:00")
    assert (await repo.get("tg:private:1"))["updated_at"] == "2026-07-12T12:00:00+00:00"
    await repo.reset("tg:private:1")
    assert await repo.get("tg:private:1") is None


async def test_reset_all_empties_every_lane(engine):
    eng = await over(engine)
    repo = SessionRepository(eng)
    await repo.upsert("a", "sdk-a", "", NOW)
    await repo.upsert("b", "sdk-b", "", NOW)
    await repo.reset_all()
    assert await repo.get("a") is None and await repo.get("b") is None
    await repo.reset_all()                      # an empty table is not an error


async def test_search_returns_the_frozen_recall_shape(engine):
    eng = await over(engine)
    repo = MessageRepository(eng)
    await repo.record("web:1:a", "user", "the ansible deploy for report.pdf", NOW)
    # `content_tsv` strips punctuation, so "report.pdf" is indexed as two words and
    # a search for "report" finds it — the whole reason for the regexp_replace.
    hits = await repo.search("report")
    assert len(hits) == 1
    assert tuple(hits[0]) == SEARCH_FIELDS
    assert hits[0]["session_key"] == "web:1:a" and hits[0]["role"] == "user"
    # ts_headline runs over the raw text, so the brackets land on a standalone word.
    await repo.record("web:1:a", "user", "the quarterly numbers are in", LATER)
    assert "[quarterly]" in (await repo.search("quarterly"))[0]["snippet"]


async def test_search_survives_operator_soup_from_the_model(engine):
    eng = await over(engine)
    repo = MessageRepository(eng)
    await repo.record("web:1:a", "user", "hello there", NOW)
    assert await repo.search("!!! & | :*") == []      # plainto_tsquery, not to_tsquery


async def test_last_message_breaks_a_timestamp_tie_by_id(engine):
    """Two messages of one turn share a timestamp; "last" means written last."""
    eng = await over(engine)
    repo = MessageRepository(eng)
    await repo.record("web:1:a", "user", "first", NOW)
    await repo.record("web:1:a", "assistant", "second", NOW)
    assert (await repo.last("web:1:a"))["content"] == "second"
    assert (await repo.last("web:1:a", "user"))["content"] == "first"
    assert await repo.last_ts() == NOW
    assert await repo.last("web:9:none") is None


async def test_a_lane_is_listed_once_somebody_has_talked_in_it(engine):
    """An imported Telegram lane has a threads row but no turns: to the sidebar it
    is an empty shell until `talked=True` (or a title) says otherwise."""
    eng = await over(engine, 1)
    repo = ThreadRepository(eng)
    await repo.upsert("tg:private:1", "telegram", 1, NOW)
    assert await repo.list() == []
    await repo.upsert("tg:private:1", "telegram", 1, LATER, talked=True)
    assert [t["session_key"] for t in await repo.list()] == ["tg:private:1"]
    # and a plain touch afterwards cannot un-say it
    await repo.upsert("tg:private:1", "telegram", 1, LATER)
    assert (await repo.get("tg:private:1"))["has_turns"] is True


async def test_a_web_lane_is_listed_from_the_moment_it_is_created(engine):
    eng = await over(engine, 1)
    repo = ThreadRepository(eng)
    await repo.upsert("web:1:a", "web", 1, NOW)
    assert [t["session_key"] for t in await repo.list()] == ["web:1:a"]


async def test_an_automatic_title_never_overwrites_a_typed_one(engine):
    eng = await over(engine, 1)
    repo = ThreadRepository(eng)
    await repo.upsert("web:1:a", "web", 1, NOW)
    await repo.set_title("web:1:a", "Robot", False)
    assert (await repo.get("web:1:a"))["title"] == "Robot"
    await repo.set_title("web:1:a", "Mine", True)
    await repo.set_title("web:1:a", "Robot again", False)
    assert (await repo.get("web:1:a"))["title"] == "Mine"


async def test_archiving_hides_a_lane_unless_asked_for(engine):
    eng = await over(engine, 1)
    repo = ThreadRepository(eng)
    await repo.upsert("web:1:a", "web", 1, NOW)
    await repo.set_archived("web:1:a", LATER)
    assert await repo.list() == []
    assert [t["session_key"] for t in await repo.list(include_archived=True)] == ["web:1:a"]
    await repo.set_archived("web:1:a", None)
    assert [t["session_key"] for t in await repo.list()] == ["web:1:a"]


async def test_list_pages_backwards_on_last_event_at(engine):
    eng = await over(engine, 1)
    repo = ThreadRepository(eng)
    for i in range(3):
        await repo.upsert(f"web:1:{i}", "web", 1, f"2026-07-12T1{i}:00:00+00:00")
    rows = await repo.list(limit=2)
    assert [t["session_key"] for t in rows] == ["web:1:2", "web:1:1"]
    rows = await repo.list(before=rows[-1]["last_event_at"], limit=2)
    assert [t["session_key"] for t in rows] == ["web:1:0"]


# --- Wave 4a: the owner columns ---------------------------------------------


async def test_a_session_records_its_lane_owner_and_never_loses_it(engine):
    """`owner_id` follows the `index_mtime` rule: written when offered, left alone
    when not. A caller with no owner to offer (an import script) that passes
    none must not null a column the turn wrote."""
    eng = await over(engine, 7)
    repo = SessionRepository(eng)
    await repo.upsert("tg:private:7", "sdk-1", "APPEND", NOW, owner_id=7)
    assert await repo.owner_of("tg:private:7") == 7
    await repo.touch("tg:private:7", LATER)                  # no owner offered
    assert await repo.owner_of("tg:private:7") == 7
    await repo.upsert("tg:private:7", "sdk-2", "APPEND", LATER)   # conflict, no owner
    assert await repo.owner_of("tg:private:7") == 7
    assert await repo.owner_of("tg:private:404") is None      # no row is not user 0


async def test_search_is_scoped_by_owner_inside_the_one_query(engine):
    """The predicate lives in the GIN statement, not over its result: PostgreSQL
    applies the LIMIT, so a Python filter afterwards would turn "eight of A's
    matches" into "no matches" for B."""
    eng = await over(engine, 1, 2)
    repo = MessageRepository(eng)
    await repo.record("web:1:a", "user", "the ansible deploy notes", NOW, owner_id=1)
    await repo.record("web:2:b", "user", "the ansible deploy notes", NOW, owner_id=2)

    mine = await repo.search("ansible", owner_id=1)
    assert [h["session_key"] for h in mine] == ["web:1:a"]
    theirs = await repo.search("ansible", owner_id=2)
    assert [h["session_key"] for h in theirs] == ["web:2:b"]
    # No owner is "every owner" — the legacy facade and the deploy-wide curator.
    assert len(await repo.search("ansible")) == 2
    # A row nobody owns is nobody's recall, not everybody's.
    await repo.record("web:1:a", "user", "ansible orphan", NOW)
    assert [h["snippet"] for h in await repo.search("orphan", owner_id=1)] == []


async def test_last_ts_is_scoped_the_same_way(engine):
    """The curator's quiet-hours check asks about the deploy OWNER: an allowlisted
    user chatting at 3am must not hold the pass off."""
    eng = await over(engine, 1, 2)
    repo = MessageRepository(eng)
    await repo.record("web:1:a", "user", "early", NOW, owner_id=1)
    await repo.record("web:2:b", "user", "late", LATER, owner_id=2)
    assert await repo.last_ts(owner_id=1) == NOW
    assert await repo.last_ts() == LATER


async def test_a_thread_keeps_its_owner_and_heals_a_null_one(engine):
    """A lane does not change hands, and a row the backfill could not derive heals
    the first time somebody takes a turn in it — otherwise it stays invisible to
    its own owner forever."""
    eng = await over(engine, 1, 2)
    repo = ThreadRepository(eng)
    await repo.upsert("tg:supergroup:-5:9", "telegram", None, NOW)
    assert (await repo.get("tg:supergroup:-5:9"))["owner_user_id"] is None
    await repo.upsert("tg:supergroup:-5:9", "telegram", 1, LATER, talked=True)
    assert (await repo.get("tg:supergroup:-5:9"))["owner_user_id"] == 1
    # A later write by somebody else does not take the lane over.
    await repo.upsert("tg:supergroup:-5:9", "telegram", 2, LATER)
    assert (await repo.get("tg:supergroup:-5:9"))["owner_user_id"] == 1


# --- the (model, effort) columns (0008) --------------------------------------


async def test_a_session_records_what_ran_it_and_none_leaves_it_alone(engine):
    """`model=None` is "the caller has nothing to say", exactly like `owner_id` and
    `index_mtime`: a plain touch after a turn that resolved nothing must not erase
    what the session is actually running."""
    eng = await over(engine)
    repo = SessionRepository(eng)
    await repo.upsert("tg:private:1", "sdk-1", "", NOW)
    row = await repo.get("tg:private:1")
    assert (row["model"], row["effort"]) == (None, None)

    await repo.upsert("tg:private:1", "sdk-1", "", LATER,
                      model="claude-opus-5-5", effort="high")
    row = await repo.get("tg:private:1")
    assert (row["model"], row["effort"]) == ("claude-opus-5-5", "high")

    await repo.touch("tg:private:1", LATER)                  # nothing to say
    row = await repo.get("tg:private:1")
    assert (row["model"], row["effort"]) == ("claude-opus-5-5", "high")

    # The pair travels together: a model with no effort writes the NULL effort,
    # because "no effort flag was sent" is a value and not an absence.
    await repo.touch("tg:private:1", LATER, model="claude-sonnet-5")
    row = await repo.get("tg:private:1")
    assert (row["model"], row["effort"]) == ("claude-sonnet-5", None)


async def test_redirecting_the_session_pair_keeps_the_session(engine):
    """Clearing a lane's choice must not end its conversation, nor count as
    activity: the switch is a priced, shown cold write on the next turn."""
    eng = await over(engine)
    repo = SessionRepository(eng)
    await repo.upsert("web:1:a", "sdk-1", "", NOW, model="claude-opus-5", effort="max")
    await repo.set_session_pair("web:1:a", "claude-sonnet-5", None)
    row = await repo.get("web:1:a")
    assert (row["model"], row["effort"]) == ("claude-sonnet-5", None)
    assert row["sdk_session_id"] == "sdk-1"
    assert row["updated_at"] == NOW
    await repo.set_session_pair("web:1:nope", None, None)     # no row is not an error


async def test_a_thread_pair_is_written_only_onto_an_existing_row(engine):
    """`set_model` is an UPDATE: a lane nobody has spoken in has no channel and no
    owner to invent, and a row created here would be an unowned shell."""
    eng = await over(engine, 1)
    repo = ThreadRepository(eng)
    assert await repo.model_of("web:1:a") is None             # no row at all

    await repo.upsert("web:1:a", "web", 1, NOW)
    assert await repo.model_of("web:1:a") == (None, None)     # a row, no choice

    await repo.set_model("web:1:a", "claude-opus-5-5", "xhigh")
    assert await repo.model_of("web:1:a") == ("claude-opus-5-5", "xhigh")
    assert (await repo.get("web:1:a"))["model"] == "claude-opus-5-5"

    await repo.set_model("web:1:a", None, None)
    assert await repo.model_of("web:1:a") == (None, None)

    await repo.set_model("web:1:ghost", "claude-opus-5", None)
    assert await repo.model_of("web:1:ghost") is None         # still no row


# --- the shell stamp (0009) ---------------------------------------------------


async def test_the_shell_stamp_is_written_by_bind_and_touch_and_none_leaves_it(engine):
    """Whether the session was started with a shell. The turn writes it through the
    router and reads it back at the next turn's start, so the column the writer
    stamps must be the key `get` returns — and `None` must never erase it: a NULL
    reads back as "started under the pre-0009 rule", a different answer."""
    from claude_hermes.core.config import Config
    from claude_hermes.modules.sessions.application.services import SessionRouter

    eng = await over(engine, 7)
    repo = SessionRepository(eng)
    router = SessionRouter(repo, Config())

    await router.bind("tg:private:7", "sdk-1", "", NOW, owner_id=7)
    assert (await repo.get("tg:private:7"))["shell_enabled"] is None     # unstamped

    await router.bind("tg:private:7", "sdk-1", "", NOW, owner_id=7, shell_enabled=False)
    assert (await router.get("tg:private:7"))["shell_enabled"] is False
    await router.bind("tg:private:7", "sdk-1", "", LATER)                # nothing to say
    assert (await repo.get("tg:private:7"))["shell_enabled"] is False

    await router.touch("tg:private:7", LATER, shell_enabled=True)
    assert (await repo.get("tg:private:7"))["shell_enabled"] is True
    await router.touch("tg:private:7", LATER, model="claude-opus-5-5")   # nothing to say
    assert (await repo.get("tg:private:7"))["shell_enabled"] is True
    await router.touch("tg:private:7", LATER, shell_enabled=False)
    assert (await repo.get("tg:private:7"))["shell_enabled"] is False

    # A reset drops the stamp with the session: the next bind starts unstamped
    # unless it says otherwise.
    await router.reset("tg:private:7")
    assert await repo.get("tg:private:7") is None
    await router.bind("tg:private:7", "sdk-2", "", LATER, owner_id=7,
                      shell_enabled=True)
    assert (await repo.get("tg:private:7"))["shell_enabled"] is True
    await repo.reset_owned(7)
    assert await repo.get("tg:private:7") is None


async def test_a_fresh_bind_stamps_the_new_row(engine):
    eng = await over(engine)
    repo = SessionRepository(eng)
    await repo.upsert("web:1:a", "sdk-1", "", NOW, shell_enabled=True)
    assert (await repo.get("web:1:a"))["shell_enabled"] is True
    await repo.upsert("web:1:b", "sdk-2", "", NOW, shell_enabled=False)
    assert (await repo.get("web:1:b"))["shell_enabled"] is False
