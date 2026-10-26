from datetime import datetime, timezone

import pytest

from claude_hermes.modules.scheduler.domain.schedule import (
    Schedule,
    next_fire,
    parse_schedule,
    period_seconds,
)


def dt(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


NOW = dt("2026-07-12T10:00:00")


def test_parse_interval_shorthands():
    assert parse_schedule("30m", NOW) == Schedule("interval", "1800")
    assert parse_schedule("every 2h", NOW) == Schedule("interval", "7200")
    assert parse_schedule("1d", NOW) == Schedule("interval", "86400")


def test_parse_cron_and_once():
    assert parse_schedule("0 8 * * *", NOW) == Schedule("cron", "0 8 * * *")
    once = parse_schedule("2026-08-01T09:00:00+00:00", NOW)
    assert once.kind == "once"


def test_parse_once_normalizes_to_utc():
    once = parse_schedule("2026-08-01T09:00:00+03:00", NOW)
    assert once == Schedule("once", "2026-08-01T06:00:00+00:00")


def test_parse_garbage_raises():
    with pytest.raises(ValueError):
        parse_schedule("когда-нибудь потом", NOW)
    with pytest.raises(ValueError):
        parse_schedule("99 99 * * *", NOW)  # invalid cron field


def test_next_fire_interval_and_cron():
    assert next_fire(Schedule("interval", "1800"), NOW) == dt("2026-07-12T10:30:00")
    assert next_fire(Schedule("cron", "0 8 * * *"), NOW) == dt("2026-07-13T08:00:00")


def test_next_fire_once():
    s = Schedule("once", "2026-08-01T09:00:00+00:00")
    assert next_fire(s, NOW) == dt("2026-08-01T09:00:00")
    assert next_fire(s, dt("2026-08-02T00:00:00")) is None  # spent


def test_period_seconds():
    assert period_seconds(Schedule("interval", "1800")) == 1800.0
    assert period_seconds(Schedule("once", "2026-08-01T09:00:00+00:00")) is None
    assert period_seconds(Schedule("cron", "0 8 * * *")) == 86400.0
