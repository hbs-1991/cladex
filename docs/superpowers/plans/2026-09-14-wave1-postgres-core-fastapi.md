# Wave 1 — PostgreSQL, `core/`, structlog, import-linter, FastAPI shell — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the app runs on PostgreSQL instead of SQLite, logs through structlog, has a
`core/` package with the exception hierarchy and DB machinery, an import-linter gate,
and serves its existing routes from a FastAPI application — with every one of the 814
existing tests still green (against a real PostgreSQL).

**Architecture:** Step 0 + Step 1 of
`docs/superpowers/specs/2026-09-14-modular-fastapi-architecture-design.md`. The
`Database` facade in `db.py` keeps every public method it has today but is re-implemented
over a SQLAlchemy async engine (asyncpg). Its ORM models already live where the target
architecture wants them — `modules/<name>/domain/models.py` — so Wave 2 moves code, not
tables. Schema is owned by Alembic (`core/db/migrations/`); `main.py` runs `upgrade head`
at boot. The three raw-SQL stores (`cron/store.py`, `extensions/store.py`,
`extensions/tokens.py`) and one curator query are ported to SQLAlchemy Core. FTS5 becomes
a `tsvector` generated column with a GIN index. The Starlette app becomes a `FastAPI`
instance that serves the existing `Route` lists unchanged; per-module `APIRouter`
conversion is Wave 2.

**Tech Stack:** Python 3.12 (image) / 3.13 (dev venv), SQLAlchemy 2.0 async, asyncpg,
Alembic 1.13+, FastAPI 0.115+, structlog 24+, import-linter 2.x, PostgreSQL 16, pytest +
pytest-asyncio (auto mode).

Spec: `docs/superpowers/specs/2026-09-14-modular-fastapi-architecture-design.md`.

## Locked Decisions

- **`Database.open(url: str | None = None)`** — the URL, not a path. `None` reads
  `CH_DATABASE_URL`. Tests call `Database.open()` with no argument; `conftest.py` sets the
  env var to a per-pytest-process database. `open()` does NOT run migrations.
- **Migrations run at boot, in a thread**: `core.db.migrate.upgrade_head(url)` wraps
  Alembic's sync `command.upgrade` in `asyncio.to_thread` (Alembic's async env.py calls
  `asyncio.run`, which cannot run inside the live loop). `main.build_app` calls it once
  before `Database.open`.
- **Schema is 1:1 with today's SQLite schema** (same table and column names, ISO-8601
  text timestamps kept as `TEXT`, lexicographic comparisons preserved) with three
  deliberate deviations: `messages_fts` → `messages` (+ `id BIGSERIAL`, `content_tsv`
  generated column, GIN index); `job_runs` and `turn_costs` gain `id BIGSERIAL` primary
  keys (the ORM needs one); Telegram ids (`deliver_chat_id`, `owner_user_id`,
  `installed_by`) are `BIGINT`. `turn_events.payload` is `JSONB`. `users` / `owner_id`
  arrive with the identity module in Wave 2 (Alembic makes that a one-column migration).
- **Full-text search** = `plainto_tsquery('simple', :q)` against the generated column,
  ranked by `ts_rank`, snippet via `ts_headline(... 'StartSel=[, StopSel=], MaxWords=12,
  MinWords=6, MaxFragments=1, FragmentDelimiter=" … "')`. `search_messages` never raises
  on odd input (plainto_tsquery does not parse operators).
- **ORM models live in `modules/<m>/domain/models.py`** from day one: `sessions`
  (sessions, messages, threads), `scheduler` (jobs, job_runs), `turns` (turn_events,
  turn_costs), `extensions` (extensions, oauth_tokens), `files` (files). `meta` lives in
  `core/db/models.py`. `core/db/registry.py` imports them all for Alembic; it is the one
  `core → modules` import, whitelisted in import-linter.
- **`ApplicationException(status, code, message)`** in `core/exceptions.py` is the root
  of every application error; `web/errors.ApiError` becomes a subclass (same constructor,
  same fields) so nothing that raises it changes. One FastAPI exception handler renders
  `{"error": {"code", "message"}}` for the whole hierarchy.
- **structlog routes through stdlib logging**: `structlog.stdlib.LoggerFactory` +
  `ProcessorFormatter` on the root handler, wrapped in the existing
  `logsafe.RedactingFormatter`. `caplog`, third-party loggers and the token redaction
  keep working; `log = structlog.get_logger(__name__)` replaces `logging.getLogger`.
- **FastAPI app serves Starlette `Route` objects as-is** (`FastAPI(routes=...)`); OpenAPI
  is off (`openapi_url=None`) until routers become `APIRouter`s in Wave 2.
- **Test database**: `CH_TEST_DATABASE_URL` (default
  `postgresql+asyncpg://hermes:hermes@127.0.0.1:55432/hermes`) names a *server*; each
  pytest process creates `hermes_test_<8 hex>` from it, runs `alembic upgrade head`, and
  an autouse fixture `TRUNCATE ... RESTART IDENTITY CASCADE`s every table before each
  test. Parallel pytest processes never share a database.
- **`aiosqlite` leaves `pyproject.toml`**; `scripts/import_sqlite.py` reads the old file
  with the stdlib `sqlite3` module.

## Global Constraints

- Backend checks: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` (needs the
  test PostgreSQL: `make test-db` starts `hermes-test-pg` on 127.0.0.1:55432),
  `.venv/Scripts/python.exe -m ruff check src tests`, `.venv/Scripts/lint-imports`.
- No `print()`; after Task 10 no `logging.getLogger` outside `core/logging.py` and
  `logsafe.py`.
- No `HTTPException` outside `web/`; services raise `ApplicationException` subclasses.
- External contracts unchanged: REST/SSE wire shapes consumed by `web/src/api/client.ts`,
  Telegram behaviour, `config.yaml.example` keys (only additions), compose entrypoints.
- Secrets never enter `config.yaml`: the database URL is `CH_DATABASE_URL` in the
  environment (`.env` / compose), read by `load_config`.
- `docs/llm-wiki/` pages whose evidence changes are updated and re-fingerprinted
  (`.llm-wiki/llm-wiki fingerprint --page <page>`), then `.llm-wiki/llm-wiki validate`.

## Architecture Diagram

```mermaid
flowchart LR
  main[main.py] -->|upgrade_head| alembic[core/db/migrate]
  main -->|Database.open| facade[db.py facade]
  facade --> engine[core/db/engine\nAsyncEngine]
  stores[cron/store · extensions/store · extensions/tokens] --> engine
  engine --> pg[(PostgreSQL)]
  alembic --> pg
  registry[core/db/registry] --> models[modules/*/domain/models.py]
  alembic --> registry
  web[web/app.py FastAPI] -->|Route lists| routers[web/*.py]
  web -->|exception handler| exc[core/exceptions]
```

## File map

```
src/claude_hermes/
├── core/
│   ├── __init__.py
│   ├── exceptions.py            # ApplicationException hierarchy
│   ├── logging.py               # configure(): structlog → stdlib → RedactingFormatter
│   └── db/
│       ├── __init__.py
│       ├── base.py              # Base(DeclarativeBase) + naming convention
│       ├── engine.py            # make_engine(url), make_session_factory(engine)
│       ├── models.py            # Meta
│       ├── registry.py          # imports every module's models; exposes Base.metadata
│       ├── migrate.py           # alembic_config(url), upgrade_head_sync/upgrade_head
│       └── migrations/
│           ├── env.py
│           ├── script.py.mako
│           └── versions/0001_initial.py
├── modules/
│   ├── __init__.py
│   ├── sessions/{__init__.py, domain/__init__.py, domain/models.py}     # Session, Message, Thread
│   ├── scheduler/{...}                                                 # Job, JobRun
│   ├── turns/{...}                                                     # TurnEvent, TurnCost
│   ├── extensions/{...}                                                # Extension, OauthToken
│   └── files/{...}                                                     # FileRecord
├── db.py                        # Database facade over the engine (same public methods)
├── config.py                    # + database_url
├── main.py                      # upgrade_head + Database.open(cfg.database_url); core.logging.configure
├── cron/store.py                # JobStore over SQLAlchemy Core
├── extensions/store.py          # ExtensionStore over SQLAlchemy Core
├── extensions/tokens.py         # TokenStore over SQLAlchemy Core
├── learning/curator.py          # _idle_hours via db.last_message_ts()
└── web/{app.py, errors.py}      # FastAPI + ApplicationException handler
scripts/import_sqlite.py         # state.db → PostgreSQL, one shot
tests/conftest.py                # per-process database + per-test truncate
tests/test_migrations.py         # upgrade head == ORM metadata
tests/test_import_sqlite.py
tests/test_core_logging.py
```

---

### Task 1: `core/` package, ORM models, Alembic

**Files:**
- Modify: `pyproject.toml` (dependencies, package-data)
- Create: `src/claude_hermes/core/__init__.py`, `src/claude_hermes/core/exceptions.py`
- Create: `src/claude_hermes/core/db/__init__.py`, `base.py`, `engine.py`, `models.py`, `registry.py`, `migrate.py`
- Create: `src/claude_hermes/core/db/migrations/env.py`, `script.py.mako`, `versions/0001_initial.py`
- Create: `src/claude_hermes/modules/__init__.py` and, for each of `sessions`, `scheduler`, `turns`, `extensions`, `files`: `modules/<m>/__init__.py`, `modules/<m>/domain/__init__.py`, `modules/<m>/domain/models.py`
- Create: `tests/conftest.py`, `tests/test_migrations.py`
- Modify: `Makefile` (add `test-db`)

**Interfaces:**
- Reuses: the column list in `src/claude_hermes/db.py:_SCHEMA` (the schema being reproduced); `tests/` conventions (`asyncio_mode = "auto"`).
- Produces:
  - `core.exceptions.ApplicationException(status: int = 500, code: str = "internal", message: str = "")` with attributes `.status`, `.code`, `.message`; subclasses `NotFound(404, "not_found")`, `Forbidden(403, "forbidden")`, `Unauthorized(401, "unauthorized")`, `Conflict(409, "conflict")`, `InvalidInput(422, "invalid")`. Each subclass takes `(message: str = "", *, code: str | None = None)`.
  - `core.db.engine.make_engine(url: str) -> AsyncEngine`, `make_session_factory(engine) -> async_sessionmaker[AsyncSession]`.
  - `core.db.migrate.alembic_config(url) -> alembic.config.Config`, `upgrade_head_sync(url: str) -> None`, `async upgrade_head(url: str) -> None`.
  - `core.db.registry.metadata` (== `Base.metadata` with every table registered).
  - ORM classes listed in the Locked Decisions, with `__tablename__` equal to today's names (`messages` replaces `messages_fts`).
  - `tests/conftest.py` exports fixture `test_database` (session, autouse; yields the URL and sets `CH_DATABASE_URL`) and `_clean_tables` (function, autouse).

- [ ] **Step 1: dependencies and package data in `pyproject.toml`**

Replace `"aiosqlite>=0.20",` with nothing and add these to `dependencies`:

```toml
    "sqlalchemy[asyncio]>=2.0.30",
    "asyncpg>=0.30",
    "alembic>=1.13",
    "fastapi>=0.115",
    "structlog>=24.1",
```

Add to `dev` extras: `"import-linter>=2.0"`. Add after `[tool.setuptools.packages.find]`:

```toml
[tool.setuptools.package-data]
"claude_hermes.core.db" = ["migrations/*.mako", "migrations/versions/*.py"]
```

- [ ] **Step 2: `core/exceptions.py`**

```python
"""The application error hierarchy. Services raise these; the HTTP layer renders
them as {"error": {"code", "message"}} with `.status`. Nothing here imports a web
framework — the mapping to a response lives in the presentation layer."""
from __future__ import annotations


class ApplicationException(Exception):
    status: int = 500
    code: str = "internal"

    def __init__(self, status: int | None = None, code: str | None = None,
                 message: str = ""):
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code
        self.message = message
        super().__init__(message)


class _Typed(ApplicationException):
    def __init__(self, message: str = "", *, code: str | None = None):
        super().__init__(None, code, message)


class NotFound(_Typed):
    status, code = 404, "not_found"


class Forbidden(_Typed):
    status, code = 403, "forbidden"


class Unauthorized(_Typed):
    status, code = 401, "unauthorized"


class Conflict(_Typed):
    status, code = 409, "conflict"


class InvalidInput(_Typed):
    status, code = 422, "invalid"
```

- [ ] **Step 3: `core/db/base.py`, `engine.py`, `models.py`**

```python
# base.py
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
```

```python
# engine.py
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def make_engine(url: str) -> AsyncEngine:
    # pool_pre_ping: a container that slept through a Postgres restart must not
    # hand the first request a dead connection.
    return create_async_engine(url, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
```

```python
# models.py
from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Meta(Base):
    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
```

- [ ] **Step 4: module models**

`modules/sessions/domain/models.py`:

```python
from sqlalchemy import BigInteger, Computed, Index, Integer, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from claude_hermes.core.db.base import Base


class Session(Base):
    __tablename__ = "sessions"
    session_key: Mapped[str] = mapped_column(Text, primary_key=True)
    sdk_session_id: Mapped[str | None] = mapped_column(Text)
    system_append: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class Message(Base):
    """Cross-session recall. `content_tsv` is generated by PostgreSQL; the GIN
    index is what makes `search_messages` a full-text query, not a scan."""
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_key: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[str] = mapped_column(Text, nullable=False)
    content_tsv = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', content)", persisted=True))
    __table_args__ = (
        Index("ix_messages_session_key_ts", "session_key", "ts"),
        Index("ix_messages_content_tsv", "content_tsv", postgresql_using="gin"),
    )


class Thread(Base):
    __tablename__ = "threads"
    session_key: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str | None] = mapped_column(Text)
    title_manual: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger)
    archived_at: Mapped[str | None] = mapped_column(Text)
    last_event_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
```

`modules/scheduler/domain/models.py`:

```python
from sqlalchemy import BigInteger, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from claude_hermes.core.db.base import Base


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    deliver_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deliver_thread_id: Mapped[int | None] = mapped_column(BigInteger)
    origin_session_key: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    next_run_at: Mapped[str | None] = mapped_column(Text)
    last_run_at: Mapped[str | None] = mapped_column(Text)
    last_status: Mapped[str | None] = mapped_column(Text)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ts: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (Index("job_runs_job", "job_id", "ts"),)
```

`modules/turns/domain/models.py`:

```python
from sqlalchemy import BigInteger, Float, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from claude_hermes.core.db.base import Base


class TurnEvent(Base):
    __tablename__ = "turn_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_key: Mapped[str] = mapped_column(Text, nullable=False)
    turn_id: Mapped[str] = mapped_column(Text, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    __table_args__ = (Index("turn_events_lane", "session_key", "id"),)


class TurnCost(Base):
    __tablename__ = "turn_costs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(Text, nullable=False)
    session_key: Mapped[str] = mapped_column(Text, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
```

`modules/extensions/domain/models.py`:

```python
from sqlalchemy import BigInteger, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from claude_hermes.core.db.base import Base


class Extension(Base):
    __tablename__ = "extensions"
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_version: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="enabled")
    installed_at: Mapped[str] = mapped_column(Text, nullable=False)
    installed_by: Mapped[int | None] = mapped_column(BigInteger)
    last_error: Mapped[str | None] = mapped_column(Text)
    tool_count: Mapped[int | None] = mapped_column(Integer)
    tool_names: Mapped[str | None] = mapped_column(Text)     # JSON list as text (see store.tool_names)


class OauthToken(Base):
    __tablename__ = "oauth_tokens"
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    issuer: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    client_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    client_secret: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    access_token: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    expires_at: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
```

`modules/files/domain/models.py`:

```python
from sqlalchemy import BigInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from claude_hermes.core.db.base import Base


class FileRecord(Base):
    __tablename__ = "files"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_key: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
```

Every `__init__.py` listed in Files is empty except `modules/<m>/domain/__init__.py`,
which is also empty (models are imported by path).

- [ ] **Step 5: `core/db/registry.py`, `migrate.py`, Alembic env**

```python
# registry.py
"""Every ORM model, imported once so Base.metadata is complete. This is the one
place core reaches into modules — Alembic needs the whole schema in one object,
and a module that forgets to register here silently ships without a table."""
from claude_hermes.core.db.base import Base
from claude_hermes.core.db import models as _core_models  # noqa: F401
from claude_hermes.modules.extensions.domain import models as _ext  # noqa: F401
from claude_hermes.modules.files.domain import models as _files  # noqa: F401
from claude_hermes.modules.scheduler.domain import models as _sched  # noqa: F401
from claude_hermes.modules.sessions.domain import models as _sessions  # noqa: F401
from claude_hermes.modules.turns.domain import models as _turns  # noqa: F401

metadata = Base.metadata
```

```python
# migrate.py
from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def upgrade_head_sync(url: str) -> None:
    command.upgrade(alembic_config(url), "head")


async def upgrade_head(url: str) -> None:
    # env.py calls asyncio.run(); that cannot happen on the running loop, so the
    # whole Alembic run goes to a worker thread with a loop of its own.
    await asyncio.to_thread(upgrade_head_sync, url)
```

`migrations/env.py` (the standard async template, trimmed):

```python
import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from claude_hermes.core.db.registry import metadata

config = context.config
target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"),
                      target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
```

`migrations/script.py.mako`: copy Alembic's default template verbatim
(`alembic init` output). `versions/0001_initial.py`: `revision = "0001"`,
`down_revision = None`; `upgrade()` creates the eleven tables with exactly the columns
of Step 3–4 (use `op.create_table` + `sa.Column`; `content_tsv` is
`sa.Column("content_tsv", postgresql.TSVECTOR(), sa.Computed("to_tsvector('simple', content)", persisted=True))`;
indexes `ix_messages_session_key_ts`, `ix_messages_content_tsv` (`postgresql_using="gin"`),
`job_runs_job`, `turn_events_lane`); `downgrade()` drops them in reverse order.

- [ ] **Step 6: `tests/conftest.py`**

```python
"""Every test talks to a real PostgreSQL. One database per pytest process (so
parallel runs never collide), migrated with Alembic — which makes every run a
check that `upgrade head` builds the schema — and truncated before each test."""
from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from claude_hermes.core.db.migrate import upgrade_head_sync
from claude_hermes.core.db.registry import metadata

SERVER_URL = os.environ.get(
    "CH_TEST_DATABASE_URL", "postgresql+asyncpg://hermes:hermes@127.0.0.1:55432/hermes")


def _dsn(url: str) -> str:
    """SQLAlchemy URL -> plain asyncpg DSN."""
    return str(make_url(url).set(drivername="postgresql").render_as_string(hide_password=False))


async def _admin(sql: str) -> None:
    conn = await asyncpg.connect(_dsn(SERVER_URL))
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def test_database():
    name = f"hermes_test_{uuid.uuid4().hex[:8]}"
    try:
        asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    except OSError as e:
        pytest.exit(f"PostgreSQL test server not reachable at {SERVER_URL} ({e}); "
                    "run `make test-db` or set CH_TEST_DATABASE_URL", returncode=3)
    url = str(make_url(SERVER_URL).set(database=name).render_as_string(hide_password=False))
    upgrade_head_sync(url)
    os.environ["CH_DATABASE_URL"] = url
    yield url
    asyncio.run(_admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture(autouse=True)
async def _clean_tables(test_database):
    tables = ", ".join(f'"{t.name}"' for t in metadata.sorted_tables)
    conn = await asyncpg.connect(_dsn(test_database))
    try:
        await conn.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    finally:
        await conn.close()
```

- [ ] **Step 7: `tests/test_migrations.py`**

```python
"""The migrations and the ORM must describe the same schema, or the next
`alembic revision --autogenerate` ships a surprise."""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from claude_hermes.core.db.registry import metadata


async def test_upgrade_head_matches_orm_metadata(test_database):
    engine = create_async_engine(test_database)
    try:
        async with engine.connect() as conn:
            diff = await conn.run_sync(
                lambda sync_conn: compare_metadata(
                    MigrationContext.configure(sync_conn), metadata))
    finally:
        await engine.dispose()
    assert diff == []
```

- [ ] **Step 8: `Makefile` target**

```make
.PHONY: test-db
test-db: ## a throwaway PostgreSQL 16 for the test suite (127.0.0.1:55432)
	-docker rm -f hermes-test-pg >/dev/null 2>&1
	docker run -d --name hermes-test-pg -e POSTGRES_USER=hermes -e POSTGRES_PASSWORD=hermes \
	  -e POSTGRES_DB=hermes -p 127.0.0.1:55432:5432 postgres:16-alpine
```

- [ ] **Step 9: verify**

Run: `.venv/Scripts/python.exe -m pytest tests/test_migrations.py -q -p no:cacheprovider`
Expected: 1 passed (and the process created + dropped a `hermes_test_*` database).
Run: `.venv/Scripts/python.exe -m ruff check src/claude_hermes/core src/claude_hermes/modules tests/conftest.py tests/test_migrations.py`
Expected: All checks passed.

---

### Task 2: `Database` facade over SQLAlchemy; tests call `Database.open()`

**Files:**
- Modify: `src/claude_hermes/db.py` (rewrite)
- Modify: `src/claude_hermes/config.py` (add `database_url`)
- Modify: `tests/test_db.py`
- Modify (mechanical): every test file that contains `Database.open(tmp_path / "state.db")` or `Database.open(path)` → `Database.open()` (33 files, listed by `grep -rl "Database.open" tests`), including `tests/web_helpers.py`.

**Interfaces:**
- Reuses: every public method of today's `Database` (names, parameters, return shapes: dicts / lists of dicts / `int` ids / `None`), Task 1's `make_engine`, models.
- Consumes: `core.db.engine`, `modules/*/domain/models.py`.
- Produces:
  - `Database.open(url: str | None = None) -> Database` (no migrations; raises `RuntimeError("CH_DATABASE_URL is not set")` when neither is given).
  - `Database.engine -> AsyncEngine`; `Database.session() -> AsyncSession` (from `make_session_factory`); `Database.close()` disposes the engine.
  - **New** `Database.last_message_ts() -> str | None` (max `messages.ts`), for the curator.
  - `Config.database_url: str` read from `CH_DATABASE_URL`.
  - `db.conn` **no longer exists**.

- [ ] **Step 1: config**

In `Config` add `database_url: str = ""` next to `web_secret`; in `load_config` add
`cfg.database_url = os.environ.get("CH_DATABASE_URL", "")` and add `"database_url"` to
the tuple of keys `config.yaml` may not set.

- [ ] **Step 2: rewrite `db.py`**

Keep the module docstring short. Implementation pattern for every method — Core
statements on `engine.begin()`, rows as dicts:

```python
from __future__ import annotations

import os

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .core.db.engine import make_engine, make_session_factory
from .core.db.models import Meta
from .modules.files.domain.models import FileRecord
from .modules.sessions.domain.models import Message, Session, Thread
from .modules.turns.domain.models import TurnCost, TurnEvent


class Database:
    """The store every runtime object is handed. Same methods as the SQLite era;
    each is one statement on a short transaction, so a caller holds no session."""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine
        self._sessions = make_session_factory(engine)

    @classmethod
    async def open(cls, url: str | None = None) -> "Database":
        url = url or os.environ.get("CH_DATABASE_URL", "")
        if not url:
            raise RuntimeError("CH_DATABASE_URL is not set")
        return cls(make_engine(url))

    async def close(self) -> None:
        await self._engine.dispose()

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    def session(self) -> AsyncSession:
        return self._sessions()

    async def get_session(self, session_key: str) -> dict | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                select(Session).where(Session.session_key == session_key))).mappings().first()
        return dict(row) if row else None

    async def upsert_session(self, session_key: str, sdk_session_id: str | None,
                             system_append: str, now: str) -> None:
        stmt = pg_insert(Session).values(
            session_key=session_key, sdk_session_id=sdk_session_id,
            system_append=system_append, created_at=now, updated_at=now)
        stmt = stmt.on_conflict_do_update(index_elements=[Session.session_key], set_={
            "sdk_session_id": stmt.excluded.sdk_session_id,
            "system_append": stmt.excluded.system_append,
            "updated_at": stmt.excluded.updated_at})
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
```

Continue in the same style for `touch_session`, `reset_session`, `reset_all_sessions`,
`record_message` (→ `messages`), `last_message` (`ORDER BY ts DESC LIMIT 1`, returns
`{"role","content","ts"}`), `get_meta`/`set_meta` (upsert), `record_cost`, `cost_by_day`
(`func.substr(TurnCost.ts, 1, 10).label("day")`, `func.sum(TurnCost.cost_usd)`, group/order
by day desc, limit), `append_event` (returns the new id via `.returning(TurnEvent.id)`;
`payload` is stored as the dict itself — JSONB), `events_after` (payload comes back as a
dict already), `upsert_thread` (on conflict update `last_event_at` only), `get_thread`,
`list_threads` (same predicate as today, with `exists(select(1).where(TurnEvent.session_key == Thread.session_key))`),
`set_thread_title`, `set_thread_archived`, `insert_file`, `get_file`.

`search_messages`:

```python
    async def search_messages(self, query: str, limit: int = 8) -> list[dict]:
        q = func.plainto_tsquery("simple", query)
        stmt = (
            select(Message.session_key, Message.role, Message.ts,
                   func.ts_headline("simple", Message.content, q,
                                    "StartSel=[, StopSel=], MaxWords=12, MinWords=6, "
                                    "MaxFragments=1, FragmentDelimiter=\" … \"").label("snippet"))
            .where(Message.content_tsv.op("@@")(q))
            .order_by(func.ts_rank(Message.content_tsv, q).desc(), Message.id)
            .limit(limit))
        async with self._engine.connect() as conn:
            return [dict(r) for r in (await conn.execute(stmt)).mappings().all()]

    async def last_message_ts(self) -> str | None:
        async with self._engine.connect() as conn:
            return (await conn.execute(select(func.max(Message.ts)))).scalar_one_or_none()
```

- [ ] **Step 3: tests**

In `tests/test_db.py`: delete `test_jobs_migration_adds_deliver_thread_id_and_is_idempotent`
(SQLite ALTER migrations no longer exist; `tests/test_migrations.py` covers the schema).
Replace every `Database.open(tmp_path / "state.db")` with `Database.open()`. Add:

```python
async def test_search_is_safe_on_operator_soup():
    db = await Database.open()
    await db.record_message("tg:private:1", "user", "ingress notes", NOW)
    assert await db.search_messages("AND OR ( ingress") != []      # plainto_tsquery ignores syntax
    assert await db.search_messages("") == []
    await db.close()


async def test_last_message_ts_is_the_max():
    db = await Database.open()
    assert await db.last_message_ts() is None
    await db.record_message("k", "user", "a", "2026-07-12T10:00:00+00:00")
    await db.record_message("k", "user", "b", "2026-07-12T12:00:00+00:00")
    assert await db.last_message_ts() == "2026-07-12T12:00:00+00:00"
    await db.close()
```

Then the mechanical sweep over the other 32 files:

```bash
grep -rl "Database.open" tests | xargs sed -i -E 's/Database\.open\((tmp_path \/ "state\.db"|path)\)/Database.open()/g'
grep -rn "Database.open(" tests | grep -v "Database.open()"     # must print nothing
```

- [ ] **Step 4: verify the facade's own tests and the runtime tests that only need the facade**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db.py tests/test_acl.py tests/test_sessions.py tests/test_turn_bus.py tests/test_costs.py tests/test_filestore.py tests/test_titles.py tests/test_reset_all.py -q -p no:cacheprovider`
Expected: all pass. (Tests touching `JobStore`, `ExtensionStore`, `TokenStore` and the
curator fail until Tasks 3–5 land — expected at this point.)

---

### Task 3: `JobStore` over SQLAlchemy Core

**Files:**
- Modify: `src/claude_hermes/cron/store.py`
- Test: `tests/test_cron_store.py`, `tests/test_cron_ticker.py`, `tests/test_cron_executor.py`, `tests/test_cron_tool.py`, `tests/test_web_routines.py` (unchanged; they must pass)

**Interfaces:**
- Reuses: every `JobStore` method signature and return shape as today (`dict` rows with the same keys — `runs()` returns `{"ts","status"}` only); `Database.engine` from Task 2; `Job`, `JobRun` from Task 1.
- Produces: nothing new.

- [ ] **Step 1: run the tests to see the failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cron_store.py -q -p no:cacheprovider`
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'conn'`.

- [ ] **Step 2: port**

```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, insert, select, update

from ..db import Database
from ..modules.scheduler.domain.models import Job, JobRun


class JobStore:
    def __init__(self, db: Database):
        self._engine = db.engine

    async def create(self, name, kind, value, prompt, deliver_chat_id, origin_session_key,
                     next_run_at, deliver_thread_id=None, timeout_seconds=None) -> int:
        stmt = insert(Job).values(
            name=name, kind=kind, value=value, prompt=prompt,
            deliver_chat_id=deliver_chat_id, deliver_thread_id=deliver_thread_id,
            origin_session_key=origin_session_key, next_run_at=next_run_at,
            timeout_seconds=timeout_seconds,
            created_at=datetime.now(timezone.utc).isoformat()).returning(Job.id)
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt)).scalar_one()

    async def get(self, job_id: int) -> dict | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(select(Job).where(Job.id == job_id))).mappings().first()
        return dict(row) if row else None
```

Port `by_thread` (order by `Job.id.desc()`, limit 1), `list_jobs` (order by id), `due`
(`Job.state == "active"`, `Job.next_run_at <= now_iso`, order by `next_run_at`),
`reschedule`, `set_state`, `update` (same whitelist; build `update(Job).values(**{c: fields[c] ...})`),
`record_run` (one transaction: update the job row, insert the `JobRun`), `runs`
(`select(JobRun.ts, JobRun.status)` order by ts desc, limit), `delete` (runs, then job, one
transaction). Keep the docstrings that explain *why* (whitelist, history vs latest).

- [ ] **Step 3: verify**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cron_store.py tests/test_cron_ticker.py tests/test_cron_executor.py tests/test_cron_tool.py tests/test_web_routines.py -q -p no:cacheprovider`
Expected: all pass.
Run: `.venv/Scripts/python.exe -m ruff check src/claude_hermes/cron`

---

### Task 4: `ExtensionStore` and `TokenStore` over SQLAlchemy Core

**Files:**
- Modify: `src/claude_hermes/extensions/store.py`, `src/claude_hermes/extensions/tokens.py`
- Modify: `tests/test_ext_store.py` (delete `test_an_old_database_gains_the_column`)
- Test (unchanged, must pass): `tests/test_tokens.py`, `tests/test_oauth.py`, `tests/test_ext_manage.py`, `tests/test_manage.py`, `tests/test_compose.py`, `tests/test_secret_command.py`, `tests/test_approve_command.py`, `tests/test_drafts.py`, `tests/test_web_connectors.py`, `tests/test_web_plugins.py`

**Interfaces:**
- Reuses: all method signatures and the row shapes (`tool_names` stays a JSON *string* column; the `tool_names(row)` helper is unchanged); `Extension`, `OauthToken` from Task 1; `Database.engine`.
- Produces: nothing new.

- [ ] **Step 1: see the failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ext_store.py tests/test_tokens.py -q -p no:cacheprovider`
Expected: FAIL on `.conn`.

- [ ] **Step 2: port `store.py`**

`add` is a PostgreSQL upsert (`pg_insert(Extension).values(...).on_conflict_do_update(index_elements=[Extension.name], set_={...})`,
setting `state="enabled"`, `last_error=None` and the excluded values for the rest).
`set_tool_count`, `set_tools`, `set_state`, `set_error` are `update(Extension).where(Extension.name == name).values(...)`;
`get` / `list` / `enabled` are selects ordered by name; `remove` is a delete. `ExtensionStore.__init__`
keeps `self.tokens = TokenStore(db)` and stores `self._engine = db.engine`.

- [ ] **Step 3: port `tokens.py`**

`get` → select by name; `put` keeps the field whitelist and the merge-with-current logic,
then `pg_insert(OauthToken).values(name=name, **merged, updated_at=now).on_conflict_do_update(index_elements=[OauthToken.name], set_={**merged, "updated_at": now})`;
`delete` → delete by name.

- [ ] **Step 4: tests**

Delete `test_an_old_database_gains_the_column` from `tests/test_ext_store.py` (SQLite
ALTER migration; the column is in the Alembic schema and `test_migrations.py` proves it).

Run: `.venv/Scripts/python.exe -m pytest tests/test_ext_store.py tests/test_tokens.py tests/test_oauth.py tests/test_ext_manage.py tests/test_manage.py tests/test_compose.py tests/test_secret_command.py tests/test_approve_command.py tests/test_drafts.py tests/test_web_connectors.py tests/test_web_plugins.py -q -p no:cacheprovider`
Expected: all pass.
Run: `.venv/Scripts/python.exe -m ruff check src/claude_hermes/extensions`

---

### Task 5: curator idle check without raw SQL

**Files:**
- Modify: `src/claude_hermes/learning/curator.py` (`_idle_hours`)
- Test (unchanged, must pass): `tests/test_curator_pass.py`, `tests/test_curator.py`, `tests/test_learning_loop.py`, `tests/test_nudges.py`, `tests/test_review.py`, `tests/test_backup.py`, `tests/test_archive.py`

**Interfaces:**
- Consumes: `Database.last_message_ts()` (Task 2).

- [ ] **Step 1: replace the query**

```python
async def _idle_hours(db: Database, now: datetime) -> float:
    ts = await db.last_message_ts()
    if not ts:
        return float("inf")  # no messages ever recorded: nothing to be idle from
    return (now - _parse_aware(ts)).total_seconds() / 3600
```

- [ ] **Step 2: verify**

Run: `.venv/Scripts/python.exe -m pytest tests/test_curator_pass.py tests/test_curator.py tests/test_learning_loop.py tests/test_nudges.py tests/test_review.py tests/test_backup.py tests/test_archive.py -q -p no:cacheprovider`
Expected: all pass.

---

### Task 6: runtime boot, compose, env, README

**Files:**
- Modify: `src/claude_hermes/main.py` (`build_app` only)
- Modify: `docker-compose.yml`, `docker-compose.prod.yml`, `.env.example`, `README.md` (sections "Run (Docker)" and "Develop"), `docs/deploy.md` (new section "Migrating state.db to PostgreSQL")
- Test: `tests/test_main.py` (must pass)

**Interfaces:**
- Consumes: `core.db.migrate.upgrade_head`, `Config.database_url`, `Database.open(url)`.

- [ ] **Step 1: `main.build_app`**

```python
async def build_app():
    home = get_home()
    cfg = load_config(home)
    if not cfg.database_url:
        raise SystemExit("CH_DATABASE_URL is not set")
    await upgrade_head(cfg.database_url)      # schema first, then the pool
    db = await Database.open(cfg.database_url)
```

with `from .core.db.migrate import upgrade_head` at the top. Nothing else in `main.py`
changes in this task.

- [ ] **Step 2: compose**

`docker-compose.yml` — add before `agent`:

```yaml
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: hermes
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
      POSTGRES_DB: hermes
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U hermes -d hermes"]
      interval: 10s
      timeout: 5s
      retries: 10
```

and on `agent`: `environment: - CH_DATABASE_URL=postgresql+asyncpg://hermes:${POSTGRES_PASSWORD}@db:5432/hermes`
and `depends_on: db: { condition: service_healthy }`; add a top-level `volumes: pgdata:`.
`docker-compose.prod.yml` — the `agent` healthcheck becomes a live connection check:

```yaml
    healthcheck:
      test: ["CMD", "python", "-c", "import asyncio,os,asyncpg; asyncio.run(asyncpg.connect(os.environ['CH_DATABASE_URL'].replace('+asyncpg',''))).close()"]
```

(the same 60s/10s/90s/3 timings), and a `db` block with the same `logging` limits as `agent`.

- [ ] **Step 3: `.env.example`**

```
# PostgreSQL (docker-compose starts it; the URL is derived from this password)
POSTGRES_PASSWORD=replace-me
# Only for running the process outside compose, e.g. on a dev host:
#CH_DATABASE_URL=postgresql+asyncpg://hermes:hermes@127.0.0.1:55432/hermes
```

- [ ] **Step 4: docs**

README "Run (Docker)": step 1 also sets `POSTGRES_PASSWORD`; the "State lives in
`./data`" paragraph says sessions/jobs/registry live in PostgreSQL (`pgdata` volume, back
up with `docker compose exec db pg_dump -U hermes hermes`), files and the wiki still in
`./data`. README "Develop": add `make test-db` before `pytest`. `docs/deploy.md`: a
section "Migrating state.db to PostgreSQL" — stop the stack, `docker compose up -d db`,
`docker compose run --rm agent python scripts/import_sqlite.py --sqlite /data/state.db`,
then `docker compose up -d`; `state.db` stays on disk as a backup, unused.

- [ ] **Step 5: verify**

Run: `.venv/Scripts/python.exe -m pytest tests/test_main.py -q -p no:cacheprovider`
Expected: pass. Run: `docker compose config -q` Expected: no error.

---

### Task 7: one-shot `state.db → PostgreSQL` import

**Files:**
- Create: `scripts/import_sqlite.py`
- Test: `tests/test_import_sqlite.py`

**Interfaces:**
- Reuses: the old `_SCHEMA` (as the fixture the test builds with `sqlite3`), Task 1 models, `Database` facade to read back.
- Produces: `python scripts/import_sqlite.py --sqlite <path> [--url <CH_DATABASE_URL>]`, and an importable `async def import_sqlite(sqlite_path: Path, url: str) -> dict[str, int]` (rows imported per table).

- [ ] **Step 1: failing test**

```python
import sqlite3

from claude_hermes.db import Database
from scripts.import_sqlite import import_sqlite

OLD = """
CREATE TABLE sessions (session_key TEXT PRIMARY KEY, sdk_session_id TEXT, system_append TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE VIRTUAL TABLE messages_fts USING fts5(session_key, role, content, ts UNINDEXED);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT NOT NULL,
  value TEXT NOT NULL, prompt TEXT NOT NULL, deliver_chat_id INTEGER NOT NULL, deliver_thread_id INTEGER,
  origin_session_key TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active', next_run_at TEXT,
  last_run_at TEXT, last_status TEXT, timeout_seconds INTEGER, created_at TEXT NOT NULL);
CREATE TABLE job_runs (job_id INTEGER NOT NULL, ts TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE turn_costs (ts TEXT NOT NULL, session_key TEXT NOT NULL, cost_usd REAL NOT NULL);
CREATE TABLE extensions (name TEXT PRIMARY KEY, kind TEXT NOT NULL, catalog_version TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'enabled', installed_at TEXT NOT NULL, installed_by INTEGER,
  last_error TEXT, tool_count INTEGER, tool_names TEXT);
CREATE TABLE oauth_tokens (name TEXT PRIMARY KEY, issuer TEXT NOT NULL DEFAULT '', client_id TEXT NOT NULL DEFAULT '',
  client_secret TEXT NOT NULL DEFAULT '', access_token TEXT NOT NULL DEFAULT '', refresh_token TEXT NOT NULL DEFAULT '',
  expires_at TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
CREATE TABLE turn_events (id INTEGER PRIMARY KEY AUTOINCREMENT, session_key TEXT NOT NULL, turn_id TEXT NOT NULL,
  seq INTEGER NOT NULL, ts TEXT NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE threads (session_key TEXT PRIMARY KEY, title TEXT, title_manual INTEGER NOT NULL DEFAULT 0,
  channel TEXT NOT NULL, owner_user_id INTEGER, archived_at TEXT, last_event_at TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE files (id TEXT PRIMARY KEY, session_key TEXT NOT NULL, path TEXT NOT NULL, name TEXT NOT NULL,
  media_type TEXT NOT NULL, size INTEGER NOT NULL, created_at TEXT NOT NULL);
"""
NOW = "2026-07-12T10:00:00+00:00"


def _old_db(path):
    c = sqlite3.connect(path)
    c.executescript(OLD)
    c.execute("INSERT INTO sessions VALUES ('tg:private:1','sdk-1','A',?,?)", (NOW, NOW))
    c.execute("INSERT INTO messages_fts VALUES ('tg:private:1','user','kubernetes ingress',?)", (NOW,))
    c.execute("INSERT INTO meta VALUES ('owner_user_id','42')")
    c.execute("INSERT INTO jobs (name,kind,value,prompt,deliver_chat_id,origin_session_key,next_run_at,created_at)"
              " VALUES ('daily','cron','0 9 * * *','hi',1,'tg:private:1',?,?)", (NOW, NOW))
    c.execute("INSERT INTO job_runs VALUES (1,?, 'ok')", (NOW,))
    c.execute("INSERT INTO turn_events (session_key,turn_id,seq,ts,type,payload)"
              " VALUES ('tg:private:1','t1',1,?,'turn.start','{\"channel\": \"telegram\"}')", (NOW,))
    c.execute("INSERT INTO threads (session_key,channel,owner_user_id,last_event_at,created_at)"
              " VALUES ('tg:private:1','telegram',42,?,?)", (NOW, NOW))
    c.commit()
    c.close()


async def test_roundtrip(tmp_path, test_database):
    _old_db(tmp_path / "state.db")
    counts = await import_sqlite(tmp_path / "state.db", test_database)
    assert counts["sessions"] == 1 and counts["messages"] == 1 and counts["turn_events"] == 1
    db = await Database.open()
    assert (await db.get_session("tg:private:1"))["sdk_session_id"] == "sdk-1"
    assert await db.get_meta("owner_user_id") == "42"
    assert (await db.search_messages("ingress"))[0]["session_key"] == "tg:private:1"
    ev = await db.events_after("tg:private:1")
    assert ev[0]["payload"] == {"channel": "telegram"}
    # sequences continue after the imported ids: a new event must not collide
    new_id = await db.append_event("tg:private:1", "t2", 1, NOW, "turn.start", {})
    assert new_id > ev[-1]["id"]
    # running it again is a no-op, not a duplicate
    again = await import_sqlite(tmp_path / "state.db", test_database)
    assert again["sessions"] == 0
    await db.close()
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_import_sqlite.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: scripts.import_sqlite` (add an empty
`scripts/__init__.py` if pytest cannot import the package).

- [ ] **Step 2: implement**

`scripts/import_sqlite.py`: read each table with `sqlite3` (`row_factory = sqlite3.Row`),
map `messages_fts → messages`, `turn_events.payload` via `json.loads`, and insert with
`pg_insert(Table).values(rows).on_conflict_do_nothing()` in batches of 500 for tables
with a natural key (`sessions`, `meta`, `extensions`, `oauth_tokens`, `threads`, `files`,
`jobs`, `turn_events` — the last two keep their ids). `messages`, `job_runs`,
`turn_costs` have no natural key: skip the whole table if the target already has rows
(`SELECT count(*)`), otherwise insert. Return `{table: rows_inserted}` using
`result.rowcount`. After importing, reset sequences for `jobs` and `turn_events`:
`SELECT setval(pg_get_serial_sequence('jobs','id'), COALESCE(max(id),1)) FROM jobs` (same
for `turn_events`). `main()` parses `--sqlite` (required) and `--url` (default
`CH_DATABASE_URL`), runs `upgrade_head_sync(url)` first, prints the counts as one line
per table, exits 1 with a clear message if the sqlite file is missing.

- [ ] **Step 3: verify**

Run: `.venv/Scripts/python.exe -m pytest tests/test_import_sqlite.py -q -p no:cacheprovider`
Expected: 1 passed. `ruff check scripts tests/test_import_sqlite.py`.

---

### Task 8: FastAPI application shell + one error handler

**Files:**
- Modify: `src/claude_hermes/web/app.py`, `src/claude_hermes/web/errors.py`
- Modify: `tests/test_web_app.py` (add one test)
- Test (unchanged, must pass): every `tests/test_web_*.py`

**Interfaces:**
- Reuses: the whole `routes` list, `ApiMiddleware`, `SpaStaticFiles`, `auth_routes`; `core.exceptions.ApplicationException`.
- Produces: `build_web_app(deps, extra_routes=None) -> fastapi.FastAPI`; `web.errors.ApiError(ApplicationException)`.

- [ ] **Step 1: failing test** (append to `tests/test_web_app.py`)

```python
from starlette.routing import Route

from claude_hermes.core.exceptions import NotFound


async def test_application_exceptions_render_as_the_api_error_shape(tmp_path):
    async def boom(request):
        raise NotFound("no such thread")
    from tests.web_helpers import make_client, login
    client, deps = await make_client(tmp_path)
    # rebuild with the extra route
    from claude_hermes.web.app import build_web_app
    import httpx
    app = build_web_app(deps, extra_routes=[Route("/api/boom", boom)])
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://cladex.test")
    login(client, 1)
    r = await client.get("/api/boom")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "not_found", "message": "no such thread"}}
```

- [ ] **Step 2: `errors.py`**

```python
from starlette.responses import JSONResponse

from ..core.exceptions import ApplicationException


class ApiError(ApplicationException):
    """A router's way to answer with a status + code. Same constructor as always."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(status, code, message)


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)
```

- [ ] **Step 3: `app.py`**

Replace `from starlette.applications import Starlette` with `from fastapi import FastAPI`;
in `build_web_app`:

```python
    app = FastAPI(routes=routes, openapi_url=None, docs_url=None, redoc_url=None)
    app.add_middleware(ApiMiddleware, deps=deps)

    async def _application_error(request: Request, exc: ApplicationException):
        return error_response(exc.status, exc.code, exc.message)

    app.add_exception_handler(ApplicationException, _application_error)
    return app
```

and remove the `try/except ApiError` around `call_next` in `ApiMiddleware.dispatch` (the
handler renders it now; keep everything else in the middleware). Import
`ApplicationException` from `..core.exceptions`.

- [ ] **Step 4: verify**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_app.py tests/test_web_auth.py tests/test_web_threads.py tests/test_web_turns.py tests/test_web_files.py tests/test_web_routines.py tests/test_web_connectors.py tests/test_web_plugins.py tests/test_web_drafts.py tests/test_web_skills.py tests/test_web_wiki.py tests/test_web_voice.py tests/test_web_commands.py -q -p no:cacheprovider`
Expected: all pass.

---

### Task 9: import-linter contracts, `make gate`, CI

**Files:**
- Modify: `pyproject.toml` (`[tool.importlinter]`), `Makefile` (`gate`, `lint`), `.github/workflows/deploy.yml` (verify job)

**Interfaces:**
- Produces: `lint-imports` passes; `make gate` = pytest + ruff + lint-imports.

- [ ] **Step 1: contracts**

```toml
[tool.importlinter]
root_package = "claude_hermes"
include_external_packages = true

[[tool.importlinter.contracts]]
name = "core does not depend on the application"
type = "forbidden"
source_modules = ["claude_hermes.core"]
forbidden_modules = [
    "claude_hermes.modules", "claude_hermes.web", "claude_hermes.gateway",
    "claude_hermes.cron", "claude_hermes.extensions", "claude_hermes.learning",
    "claude_hermes.runner", "claude_hermes.tools", "claude_hermes.main", "claude_hermes.db",
]
ignore_imports = ["claude_hermes.core.db.registry -> claude_hermes.modules.*"]

[[tool.importlinter.contracts]]
name = "modules are independent of each other"
type = "independence"
modules = ["claude_hermes.modules.*"]

[[tool.importlinter.contracts]]
name = "domain layers are pure"
type = "forbidden"
source_modules = ["claude_hermes.modules.*.domain"]
forbidden_modules = [
    "fastapi", "starlette", "httpx", "aiogram", "claude_agent_sdk", "structlog",
    "claude_hermes.web", "claude_hermes.gateway", "claude_hermes.runner",
    "claude_hermes.tools", "claude_hermes.db",
]
```

- [ ] **Step 2: Makefile**

```make
PY := .venv/Scripts/python.exe

.PHONY: test lint gate
test:            ## the suite (needs `make test-db` once)
	$(PY) -m pytest -q -p no:cacheprovider
lint:            ## ruff + import-linter
	$(PY) -m ruff check src tests
	.venv/Scripts/lint-imports
gate: lint test  ## what CI runs
```

- [ ] **Step 3: CI**

In `.github/workflows/deploy.yml`, job `verify`: add

```yaml
    services:
      postgres:
        image: postgres:16-alpine
        env: { POSTGRES_USER: hermes, POSTGRES_PASSWORD: hermes, POSTGRES_DB: hermes }
        ports: ["55432:5432"]
        options: >-
          --health-cmd "pg_isready -U hermes" --health-interval 5s --health-timeout 5s --health-retries 10
```

and `- run: lint-imports` after the ruff step. (`CH_TEST_DATABASE_URL` default already
points at 127.0.0.1:55432.)

- [ ] **Step 4: verify**

Run: `.venv/Scripts/lint-imports` Expected: `Contracts: 3 kept, 0 broken.`

---

### Task 10: structlog (serial — touches every module)

**Files:**
- Create: `src/claude_hermes/core/logging.py`, `tests/test_core_logging.py`
- Modify: every file under `src/claude_hermes/` that contains `logging.getLogger` or a module-level `logging.<level>(` call (29 files; `grep -rl "logging" src/claude_hermes --include=*.py`), and `main.py` (`logsafe.install(logging.INFO)` → `configure(logging.INFO)`)
- Leave alone: `src/claude_hermes/logsafe.py`, `tests/test_logsafe.py`

**Interfaces:**
- Reuses: `logsafe.RedactingFormatter`.
- Produces: `core.logging.configure(level: int = logging.INFO) -> None`.

- [ ] **Step 1: failing test**

```python
import io
import logging

import structlog

from claude_hermes.core import logging as core_logging

TOKEN = "8890275799:AAGtZFDmyxDhUj7k1PVg3my7hmT4Nl5uwn0"


def test_configure_renders_key_values_and_redacts(monkeypatch):
    root = logging.getLogger()
    saved = list(root.handlers)
    try:
        core_logging.configure(logging.INFO)
        stream = io.StringIO()
        root.handlers[0].stream = stream
        structlog.get_logger("t").info("calling %s", f"https://api.telegram.org/bot{TOKEN}/x", lane="tg:1")
        logging.getLogger("third_party").warning("plain %s", "record")
        out = stream.getvalue()
        assert "lane=tg:1" in out and "<redacted>" in out and TOKEN not in out
        assert "plain record" in out
    finally:
        root.handlers = saved
```

- [ ] **Step 2: implement `core/logging.py`**

```python
"""One logging setup for the process: structlog for our code, stdlib for
everything else, both rendered by the same formatter — and the same token
redaction (logsafe) wrapped around it."""
from __future__ import annotations

import logging

import structlog

from claude_hermes import logsafe

_SHARED = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.stdlib.PositionalArgumentsFormatter(),
]


def configure(level: int = logging.INFO) -> None:
    structlog.configure(
        processors=[*_SHARED, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.KeyValueRenderer(
                        key_order=["timestamp", "level", "logger", "event"])],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(logsafe.RedactingFormatter(formatter))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
```

- [ ] **Step 3: the sweep**

In each of the 29 files: `import logging` stays only if something other than
`getLogger` uses it (e.g. `logging.INFO`); `log = logging.getLogger(__name__)` →
`log = structlog.get_logger(__name__)` with `import structlog`; module-level
`logging.info(...)` / `logging.exception(...)` calls (`main.py`) → `log.info` / `log.exception`
on that module's logger. Keep `%s` positional arguments as they are
(`PositionalArgumentsFormatter` renders them). In `main.run`: `from .core.logging import configure`
and `configure(logging.INFO)` instead of `logsafe.install(...)`.

```bash
grep -rn "logging.getLogger\|^logging\.\(info\|warning\|error\|exception\|debug\)(" src/claude_hermes --include=*.py | grep -v "core/logging.py\|logsafe.py"
```

must print nothing when done.

- [ ] **Step 4: verify**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` Expected: all pass.
Run: `.venv/Scripts/python.exe -m ruff check src tests` Expected: clean.

---

### Task 11: wiki + docs

**Files:**
- Modify: `docs/llm-wiki/contracts/state-db.md` (rewrite as the PostgreSQL schema contract; keep the id `contract.state-db`, retitle "Contract: PostgreSQL schema"), `docs/llm-wiki/index.md` (link text), `docs/llm-wiki/components/core-runtime.md` (boot sequence, `core/`, structlog), `docs/llm-wiki/components/web.md` (FastAPI shell), `docs/llm-wiki/quality/testing.md` (PostgreSQL requirement, `make test-db`, per-process DB), `docs/llm-wiki/operations.md` (pg_dump, import script), `docs/llm-wiki/system.md` (state split: PostgreSQL + `./data`)
- Create: `docs/llm-wiki/decisions/postgres-and-modular-core.md` (why PostgreSQL, why models in `modules/*/domain`, why structlog via stdlib, why FastAPI shell first; links the spec)

**Interfaces:**
- Reuses: `docs/llm-wiki/schema.md` page rules; `.llm-wiki/llm-wiki fingerprint --page <page>`; `.llm-wiki/llm-wiki validate`.

- [ ] **Step 1: write the pages** — evidence lists point at the new files (`core/db/*`, `modules/*/domain/models.py`, `db.py`, `conftest.py`, `scripts/import_sqlite.py`, `web/app.py`, `core/logging.py`).
- [ ] **Step 2: fingerprint each touched page**, then `.llm-wiki/llm-wiki validate` — Expected: OK.

---

## Open Questions

1. **Timestamps as `TEXT` or `TIMESTAMPTZ`?** Options: keep ISO text (1:1 with today, no
   caller changes) / convert now. **Default: keep text** — every comparison in the code is
   lexicographic on ISO-8601 UTC strings; a type change belongs to the module that owns
   each table in Wave 2.
2. **Run Alembic at boot or as a separate compose step?** Options: boot (single process
   today) / `docker compose run agent alembic upgrade`. **Default: boot** — one instance
   in Wave 1; Wave 3 (N API instances) moves it to a one-shot `migrate` service.
