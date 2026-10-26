"""Alembic, driven from Python instead of alembic.ini — the URL is a secret that
lives in the environment, so there is no file to put it in."""
from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # escape_percent: a password may contain '%', which ConfigParser would read
    # as interpolation and mangle.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade_head_sync(url: str) -> None:
    command.upgrade(alembic_config(url), "head")


async def upgrade_head(url: str) -> None:
    # env.py calls asyncio.run(); that cannot happen on the running loop, so the
    # whole Alembic run goes to a worker thread with a loop of its own.
    await asyncio.to_thread(upgrade_head_sync, url)
