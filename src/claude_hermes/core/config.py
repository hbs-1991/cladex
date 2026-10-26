import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

# The advisor is a server tool, configured file-based in agent_home/.claude/settings.json
# (see paths.seed_settings). Every fork that runs with setting_sources=["project"] and
# cwd=agent_home inherits it — including the background ones. Those have no user at a
# decision point, so an advisor call there just resends the whole transcript at the
# advisor's rates. Merged over os.environ by the SDK, so it drops nothing.
NO_ADVISOR_ENV = {"CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"}


@dataclass
class Config:
    model: str = "claude-sonnet-5"
    # Which models a user may pick for a lane — the composer's picker, /model in
    # Telegram, a routine's form. Only the OFFER: what each model is (window,
    # prices, efforts, the spelling the CLI needs) lives in core/model_catalog.py.
    # `model` is always offered, first, even when missing here. A model joins this
    # default only after scripts/model_matrix.py has proved it on the pinned CLI
    # under production auth (docs/deploy.md, "Offering a model").
    models: list[str] = field(default_factory=lambda: [
        "claude-sonnet-5", "claude-opus-5-5", "claude-fable-5-1"])
    max_turns: int = 50
    idle_reset_hours: float | None = None   # None → sessions never idle-expire
    daily_reset_hour: int | None = None     # None → no daily boundary reset
    group_require_mention: bool = True
    wiki_index_max_chars: int = 8000   # above this the injected index is truncated, loudly
    telegram_token: str = ""

    # Voice — speech-to-text on the way in, text-to-speech on the way out. Both use
    # the OpenAI audio API. The key is a secret, so it is read from the environment
    # like telegram_token (never from config.yaml) and dropped from os.environ at
    # startup so the agent's Bash cannot read it. Empty key → voice simply off:
    # a voice note gets a one-line "not configured" reply, nothing crashes.
    openai_api_key: str = ""
    stt_model: str = "gpt-4o-mini-transcribe"   # whisper-1 also works, cheaper/older
    tts_model: str = "gpt-4o-mini-tts"          # tts-1 / tts-1-hd also valid
    tts_voice: str = "alloy"                    # alloy, echo, fable, onyx, nova, shimmer…
    voice_reply_enabled: bool = True            # false → transcribe in, but reply text-only
    tts_max_chars: int = 4000                   # OpenAI TTS caps input; voice the head only

    # Phase 2 — learning loop
    wiki_nudge_interval: int = 4      # turns without a wiki write before the ingest pass fires
    skill_nudge_interval: int = 15
    # empty → the main model. Not for cache reasons: the review fork runs its own
    # narrow toolset, and tools are the first segment of the cached prefix, so it
    # never reads the lane's cache whatever model it runs — a cheaper one is free
    # to pick. Its window must hold the longest lane it resumes.
    review_model: str = ""
    review_max_turns: int = 16
    curator_enabled: bool = True
    stale_after_days: int = 30
    archive_after_days: int = 90
    allowed_users: list[int] = field(default_factory=list)
    # Wave 4a (D6): the DEFAULT for whether a non-owner's turn gets `Bash`,
    # `Agent` and `Task` — what a user inherits until the owner sets their own
    # grant in Settings → Users (`users.shell_enabled`, 0009; identity's
    # `AccessControl.grants` is the one resolver). False, and the default is the
    # decision rather than a precaution: every turn in the container runs as the
    # same uid, so a shell reaches every other user's tree no matter what a
    # PreToolUse hook says, and a regex over the command would deny `cd ..` while
    # still missing `ls $X/users`. The honest boundary is a container per user.
    users_shell_enabled: bool = False
    # Wave 4b: the DEFAULT for whether a non-owner's turns are observed by the
    # learning loop (their sidecar, their nudge counters, review forks over their
    # sessions) and whether the curator sweeps their tree too — inherited until
    # the owner sets `users.learning_enabled` for that person. False because every
    # one of those forks runs on the OWNER's account for somebody else's traffic,
    # and nothing yet meters that spend per user (`skill_admin` and the skills API
    # are per user regardless — reading your own library is not learning). One
    # review fork at a time deploy-wide either way. Each process reads its own
    # copy of both defaults at boot, so editing either means restarting the agent
    # and the worker together.
    users_learning_enabled: bool = False
    memory_search_enabled: bool = True   # the wiki index tool on private/web lanes
    title_model: str = "claude-haiku-4-5-20251001"   # cheap one-shot for thread titles
    rich_enabled: bool = True    # kill switch: false -> plain sendMessage everywhere

    # Phase 4 — curator: scheduling, backups, consolidation
    curator_interval_hours: float = 168.0   # 7d between passes
    curator_min_idle_hours: float = 2.0     # never curate mid-conversation
    curator_tick_seconds: int = 900         # how often the loop re-checks the gates
    backup_keep: int = 5                    # snapshots retained before pruning
    curator_consolidate: bool = False       # LLM umbrella-building pass — opt-in
    curator_dry_run: bool = False           # report only; archive nothing
    curator_model: str = ""                 # empty → review_model, then main model
    curator_max_turns: int = 30
    wiki_lint_enabled: bool = True     # the wiki half of the curator pass
    wiki_lint_dry_run: bool = False    # report only; the guard denies every write

    # Phase 3 — cron
    cron_tick_seconds: int = 30
    cron_timeout_seconds: float = 180.0
    cron_model: str = ""              # empty → main model

    # The owner's clock. An IANA zone name: the content plan reads a naive time
    # ("2026-10-01 10:00") in it and writes every time it answers the agent with
    # in it. Checked at load, because a misspelt zone would otherwise surface as
    # the first plan item the agent fails to create. Cron expressions stay UTC —
    # this key does not move a single routine.
    timezone: str = "UTC"
    # The content plan (`modules/content`): the `/api/content*` routes, the
    # `content_plan` tool on the deploy owner's lanes and routines, and the tick
    # that queues approved items and wakes the publisher routine. Off by default.
    # Flipping it changes the hermes toolset, so every conversation starts a fresh
    # session at the next boot — like `memory_search_enabled`.
    content_enabled: bool = False
    # The channel names a plan item may carry. The calendar colours and icons the
    # six below; another name is accepted and drawn plain.
    content_channels: list[str] = field(default_factory=lambda: [
        "linkedin", "telegram", "x", "threads", "instagram", "youtube"])
    # How long before its publish time a theme-approved (`planned`) item is due
    # for drafting: the drafter routine is woken once `publish_at - lead` has
    # passed. 24 leaves a day to read the card and redraft; a render takes minutes.
    content_draft_lead_hours: float = 24.0

    # Phase 4 — extensions
    ext_probe_timeout_seconds: float = 60.0     # how long a server gets to hand-shake
    plugin_clone_timeout_seconds: float = 120.0  # how long `git clone` gets

    # Phase 5 — asking the user
    ask_user_timeout_seconds: float = 120.0   # how long a question waits for a tap
    ask_user_max_per_turn: int = 2            # bounds how long one turn can hold the lane

    # Phase 6 — web channel (CladeX). Off by default: with web_enabled false the
    # HTTP server is never constructed and the process behaves exactly as before.
    web_enabled: bool = False
    web_port: int = 8080
    web_public_origin: str = ""            # e.g. https://cladex.example.com — Origin check + widget domain
    web_upload_max_mb: int = 20
    # The cap on both halves of a tool's turn event: `tool.result`'s output, and
    # each string inside `tool.call`'s input (a Write's file body, an Edit's hunk).
    # Both are stored in `turn_events` for the life of the lane and re-sent on every
    # open of the thread, so an uncapped one is paid for again on every visit.
    web_tool_output_max_chars: int = 8000
    web_session_days: int = 30
    web_menu_button: bool = True           # register the Mini App as the bot's menu button on boot
    web_secret: str = ""                   # CH_WEB_SECRET — env only, signs the session cookie

    # The connection string carries the database password, so it is a secret like
    # the others: environment only, never config.yaml.
    database_url: str = ""                 # CH_DATABASE_URL

    # Wave 2 — the Telegram transport seam. "polling" is what the bot has always
    # done and stays the default; "webhook" mounts POST /telegram/webhook/<secret>
    # on the web app instead. The secret is the only credential on that route, so
    # it is environment-only like the others.
    telegram_mode: str = "polling"         # polling | webhook
    telegram_webhook_secret: str = ""      # CH_TELEGRAM_WEBHOOK_SECRET

    # Wave 3a — the queue. A Redis URL carries a password, so it is environment
    # only like the other connection strings. This is the ONLY switch: set it and
    # scheduled jobs, learning reviews and curator passes are enqueued for a
    # worker process; leave it empty and they run in-process exactly as they did
    # before, which is what keeps a laptop deployment to `db + agent`.
    redis_url: str = ""                    # CH_REDIS_URL

    # Wave 3a — blobs. `local` is the filesystem store under agent_home that the
    # app has always had; `s3` puts uploads and agent-produced files in a bucket
    # and keeps the on-disk inbox/outbox layout as a cache under the same keys,
    # so the agent's own Read/Glob and the prompts naming `inbox/…` keep working.
    blob_backend: str = "local"            # local | s3
    s3_endpoint: str = ""                  # e.g. http://minio:9000
    s3_bucket: str = "hermes"
    s3_region: str = "us-east-1"
    # The access key is half a credential and the secret key is the other half;
    # both stay out of config.yaml and are popped from os.environ at startup so
    # the agent's Bash cannot read them.
    s3_access_key: str = ""                # CH_S3_ACCESS_KEY
    s3_secret_key: str = ""                # CH_S3_SECRET_KEY

    # Wave 3a — extension secrets are Fernet-encrypted rows in PostgreSQL. The
    # key that decrypts them must never sit next to the ciphertext, so it is
    # environment only: a stolen database dump is not a stolen credential.
    secrets_key: str = ""                  # CH_SECRETS_KEY

    # Wave 3b — a turn is a row and a task. Four dials on the `turns` table.
    #
    # `turn_lease_seconds` is the dead-man's switch on a RUNNING turn: the worker
    # heartbeats every lease/3, and a lease that lapses is what lets the reaper
    # mark the turn `lost` and free the lane. Shorter means a killed worker's lane
    # comes back sooner; too short and a stalled-but-alive worker is reaped out
    # from under a turn that was going to finish.
    turn_lease_seconds: float = 60.0
    # How long a QUEUED turn may sit before the reaper frees its lane. This is the
    # queue message that vanished — a lane must not be held forever by a task no
    # worker ever claimed. It is also how long a second Telegram message waits
    # behind a busy lane before it gives up.
    turn_queue_timeout_seconds: float = 300.0
    # How long the in-flight snapshot (the events of a turn plus its unflushed
    # deltas) lives in Redis. A worker killed between two heartbeats leaves keys
    # behind; they age out on their own rather than accumulating.
    turn_snapshot_ttl_seconds: int = 3600
    # How long a finished `turns` row is kept before pruning. The row holds a
    # third copy of the user's text and the reply (messages and turn_events hold
    # the other two), so this is a disk bound, not a history feature.
    turn_retention_days: int = 30

    # Wave 3a — how long the `learning_review_until` gate stays claimed. It is a
    # lease, not a flag: a worker that dies mid-review must not block the curator
    # and TurnService forever, so the gate expires on its own.
    review_timeout_seconds: float = 900.0

    @property
    def queue_mode(self) -> str:
        """`redis` when a Redis URL is configured, `inline` otherwise.

        Derived, never a second switch: a `queue_mode: redis` in config.yaml with
        no reachable Redis would be a deployment that silently drops every job.
        """
        return "redis" if self.redis_url else "inline"


# Secrets come from the environment, never from the (world-readable on the volume)
# config file — so the file may not set them. Every name here is also popped from
# os.environ at startup, before anything can spawn the CLI.
_ENV_ONLY = {
    "telegram_token": "TELEGRAM_BOT_TOKEN",
    "openai_api_key": "OPENAI_API_KEY",
    "web_secret": "CH_WEB_SECRET",
    "database_url": "CH_DATABASE_URL",
    "telegram_webhook_secret": "CH_TELEGRAM_WEBHOOK_SECRET",
    "redis_url": "CH_REDIS_URL",
    "s3_access_key": "CH_S3_ACCESS_KEY",
    "s3_secret_key": "CH_S3_SECRET_KEY",
    "secrets_key": "CH_SECRETS_KEY",
}

# Read-only properties derived from the fields above. `hasattr` says yes to these
# and `setattr` raises, so a config.yaml with a stray `queue_mode:` would crash the
# boot instead of being ignored.
_DERIVED = frozenset({"queue_mode"})

# Addresses, not secrets: config.yaml may set them, and the environment wins when
# it does. docker-compose sets CH_S3_ENDPOINT from the same place it names the
# MinIO service, so the URL in the container can never drift from the container
# it points at — the config file on the data volume does not have to be edited to
# match a topology change.
_ENV_OVERRIDE = {"s3_endpoint": "CH_S3_ENDPOINT"}


def _checked_zone(name) -> str:
    """`timezone` as a zone `zoneinfo` can open, or a `ValueError` that names it.

    Loud on purpose: the process refuses to boot on a typo rather than reading
    every naive time the owner gives the agent in a zone they did not mean. The
    zone database is the `tzdata` package where the OS has none (Windows, a slim
    image), which is why it is a dependency.
    """
    text = str(name or "").strip() or "UTC"
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            f"config.yaml: timezone {text!r} is not an IANA zone name "
            "(for example 'UTC' or 'Asia/Tashkent')") from exc
    return text


def load_config(home: Path) -> Config:
    cfg = Config()
    path = home / "config.yaml"
    if path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for key, value in data.items():
            if hasattr(cfg, key) and key not in _ENV_ONLY and key not in _DERIVED:
                setattr(cfg, key, value)
    # YAML values are copied as-is, and a bare `models: claude-opus-5-5` would
    # otherwise iterate as a list of characters.
    if isinstance(cfg.models, str):
        cfg.models = [cfg.models]
    cfg.models = [str(m).strip() for m in (cfg.models or []) if str(m).strip()]
    # The same for the channel list, lower-cased: the plan compares names, and a
    # `LinkedIn` in the file must not be a seventh channel.
    if isinstance(cfg.content_channels, str):
        cfg.content_channels = [cfg.content_channels]
    cfg.content_channels = [str(c).strip().lower() for c in (cfg.content_channels or [])
                            if str(c).strip()]
    cfg.timezone = _checked_zone(cfg.timezone)
    for field_name, env_name in _ENV_ONLY.items():
        setattr(cfg, field_name, os.environ.get(env_name, ""))
    for field_name, env_name in _ENV_OVERRIDE.items():
        if os.environ.get(env_name):
            setattr(cfg, field_name, os.environ[env_name])
    return cfg
