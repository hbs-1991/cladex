"""The application services, driven through fake ports — no database, no SDK."""
import asyncio
from datetime import datetime, timezone

import pytest

from claude_hermes.core.config import Config
from claude_hermes.core.exceptions import NotFound
from claude_hermes.modules.sessions.application.ports import (
    EventPublisher,
    LaneActivity,
    OwnerDefaults,
    SessionStore,
    ThreadStore,
    TitleGenerator,
)
from claude_hermes.modules.sessions.application.services import (
    SessionRouter,
    SessionService,
    ThreadService,
    ThreadTitleService,
)


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class FakeSessions:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.reset_alls = 0

    async def get(self, session_key):
        return self.rows.get(session_key)

    async def upsert(self, session_key, sdk_session_id, system_append, now, *,
                     index_mtime=None, owner_id=None, model=None, effort=None,
                     shell_enabled=None):
        old = self.rows.get(session_key, {})
        self.rows[session_key] = {"session_key": session_key, "sdk_session_id": sdk_session_id,
                                  "system_append": system_append, "updated_at": now,
                                  "index_mtime": index_mtime, "owner_id": owner_id,
                                  "model": old.get("model"), "effort": old.get("effort")}
        if model is not None:                      # the pair travels together
            self.rows[session_key]["model"] = model
            self.rows[session_key]["effort"] = effort

    async def touch(self, session_key, now, *, index_mtime=None, owner_id=None,
                    model=None, effort=None, shell_enabled=None):
        self.rows[session_key]["updated_at"] = now
        if index_mtime is not None:
            self.rows[session_key]["index_mtime"] = index_mtime
        if owner_id is not None:                   # None never clears a known owner
            self.rows[session_key]["owner_id"] = owner_id
        if model is not None:                      # …and the same for the pair
            self.rows[session_key]["model"] = model
            self.rows[session_key]["effort"] = effort

    async def set_session_pair(self, session_key, model, effort):
        row = self.rows.get(session_key)
        if row is not None:
            row["model"], row["effort"] = model, effort

    async def owner_of(self, session_key):
        return (self.rows.get(session_key) or {}).get("owner_id")

    async def reset(self, session_key):
        self.rows.pop(session_key, None)

    async def reset_all(self):
        self.rows.clear()
        self.reset_alls += 1

    async def reset_owned(self, owner_id, *, include_unowned=False):
        for key, row in list(self.rows.items()):
            if row["owner_id"] == owner_id or (include_unowned and row["owner_id"] is None):
                del self.rows[key]


class FakeThreads:
    def __init__(self, rows=()):
        self.rows: dict[str, dict] = {r["session_key"]: dict(r) for r in rows}
        self.pages: list[tuple] = []

    async def upsert(self, session_key, channel, owner_user_id, now, *, talked=False):
        row = self.rows.setdefault(session_key, {
            "session_key": session_key, "channel": channel, "owner_user_id": owner_user_id,
            "title": None, "title_manual": 0, "archived_at": None, "created_at": now,
            "has_turns": False, "model": None, "effort": None})
        row["last_event_at"] = now
        row["has_turns"] = row["has_turns"] or talked

    async def get(self, session_key):
        row = self.rows.get(session_key)
        return dict(row) if row else None

    async def list(self, *, include_archived=False, before=None, limit=50):
        self.pages.append((before, limit))
        rows = sorted(self.rows.values(), key=lambda r: r["last_event_at"], reverse=True)
        if not include_archived:
            rows = [r for r in rows if r["archived_at"] is None]
        if before is not None:
            rows = [r for r in rows if r["last_event_at"] < before]
        return [dict(r) for r in rows[:limit]]

    async def set_title(self, session_key, title, manual):
        row = self.rows[session_key]
        if manual:
            row["title"], row["title_manual"] = title, 1
        elif row["title_manual"] == 0:
            row["title"] = title

    async def set_archived(self, session_key, archived_at):
        self.rows[session_key]["archived_at"] = archived_at

    async def model_of(self, session_key):
        row = self.rows.get(session_key)
        return None if row is None else (row.get("model"), row.get("effort"))

    async def set_model(self, session_key, model, effort):
        row = self.rows.get(session_key)           # an UPDATE: no row, no write
        if row is not None:
            row["model"], row["effort"] = model, effort


class SpyBus:
    def __init__(self, running=()):
        self.emitted = []
        self._running = set(running)

    async def emit(self, session_key, turn_id, type, payload):
        self.emitted.append((session_key, turn_id, type, payload))

    async def active_lanes(self, session_keys):
        return {k for k in session_keys if k in self._running}


def test_the_fakes_satisfy_the_ports():
    """Structural typing is the whole wiring story: if a fake passes here, so does
    the repository the composition root hands in."""
    assert isinstance(FakeSessions(), SessionStore)
    assert isinstance(FakeThreads(), ThreadStore)
    assert isinstance(SpyBus(), EventPublisher)
    assert isinstance(SpyBus(), LaneActivity)


# --- SessionRouter ---------------------------------------------------------

async def test_an_expired_lane_is_purged_not_just_hidden():
    """Returning None while leaving the row would resume the stale SDK session on
    the next bind attempt."""
    store = FakeSessions()
    router = SessionRouter(store, Config(idle_reset_hours=24.0))
    await router.bind("tg:private:1", "sdk-1", "APPEND", "2026-07-10T10:00:00+00:00")
    assert await router.route("tg:private:1", dt("2026-07-12T12:00:00")) is None
    assert store.rows == {}


async def test_is_unbound_is_true_only_before_the_first_turn():
    store = FakeSessions()
    router = SessionRouter(store, Config())
    assert await router.is_unbound("tg:private:1:t9") is True
    await router.bind("tg:private:1:t9", "sdk-1", "APPEND", "2026-07-12T10:00:00+00:00")
    assert await router.is_unbound("tg:private:1:t9") is False


async def test_reset_and_reset_all_go_through_the_router():
    """The runner used to reach past the router into the store; the lane's session
    lifecycle is the router's alone."""
    store = FakeSessions()
    router = SessionRouter(store, Config())
    await router.bind("a", "sdk-a", "", "2026-07-12T10:00:00+00:00")
    await router.bind("b", "sdk-b", "", "2026-07-12T10:00:00+00:00")
    await router.reset("a")
    assert await router.get("a") is None and await router.get("b") is not None
    await router.reset_all()
    assert store.rows == {} and store.reset_alls == 1


# --- ThreadService ---------------------------------------------------------

async def test_a_foreign_thread_is_not_found_not_forbidden():
    """403 would confirm that a lane with that key exists."""
    threads = ThreadService(FakeThreads([{
        "session_key": "web:2:zzz", "channel": "web", "owner_user_id": 2, "title": None,
        "title_manual": 0, "archived_at": None, "last_event_at": "2026-07-12T10:00:00+00:00",
        "created_at": "2026-07-12T10:00:00+00:00", "has_turns": False}]))
    with pytest.raises(NotFound) as e:
        await threads.get_visible("web:2:zzz", 1, False)
    assert e.value.status == 404 and e.value.code == "not_found"
    with pytest.raises(NotFound):
        await threads.get_visible("web:1:nope", 1, True)


async def test_a_visible_lane_behind_a_page_of_invisible_ones_is_still_listed():
    """Visibility is a Python predicate, so a SQL page can be all-invisible. The
    listing keeps reading rather than answering with an empty page and no cursor."""
    store = FakeThreads()
    await store.upsert("web:1:mine", "web", 1, "2026-07-12T00:00:00+00:00")
    for i in range(60):
        await store.upsert(f"web:2:{i}", "web", 2, f"2026-07-12T10:{i:02d}:00+00:00")
    page, next_cursor = await ThreadService(store).list_visible(1, False, limit=50)
    assert [t["session_key"] for t in page] == ["web:1:mine"]
    assert next_cursor is None
    assert len(store.pages) > 1                      # it did keep pulling batches


async def test_a_full_page_answers_with_a_cursor():
    store = FakeThreads()
    for i in range(5):
        await store.upsert(f"web:1:{i}", "web", 1, f"2026-07-12T1{i}:00:00+00:00")
    page, next_cursor = await ThreadService(store).list_visible(1, False, limit=2)
    assert [t["session_key"] for t in page] == ["web:1:4", "web:1:3"]
    assert next_cursor == "2026-07-12T13:00:00+00:00"


async def test_creating_a_web_lane_does_not_claim_a_turn_happened():
    store = FakeThreads()
    thread = await ThreadService(store).create_web(7, "2026-07-12T10:00:00+00:00")
    assert thread["session_key"].startswith("web:7:") and thread["channel"] == "web"
    assert thread["has_turns"] is False


async def test_touch_never_un_says_a_turn():
    store = FakeThreads()
    threads = ThreadService(store)
    await threads.touch("tg:private:1", "telegram", 1, "2026-07-12T10:00:00+00:00",
                        talked=True)
    await threads.touch("tg:private:1", "telegram", 1, "2026-07-12T11:00:00+00:00")
    assert (await threads.get("tg:private:1"))["has_turns"] is True


async def test_running_lanes_is_empty_without_a_lane_activity_port():
    """And it is asked once for a whole page: the answer lives in another module's
    table, so a per-row check would be one round trip per thread."""
    assert await ThreadService(FakeThreads()).running_lanes(["web:1:a"]) == set()
    threads = ThreadService(FakeThreads(), SpyBus(["web:1:a"]))
    assert await threads.running_lanes(["web:1:a", "web:1:b"]) == {"web:1:a"}
    assert await threads.running_lanes([]) == set()


# --- ThreadTitleService ----------------------------------------------------

class FakeGenerator:
    def __init__(self, produced="Ansible deploy flow"):
        self.produced = produced
        self.seen = []

    async def generate(self, text):
        self.seen.append(text)
        if isinstance(self.produced, Exception):
            raise self.produced
        return self.produced


def test_a_generator_fake_satisfies_the_port():
    assert isinstance(FakeGenerator(), TitleGenerator)


async def test_autotitle_names_an_untitled_thread_and_tells_the_tab():
    store = FakeThreads()
    await store.upsert("web:1:a", "web", 1, "2026-07-12T10:00:00+00:00")
    bus = SpyBus()
    service = ThreadTitleService(store, FakeGenerator(), bus)
    await service.autotitle("web:1:a", "how do I deploy with ansible?")
    assert (await store.get("web:1:a"))["title"] == "Ansible deploy flow"
    assert bus.emitted == [("web:1:a", "", "thread.title",
                            {"title": "Ansible deploy flow"})]


async def test_autotitle_leaves_a_named_thread_alone():
    store = FakeThreads()
    await store.upsert("web:1:a", "web", 1, "2026-07-12T10:00:00+00:00")
    await store.set_title("web:1:a", "Mine", True)
    gen = FakeGenerator()
    await ThreadTitleService(store, gen, SpyBus()).autotitle("web:1:a", "hello")
    assert gen.seen == []                            # not even a model call
    assert (await store.get("web:1:a"))["title"] == "Mine"


async def test_a_failing_title_call_is_swallowed():
    store = FakeThreads()
    await store.upsert("web:1:a", "web", 1, "2026-07-12T10:00:00+00:00")
    service = ThreadTitleService(store, FakeGenerator(RuntimeError("model down")), SpyBus())
    await service.autotitle("web:1:a", "hi")         # must not raise
    assert (await store.get("web:1:a"))["title"] is None


async def test_without_a_generator_autotitle_is_a_no_op():
    assert ThreadTitleService(FakeThreads(), None, SpyBus()).autotitle("web:1:a", "hi") is None


async def test_the_task_set_is_held_on_the_instance():
    """Two apps in one process (a test suite is exactly that) must not share a task
    set, and a module-level one outlives the app it belongs to."""
    store = FakeThreads()
    await store.upsert("web:1:a", "web", 1, "2026-07-12T10:00:00+00:00")
    a, b = (ThreadTitleService(store, FakeGenerator(), SpyBus()) for _ in range(2))
    task = a.autotitle("web:1:a", "hi")
    assert task in a._tasks and b._tasks == set()
    await asyncio.gather(task)
    assert a._tasks == set()                         # and released when it finishes


async def test_apply_never_overwrites_a_typed_title():
    store = FakeThreads()
    await store.upsert("web:1:a", "web", 1, "2026-07-12T10:00:00+00:00")
    await store.set_title("web:1:a", "Mine", True)
    await ThreadTitleService(store, FakeGenerator(), SpyBus()).apply("web:1:a", "Robot")
    assert (await store.get("web:1:a"))["title"] == "Mine"


# --- SessionService: which model this lane runs ------------------------------


class FakeDefaults:
    """identity's `AccessControl.default_of` by shape — the personal default."""

    def __init__(self, pairs=None):
        self.pairs = dict(pairs or {})
        self.asked: list[int] = []

    async def default_of(self, user_id):
        self.asked.append(user_id)
        return self.pairs.get(user_id, (None, None))


def test_a_defaults_fake_satisfies_the_port():
    assert isinstance(FakeDefaults(), OwnerDefaults)


def lanes(cfg=None, *, threads=None, sessions=None, defaults=None, bus=None):
    """A `SessionService` over fakes, with its router — the same object the
    resolver asks "is this session still live?"."""
    cfg = cfg or Config()
    store = sessions if sessions is not None else FakeSessions()
    return SessionService(threads if threads is not None else FakeThreads(),
                          SessionRouter(store, cfg), cfg,
                          defaults=defaults, events=bus), store


async def a_lane(threads, key="web:1:a", owner=1, when="2026-07-12T10:00:00+00:00"):
    await threads.upsert(key, "web", owner, when)
    return key


async def test_with_nothing_recorded_a_lane_runs_the_deploy_default():
    threads = FakeThreads()
    await a_lane(threads)
    service, _ = lanes(threads=threads, defaults=FakeDefaults())
    choice = await service.effective("web:1:a", 1)
    assert (choice.model, choice.cli_id, choice.effort) == (
        "claude-sonnet-5", "claude-sonnet-5", None)


async def test_the_lanes_own_choice_beats_everything_else():
    threads = FakeThreads()
    await a_lane(threads)
    await threads.set_model("web:1:a", "claude-opus-5-5", "xhigh")
    defaults = FakeDefaults({1: ("claude-fable-5-1", "low")})
    service, store = lanes(threads=threads, defaults=defaults)
    await store.upsert("web:1:a", "sdk-1", "", "2026-07-12T10:00:00+00:00",
                       model="claude-fable-5-1", effort="high")
    choice = await service.effective("web:1:a", 1, now=dt("2026-07-12T11:00:00"))
    assert (choice.model, choice.effort) == ("claude-opus-5-5", "xhigh")


async def test_a_live_session_holds_its_model_against_a_changed_default():
    """P1: a live session's cached prefix is model-scoped, so a default changed
    today must wait for the next session rather than re-write the history."""
    threads = FakeThreads()
    await a_lane(threads)
    defaults = FakeDefaults({1: ("claude-fable-5-1", "low")})
    service, store = lanes(Config(idle_reset_hours=24.0), threads=threads,
                           defaults=defaults)
    await store.upsert("web:1:a", "sdk-1", "", "2026-07-12T10:00:00+00:00",
                       model="claude-opus-5-5", effort="medium")
    choice = await service.effective("web:1:a", 1, now=dt("2026-07-12T20:00:00"))
    assert (choice.model, choice.effort) == ("claude-opus-5-5", "medium")


async def test_an_expired_session_is_ignored_and_not_deleted():
    """Reading the model menu must not end a conversation: the gatherer applies the
    router's own expiry rule, and only `route` (which a turn calls) deletes."""
    threads = FakeThreads()
    await a_lane(threads)
    defaults = FakeDefaults({1: ("claude-fable-5-1", "low")})
    service, store = lanes(Config(idle_reset_hours=24.0), threads=threads,
                           defaults=defaults)
    await store.upsert("web:1:a", "sdk-1", "", "2026-07-10T10:00:00+00:00",
                       model="claude-opus-5-5", effort="medium")
    choice = await service.effective("web:1:a", 1, now=dt("2026-07-12T20:00:00"))
    assert (choice.model, choice.effort) == ("claude-fable-5-1", "low")
    assert store.rows["web:1:a"]["sdk_session_id"] == "sdk-1"     # still there


async def test_the_daily_boundary_expires_a_session_for_the_resolver_too():
    threads = FakeThreads()
    await a_lane(threads)
    defaults = FakeDefaults({1: ("claude-fable-5-1", None)})
    service, store = lanes(Config(daily_reset_hour=4), threads=threads,
                           defaults=defaults)
    await store.upsert("web:1:a", "sdk-1", "", "2026-07-12T03:00:00+00:00",
                       model="claude-opus-5-5", effort="medium")
    # 03:00 is before the 04:00 boundary the 05:00 read measures against.
    choice = await service.effective("web:1:a", 1, now=dt("2026-07-12T05:00:00"))
    assert choice.model == "claude-fable-5-1"
    # …and before that boundary the same row is still the same conversation.
    choice = await service.effective("web:1:a", 1, now=dt("2026-07-12T03:30:00"))
    assert choice.model == "claude-opus-5-5"


async def test_a_live_session_from_before_the_feature_counts_as_the_deploy_default():
    """Its pair is NULL because it ran before 0008 — which means it ran
    `cfg.model`, not "nothing". Reading it as nothing would let a default set
    today move a conversation that is already warm on another model."""
    threads = FakeThreads()
    await a_lane(threads)
    defaults = FakeDefaults({1: ("claude-fable-5-1", "low")})
    service, store = lanes(Config(idle_reset_hours=24.0), threads=threads,
                           defaults=defaults)
    await store.upsert("web:1:a", "sdk-1", "", "2026-07-12T10:00:00+00:00")
    choice = await service.effective("web:1:a", 1, now=dt("2026-07-12T12:00:00"))
    assert (choice.model, choice.effort) == ("claude-sonnet-5", None)


async def test_a_shared_lane_never_reads_anybodys_personal_default():
    """A group, a forum topic and a `cron:` lane belong to the room, not to
    whoever spoke last."""
    threads = FakeThreads()
    defaults = FakeDefaults({1: ("claude-fable-5-1", "low")})
    service, _ = lanes(threads=threads, defaults=defaults)
    for key in ("tg:supergroup:-5", "tg:supergroup:-5:9", "cron:7"):
        await a_lane(threads, key)
        choice = await service.effective(key, 1)
        assert choice.model == "claude-sonnet-5", key
    assert defaults.asked == []                 # not even asked


async def test_a_private_chats_topic_is_its_users_own_lane():
    threads = FakeThreads()
    await a_lane(threads, "tg:private:1:t9")
    defaults = FakeDefaults({1: ("claude-opus-5-5", "high")})
    service, _ = lanes(threads=threads, defaults=defaults)
    choice = await service.effective("tg:private:1:t9", 1)
    assert (choice.model, choice.effort) == ("claude-opus-5-5", "high")


async def test_a_pair_that_left_the_offered_list_is_skipped():
    """A model dropped from `models:` moves every lane that was on it at its next
    turn — one cold cache write each, and no lane stuck on a model nobody offers."""
    threads = FakeThreads()
    await a_lane(threads)
    await threads.set_model("web:1:a", "claude-opus-5", "high")   # not in `models:`
    service, _ = lanes(threads=threads, defaults=FakeDefaults())
    assert (await service.effective("web:1:a", 1)).model == "claude-sonnet-5"


async def test_an_effort_the_model_does_not_take_becomes_none():
    threads = FakeThreads()
    await a_lane(threads)
    await threads.set_model("web:1:a", "claude-opus-5-5", "turbo")
    service, _ = lanes(threads=threads, defaults=FakeDefaults())
    choice = await service.effective("web:1:a", 1)
    assert (choice.model, choice.effort) == ("claude-opus-5-5", None)


async def test_a_lane_with_no_thread_row_still_resolves():
    """The turn asks before the thread row is certain to exist; an unknown key has
    no choice of its own and falls through, it does not raise."""
    service, _ = lanes(defaults=FakeDefaults({1: ("claude-opus-5-5", "low")}))
    assert (await service.effective("web:1:never", 1)).model == "claude-opus-5-5"


async def test_setting_a_lanes_pair_writes_it_and_tells_every_tab():
    threads = FakeThreads()
    await a_lane(threads)
    bus = SpyBus()
    service, _ = lanes(threads=threads, defaults=FakeDefaults(), bus=bus)
    assert await service.set_model("web:1:a", "claude-opus-5-5", "xhigh") == \
        ("claude-opus-5-5", "xhigh")
    assert await threads.model_of("web:1:a") == ("claude-opus-5-5", "xhigh")
    # `thread.title`'s pattern: an empty turn_id, and only the EXPLICIT pair —
    # what the lane resolves to depends on the reader's own default.
    assert bus.emitted == [("web:1:a", "", "thread.model",
                            {"model": "claude-opus-5-5", "effort": "xhigh"})]


async def test_clearing_a_lanes_pair_switches_its_live_session_to_the_default():
    """Clearing is a switch to the lane's default NOW. Left alone, the session
    would keep running the model just cleared until it expired; nulled, it would
    read as a pre-0008 session — the deploy default — and hide the owner's own."""
    threads = FakeThreads()
    await a_lane(threads)
    bus = SpyBus()
    service, store = lanes(Config(idle_reset_hours=24.0), threads=threads,
                           defaults=FakeDefaults({1: ("claude-fable-5-1", "low")}),
                           bus=bus)
    await store.upsert("web:1:a", "sdk-1", "", "2026-07-12T10:00:00+00:00",
                       model="claude-opus-5-5", effort="medium")
    await service.set_model("web:1:a", "claude-opus-5-5", "medium")

    assert await service.set_model("web:1:a", None, "high") == (None, None)
    assert await threads.model_of("web:1:a") == (None, None)
    row = store.rows["web:1:a"]
    # the live session now runs the owner's default, and it is still that session
    assert (row["model"], row["effort"]) == ("claude-fable-5-1", "low")
    assert row["sdk_session_id"] == "sdk-1"
    assert row["updated_at"] == "2026-07-12T10:00:00+00:00"   # a click is not activity
    assert bus.emitted[-1][3] == {"model": None, "effort": None}
    choice = await service.effective("web:1:a", 1, now=dt("2026-07-12T12:00:00"))
    assert (choice.model, choice.effort) == ("claude-fable-5-1", "low")


async def test_clearing_a_shared_lanes_pair_lands_on_the_deploy_default():
    """A room takes nobody's personal default — not even the owner's."""
    threads = FakeThreads()
    await a_lane(threads, "tg:supergroup:-100123", owner=1)
    service, store = lanes(threads=threads,
                           defaults=FakeDefaults({1: ("claude-fable-5-1", "low")}))
    await store.upsert("tg:supergroup:-100123", "sdk-9", "", "2026-07-12T10:00:00+00:00",
                       model="claude-opus-5-5", effort=None)
    await service.set_model("tg:supergroup:-100123", "claude-opus-5-5", None)
    await service.set_model("tg:supergroup:-100123", None, None)
    row = store.rows["tg:supergroup:-100123"]
    assert (row["model"], row["effort"]) == ("claude-sonnet-5", None)


async def test_a_lane_with_no_thread_row_cannot_be_given_a_model():
    service, _ = lanes()
    with pytest.raises(NotFound):
        await service.set_model("web:1:never", "claude-opus-5-5", None)


async def test_a_write_the_resolver_would_ignore_is_refused():
    threads = FakeThreads()
    await a_lane(threads)
    bus = SpyBus()
    service, _ = lanes(threads=threads, bus=bus)
    with pytest.raises(ValueError, match="bad_model"):
        await service.set_model("web:1:a", "claude-opus-5", None)     # not offered
    with pytest.raises(ValueError, match="bad_effort"):
        await service.set_model("web:1:a", "claude-opus-5-5", "turbo")
    assert await threads.model_of("web:1:a") == (None, None) and bus.emitted == []


async def test_a_model_named_by_its_cli_spelling_is_stored_as_the_catalog_id():
    """`[1m]` is how a CLI version gives a model its full window; it is never part
    of the id the database stores or the wire carries."""
    threads = FakeThreads()
    await a_lane(threads)
    service, _ = lanes(threads=threads)
    assert await service.set_model("web:1:a", "claude-opus-5-5[1m]", "high") == \
        ("claude-opus-5-5", "high")


def test_the_offered_list_is_computed_once_per_config():
    service, _ = lanes()
    assert service.offered_ids() == ["claude-sonnet-5", "claude-opus-5-5",
                                     "claude-fable-5-1"]
    assert service.offered_models() is service.offered_models()
