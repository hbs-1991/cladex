"""The state directory: where it is, how it is seeded, and one object that names
every path inside it — per user since Wave 4a.

Moved here from `paths.py` in Wave 2. The module-level functions are
unchanged — every caller keeps working — and ``Workspace`` is the port shape the
modules are given instead of a bare ``Path`` they each re-derive.

``WorkspaceResolver`` at the bottom is the seam between this file and identity.
Core may not import a module, so it takes the two lookups it needs as async
callables that the composition root binds to the ACL: this file knows the LAYOUT
of a workspace and the RULE for which one a lane uses, and knows nothing at all
about who a user is.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import structlog

from claude_hermes.core.session_keys import lane_owner

log = structlog.get_logger(__name__)

# Bundled defaults. bundle/ mirrors what lands at the root of agent_home; skills/ and
# agents/ land under agent_home/.claude/.
#
# Where they live depends on how the package was installed — the same problem CH_CATALOG
# solves for the catalog, and for the same reason. On the dev host this is a src layout
# and they sit at the repo root, two levels up. In the container the package is
# pip-installed into site-packages, where parents[2] is /usr/local/lib/python3.12 and
# every one of these paths is nonsense — so the image copies them in and points CH_ASSETS
# at them. Without that env var a fresh container seeds NOTHING: no persona, no skills, no
# settings, and _copy_missing's quiet no-op on a missing source means it never says so.
# One variable rather than three: they are one fact, shipped together.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ASSETS_DIR = Path(os.environ["CH_ASSETS"]) if os.environ.get("CH_ASSETS") else _REPO_ROOT
_BUNDLED_SKILLS_DIR = _ASSETS_DIR / "skills"
_BUNDLED_AGENTS_DIR = _ASSETS_DIR / "agents"
_BUNDLE_DIR = _ASSETS_DIR / "bundle"


def _warn_missing_source(dest: Path, src: Path) -> bool:
    """True when the bundled source is absent — logged loudly, because this is
    how a container ran for weeks with no persona at all: the source was missing,
    the copy quietly did nothing, and the agent came up with none of its operating
    rules and no complaint anywhere."""
    if src.exists():
        return False
    log.warning("cannot seed %s: bundled source %s does not exist "
                "(is CH_ASSETS set and does the image ship it?)", dest.name, src)
    return True


def _copy_missing(dest: Path, src: Path) -> None:
    """Copy src -> dest only when dest is absent. Never overwrites: once seeded,
    the file is the user's (or the agent's) to edit. Without a manifest we cannot
    tell "deleted on purpose" from "never seeded", so absence is the only signal —
    this is the `manifest=None` path; `ensure()` seeds
    through `SeedManifest` instead."""
    if _warn_missing_source(dest, src):
        return
    if dest.exists():
        return
    try:
        shutil.copytree(src, dest) if src.is_dir() else shutil.copy2(src, dest)
    except OSError:
        # Since Wave 4a every user has their own tree and three processes (agent,
        # worker, scheduler) may seed a brand-new one at the same moment, so the
        # loser's FileExistsError is the NORMAL case, not a failure. Say which
        # happened: a destination that exists afterwards was somebody else's copy.
        if dest.exists():
            log.warning("%s was seeded concurrently by another process; kept theirs",
                        dest)
        else:
            log.warning("failed to seed %s from %s", dest, src, exc_info=True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_atomic(dest: Path, data: bytes) -> None:
    """A whole file or nothing: a reader (the CLI loading a skill mid-turn, a
    sibling process seeding the same tree) never sees a half-written one. The
    temp name carries the pid so two processes refreshing the same file do not
    truncate each other's temp."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


# The trees whose user-modified seeds were already reported in this process.
# `get_home()` runs `ensure()` on every call, and one INFO line per tree per
# boot is informative where one per call would be noise.
_MODIFIED_REPORTED: set[Path] = set()

# sha256 (\r\n normalised to \n) of every version of a bundled seed that was
# EVER shipped and later changed — the `_SHIPPED_PERSONA_SHA256` idea, for the
# manifest's legacy row (advisor blocker 1, code pass). A tree from before the
# manifest existed holds whichever version was bundled the day it was seeded;
# the legacy rule can only adopt a file that still matches the CURRENT bundle,
# so a pristine copy of an older bundle would never refresh — not on the second
# boot, not ever — and the four files Wave 4b itself changes (the
# `/data/agent_home` literals) would keep those literals on every existing
# volume. A file whose bytes are in this set is a pristine old seed: refreshed
# and recorded on the first boot. Frozen: a version is added when it is
# replaced, and never removed.
_PREVIOUSLY_SHIPPED_SEEDS = frozenset({
    # skills/gpt-image-2/SKILL.md
    "0a60045b89995e00d7461d3207540223e23e0f3b9c66c248a70c15e29681d3c7",  # 2be3e7f
    "1ed3ca506bda100efe0ff2d7e86ba7574f5a66d4383c1f956fdbc1e1ddd6ee47",  # cb956d6
    "2b1152cb2ba1cd72162aec686638ad67f9180862fbb6ee912099a91583539b3e",  # f2915b2
    # skills/linkedin-post/SKILL.md
    "b2bd823eaec683e89c9d489be844d4acb131312aee23836d4eaf7b46ce5c7191",  # 2be3e7f
    # skills/linkedin-post/references/visuals.md
    "e1b5c1ea2571fb9fc13002925b30ea84d0b53282f71733ae060ea9f1bad2318f",  # 2be3e7f
    "744b6acf6294611e447e9f5fba71409cb4acbd44a5751197b744966258cbe74a",  # cb956d6
    "313d298e4e70d3b2e65576b31daaec86f1e47218d0d0f4e861bd871dc4d34fb5",  # 853d35d
    # skills/skill-authoring/SKILL.md
    "5e6102a8be4aeae5806228dbe68dec49c3edb31176b64d30f5bbbc18683ebbb6",  # c871081
})


class SeedManifest:
    """What the bundle seeded into ONE tree, and the bytes it seeded (Wave 4b, D8).

    Lives at `agent_home/.claude/.seeded.json`, keys relative to `agent_home`
    (`.claude/settings.json`, `.claude/skills/foo/SKILL.md`,
    `.claude/agents/refuter.md`), values the sha256 at seed time. It is the record
    `_copy_missing` never had: with it, "absent" splits into "never seeded" (copy)
    and "the user deleted it" (leave absent), and "present" splits into "still the
    bytes we shipped" (refresh when the bundle moves) and "they patched it" (theirs,
    leave alone). `CLAUDE.md` is deliberately not in it — `seed_claude_md` and
    `_SHIPPED_PERSONA_SHA256` are a finished migration, and `persona.md` is the
    delivery channel for managed rules.

    The file lives where the agent can write it, so an unparseable one reads as
    EMPTY and never raises: the worst case is that every seed is classified as
    legacy once more, which costs one extra refresh on the next boot, not a boot
    that fails.

    Three processes (agent, worker, scheduler) `ensure()` the same tree at boot.
    Entries are updated in memory and `save()` re-reads the file immediately before
    the replace and merges: disk wins for every path this process did not touch, so
    a lost race costs at most one extra refresh next boot and never a permanent
    "user-modified" classification for a file the other process had just recorded.
    """

    FILENAME = ".seeded.json"

    def __init__(self, agent_home: Path):
        self._agent_home = agent_home
        self._path = agent_home / ".claude" / self.FILENAME
        self._entries: dict[str, str] = self._read()
        self._touched: dict[str, str] = {}
        self._modified: list[str] = []
        self._legacy: list[str] = []

    @property
    def path(self) -> Path:
        return self._path

    def _read(self) -> dict[str, str]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items() if isinstance(v, str)}

    def key(self, dest: Path) -> str:
        return dest.relative_to(self._agent_home).as_posix()

    def get(self, dest: Path) -> str | None:
        return self._entries.get(self.key(dest))

    def record(self, dest: Path, sha: str) -> None:
        self._entries[self.key(dest)] = sha
        self._touched[self.key(dest)] = sha

    def note_modified(self, dest: Path) -> None:
        self._modified.append(self.key(dest))

    def note_legacy(self, dest: Path) -> None:
        """A pre-manifest file that matches neither the current bundle nor any
        version ever shipped: it is left alone, and said so — silently never
        refreshing is the failure the manifest exists to end."""
        self._legacy.append(self.key(dest))

    def save(self) -> None:
        """Merge-and-replace (see the class docstring). Written only when this
        process recorded something or no manifest exists yet, so an idle boot
        does not rewrite the file in every tree."""
        if (self._modified or self._legacy) and self._agent_home not in _MODIFIED_REPORTED:
            _MODIFIED_REPORTED.add(self._agent_home)
            if self._modified:
                log.info("%d seeded file(s) under %s were modified by the user and "
                         "are not refreshed: %s", len(self._modified),
                         self._agent_home, ", ".join(sorted(self._modified)))
            if self._legacy:
                log.info("%d seeded file(s) under %s predate the manifest and match "
                         "no shipped version; they are not refreshed (delete one to "
                         "re-seed it): %s", len(self._legacy), self._agent_home,
                         ", ".join(sorted(self._legacy)))
        if not self._touched and self._path.exists():
            return
        merged = {**self._read(), **self._touched}
        try:
            _write_atomic(self._path, json.dumps(merged, indent=1, sort_keys=True)
                          .encode("utf-8"))
        except OSError:
            log.warning("could not write the seed manifest %s; the next boot "
                        "re-classifies these seeds as legacy", self._path,
                        exc_info=True)


def _seed_file(dest: Path, src: Path, manifest: SeedManifest | None) -> None:
    """One bundled file into one tree, by the D8 table:

    | dest    | record            | rule                                        |
    |---------|-------------------|---------------------------------------------|
    | absent  | none              | copy, record (new bundle file or first boot) |
    | absent  | present           | leave absent — the user deleted it           |
    | present | none              | legacy: record iff `sha(dest) == sha(src)`;  |
    |         |                   | a PREVIOUSLY shipped version refreshes now;  |
    |         |                   | anything else is left alone, logged once     |
    | present | == dest, != src   | refresh atomically, record the new hash      |
    | present | != dest           | user-modified: leave, log once per tree      |

    The archived-skill row is `seed_skills`' — it is a property of the skill
    directory, not of one file. `manifest=None` is `_copy_missing`, unchanged.
    """
    if manifest is None:
        _copy_missing(dest, src)
        return
    if _warn_missing_source(dest, src):
        return
    recorded = manifest.get(dest)
    src_bytes = src.read_bytes()
    src_sha = hashlib.sha256(src_bytes).hexdigest()
    if not dest.exists():
        if recorded is not None:
            return
        try:
            _write_atomic(dest, src_bytes)
        except OSError:
            log.warning("failed to seed %s from %s", dest, src, exc_info=True)
            return
        manifest.record(dest, src_sha)
        return
    dest_bytes = dest.read_bytes()
    dest_sha = hashlib.sha256(dest_bytes).hexdigest()
    if recorded is None:
        if dest_sha == src_sha:
            manifest.record(dest, src_sha)
            return
        if _normalized_sha256(dest_bytes) not in _PREVIOUSLY_SHIPPED_SEEDS:
            manifest.note_legacy(dest)
            return
        # A pristine copy of an OLDER bundle: ours to refresh, exactly as if the
        # manifest had recorded it the day it was seeded.
    elif recorded != dest_sha:
        manifest.note_modified(dest)
        return
    elif dest_sha == src_sha:
        return
    try:
        _write_atomic(dest, src_bytes)
    except OSError:
        log.warning("failed to refresh %s from %s", dest, src, exc_info=True)
        return
    manifest.record(dest, src_sha)


def _seed_each(dest_dir: Path, src_dir: Path, dirs: bool) -> None:
    if not src_dir.is_dir():
        return
    for item in src_dir.iterdir():
        if item.is_dir() == dirs:
            _copy_missing(dest_dir / item.name, item)


def _is_archived(skills_dir: Path, name: str) -> bool:
    """`skills/.archive/<name>` — the curator's aging pass and `skill_admin archive`
    both move a skill there, and `archive_skill` only suffixes once that plain
    name is taken, so the plain name is the whole test."""
    return (skills_dir / ".archive" / name).exists()


def seed_skills(skills_dir: Path, source: Path | None = None,
                manifest: SeedManifest | None = None) -> None:
    """Seed bundled skill directories (<name>/SKILL.md) into agent_home/.claude/skills/.

    With a manifest, file by file, so a bundled skill the user patched keeps their
    patch while its untouched siblings refresh. A name that sits under `.archive/`
    is skipped entirely, whatever the manifest says: before 4b every boot copied
    an archived bundled skill straight back in, so the curator archived it again
    a week later, forever (B-3).
    """
    src_root = _BUNDLED_SKILLS_DIR if source is None else source
    if not src_root.is_dir():
        return
    for bundle in src_root.iterdir():
        if not bundle.is_dir():
            continue
        if _is_archived(skills_dir, bundle.name):
            continue
        if manifest is None:
            _copy_missing(skills_dir / bundle.name, bundle)
            continue
        for src in sorted(p for p in bundle.rglob("*") if p.is_file()):
            _seed_file(skills_dir / bundle.name / src.relative_to(bundle), src, manifest)


def bundled_skill_names(source: Path | None = None) -> frozenset[str]:
    """The names of the skills this build ships — the only authority on "bundled".

    The seed manifest records what was seeded, not what is bundled NOW (a skill
    dropped from the bundle keeps its entry), and the sidecar's `created_by` is
    whatever the writer claimed — a bundled skill the agent later patched still
    says "agent". So the source tree is the fact, and a name is bundled iff it is
    still there.

    Empty when the source is absent — the CH_ASSETS case _copy_missing already
    warns about. A missing source must read as "nothing is bundled", never as an
    exception in a listing: the skills are still on the volume and still work.
    """
    src = _BUNDLED_SKILLS_DIR if source is None else source
    if not src.is_dir():
        return frozenset()
    try:
        return frozenset(d.name for d in src.iterdir() if d.is_dir())
    except OSError:
        return frozenset()


def seed_agents(agents_dir: Path, source: Path | None = None,
                manifest: SeedManifest | None = None) -> None:
    """Seed bundled subagent definitions (<name>.md) into agent_home/.claude/agents/.

    The CLI discovers these from cwd; the model reaches them via the `Agent` tool."""
    src_root = _BUNDLED_AGENTS_DIR if source is None else source
    if manifest is None:
        _seed_each(agents_dir, src_root, dirs=False)
        return
    if not src_root.is_dir():
        return
    for src in sorted(p for p in src_root.iterdir() if p.is_file()):
        _seed_file(agents_dir / src.name, src, manifest)


def seed_settings(claude_dir: Path, source: Path | None = None,
                  manifest: SeedManifest | None = None) -> None:
    """Seed the SDK-native settings.json into agent_home/.claude/.

    This file is the runtime's domain (advisorModel, permissions, env); the CLI
    reads it because we pass setting_sources=["project"] with cwd=agent_home.
    config.yaml stays the app's domain (nudges, curator, cron, telegram) — the two
    do not overlap, so they cannot drift."""
    _seed_file(
        claude_dir / "settings.json",
        (_BUNDLE_DIR / "settings.json") if source is None else source,
        manifest,
    )


PERSONA_IMPORT = "@.claude/persona.md"

# sha256 (with \r\n normalized to \n) of every bundle/CLAUDE.md ever committed.
# A legacy agent_home/CLAUDE.md matching one of these is a pure runtime seed with
# no local additions and is safe to swap for the import stub. Migration artifact:
# frozen forever, never grows — new persona versions ship via the persona.md
# overwrite in write_persona, which needs no hashes.
_SHIPPED_PERSONA_SHA256 = frozenset({
    "6d74d2d826f540836658b66efc6ce3810f6eed03c00c2aaa94646ed2bde55591",  # 2204c96
    "74ad3deb86c81ce002c2555ffd3364916d409b738615e7b540c81872b8d8b100",  # a7d8d2a
    "1df70941edde7dd2b48cc437b17349787019924f9384edd25ed332a0f56159c6",  # 93a5fa2
    "b95789fe3dc2d72c7602adac53f6d4b6c69ec9939a02b2dc1f6a3b977b6b2b12",  # 0d18166
    "46d669d9d86e195dcfe9a333bc1ab99ab4baee52833d381393cc75facb689ccf",  # 18a08c4
    "281975f5ecd38faa8d933b367fdf0e4df109fb3eb13a9634f04964a8698ca133",  # c871081
})


def _normalized_sha256(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _has_persona_import(text: str) -> bool:
    # Line-anchored on purpose: a prose mention of the path must not read as the
    # directive, or the persona silently never gets imported on that volume.
    return any(line.strip() == PERSONA_IMPORT for line in text.splitlines())


def write_persona(claude_dir: Path, source: Path | None = None) -> None:
    """The managed persona — runtime-owned, rewritten every boot.

    CLAUDE.md pulls it in via the CLI's @-import, so a persona fix reaches every
    volume on the next deploy without touching the agent's own file. This is the
    delivery mechanism _copy_missing cannot be: that one never overwrites, which
    is right for the agent's files and was wrong for ours."""
    src = (_BUNDLE_DIR / "persona.md") if source is None else source
    if not src.exists():
        log.warning("cannot write persona: bundled source %s does not exist "
                    "(is CH_ASSETS set and does the image ship it?)", src)
        return
    data = src.read_bytes()
    dest = claude_dir / "persona.md"
    if dest.exists() and dest.read_bytes() == data:
        return
    tmp = dest.with_suffix(".md.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def seed_claude_md(agent_home: Path, source: Path | None = None) -> None:
    """Seed the CLAUDE.md stub once, and migrate legacy files to the import scheme.

    The CLI loads CLAUDE.md from cwd regardless of setting_sources, so every fork
    (turn, review, curator, cron) sees it. CLAUDE.md itself is the agent's own
    file; the managed rules reach it through the @.claude/persona.md import. A
    legacy file that byte-matches a version the runtime once shipped (CRLF
    normalized — hand-synced copies from a Windows worktree are CRLF, and the
    live volume holds one) carries no local additions and is swapped for the
    stub. Anything else is kept whole and only gains the import line, loudly."""
    src = (_BUNDLE_DIR / "CLAUDE.md") if source is None else source
    dest = agent_home / "CLAUDE.md"
    if not dest.exists():
        _copy_missing(dest, src)
        return
    raw = dest.read_bytes()
    if _has_persona_import(raw.decode("utf-8", errors="replace")):
        return
    if _normalized_sha256(raw) in _SHIPPED_PERSONA_SHA256:
        if src.exists():
            dest.write_bytes(src.read_bytes())
        return
    dest.write_bytes(PERSONA_IMPORT.encode() + b"\n\n" + raw)
    log.warning(
        "CLAUDE.md at %s had local content and no persona import; the import line "
        "was prepended. Stale rule text may now duplicate the managed persona — "
        "reconcile by hand.", dest)


def home_dir() -> Path:
    """Resolve the state directory WITHOUT touching the filesystem.

    Pure resolution only. Callers that need the tree to exist (seeded skills, agents,
    settings, logs) must call get_home(), which creates and seeds it and returns the
    same path. This exists so a module that only needs to know WHERE state lives —
    catalog.py resolving the user-catalog — does not trigger first-boot seeding as a
    side effect.
    """
    return Path(os.environ.get("CH_HOME", str(Path.home() / ".claude-hermes")))


def user_catalog_dir(home: Path | None = None) -> Path:
    """Where chat-approved catalog entries live — the writable half of the catalog.

    The user-catalog is STATE, so NFR-5 fixes its location: under CH_HOME, always. That
    is the difference from catalog/ itself, which is CODE whose location genuinely is not
    derivable (src layout on a dev host vs site-packages in the image) and therefore needs
    CH_CATALOG to point at it. State needs no such variable, and giving it one would only
    let state escape the /data volume that `tar` is supposed to back up whole.

    Resolved on every call, never frozen into a module constant: load_all() re-reads the
    directory on every call by design, and an import-time constant would also mean a test
    setting CH_HOME arrives too late to be seen.

    One resolver, on purpose. When the writer derived this path from CH_HOME and the
    reader from the repo root, they agreed only in the image — where the Dockerfile set
    both consistently — and diverged silently everywhere else, so /approve reported
    success for an entry nothing could install. Given the same home, writer and reader now
    agree by construction; a caller that injects a *different* home is still on its own,
    which is why _handle_approve verifies the effect rather than trusting this.
    """
    return (home_dir() if home is None else home) / "user-catalog"


def get_home() -> Path:
    """Resolve the state directory, creating and seeding it. Never hardcode this path
    elsewhere; when you need resolution WITHOUT the side effects, use home_dir().

    Since Wave 4a this is `Workspace.owner(home_dir()).ensure()` — the owner's tree
    IS `CH_HOME`, so the bytes it creates are the same ones it always created.
    """
    return Workspace.owner(home_dir()).ensure().home


SHARED_WIKI_INDEX_SEED = """# Shared wiki

Pages under `shared/` are read by EVERY admitted user of this deploy, not only
the owner. Put here what the household or team has in common — people, places,
recurring plans — and keep private facts in the namespaces above. Only the
owner (and the owner's curator pass) writes here; every other user's turn sees
this tree read-only.

## Pages

(none yet)
"""


@dataclass(frozen=True)
class Workspace:
    """Every path a turn may touch, named once — for ONE user.

    The same six or seven `home / "agent_home" / ".claude" / "skills"` chains were
    spelled out in the runner, the review pass, the curator, the file store and
    three routers; each spelling was a chance to disagree about where state lives.
    A module is handed this object and asks it, so the layout is one fact.

    Wave 4a splits that one fact in two:

    * **`root` is per user.** `agent_home`, `.claude/{skills,agents}`, the wiki,
      `inbox/`/`outbox/`, `tmp/`, `.claude-cli/` and `logs/` all hang off it. The
      **owner's `root` IS `home`**, so every one of those paths is byte-identical
      to what it was before this wave — `/data/agent_home`, `/data/.claude-cli`,
      `/data/logs` do not move, a live volume needs no migration, and SDK sessions
      keep resuming.
    * **`home` stays deploy-wide.** `user_catalog`, `plugins_dir`, `secrets_path`,
      `drafts_dir` and `pending_approvals_dir` belong to the deployment, not to a
      person: an install changes the prompt prefix and the MCP servers every turn
      composes, so it is the owner's decision and the owner's credentials (D8).

    `root` is a required `Path`, never `None`: one representation of the owner, so
    no caller has to remember which of two spellings it was handed. Build with
    `Workspace.owner(home)` or `Workspace.for_user(home, workspace_dir)`.

    Frozen and side-effect free: constructing it touches no filesystem. Call
    `ensure()` when the tree must exist and be seeded.
    """

    home: Path
    root: Path

    @classmethod
    def owner(cls, home: Path) -> "Workspace":
        """The deploy owner's workspace: `root` is `CH_HOME` itself."""
        return cls(home, home)

    @classmethod
    def for_user(cls, home: Path, workspace_dir: str) -> "Workspace":
        """A user's workspace from their `users.workspace_dir` (`""` = the owner's).

        The empty string is not a special case bolted on: it is how the OWNER's row
        is written, precisely so that this function returns the pre-4a workspace for
        them without a branch anywhere else.
        """
        return cls(home, home / workspace_dir if workspace_dir else home)

    @classmethod
    def resolve(cls) -> "Workspace":
        """The live OWNER workspace, created and seeded (see get_home)."""
        return cls.owner(get_home())

    # --- per user (derived from `root`) --------------------------------------

    @property
    def agent_home(self) -> Path:
        return self.root / "agent_home"

    @property
    def claude_dir(self) -> Path:
        return self.agent_home / ".claude"

    @property
    def skills_dir(self) -> Path:
        return self.claude_dir / "skills"

    @property
    def agents_dir(self) -> Path:
        return self.claude_dir / "agents"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def wiki_root(self) -> Path:
        return self.agent_home / "wiki"

    @property
    def inbox_dir(self) -> Path:
        return self.agent_home / "inbox"

    @property
    def outbox_dir(self) -> Path:
        return self.agent_home / "outbox"

    @property
    def tmp_dir(self) -> Path:
        """`TMPDIR` for this user's CLI. `/tmp` is shared by every lane in the
        container, so a scratch file written there is readable by every other
        turn; this one is inside the read guard's allowed roots and nobody
        else's."""
        return self.root / "tmp"

    @property
    def cli_config_dir(self) -> Path:
        """`CLAUDE_CONFIG_DIR` — where the CLI keeps its own transcript store, and
        therefore what `resume` reads. Per user, or one user's `--resume` would
        replay another's conversation."""
        return self.root / ".claude-cli"

    @property
    def is_owner(self) -> bool:
        """The deploy owner's workspace is the one whose `root` IS `home`."""
        return self.root == self.home

    # --- deploy-wide (derived from `home`) -----------------------------------

    @property
    def shared_wiki_root(self) -> Path:
        """The one wiki namespace every admitted user may READ: `shared/` inside
        the OWNER's wiki (`CH_HOME/agent_home/wiki/shared`).

        It lives inside the owner's tree on purpose: the owner edits it with the
        same native `Write`/`Edit` and the same `index.md` discipline as any other
        page, and nothing new has to be mounted for them. For everybody else it
        is an extra read root and a denied write root — `readonly_guard_factory`
        in `integrations/claude_sdk/guards.py` is the fence. Deploy-wide, so it
        hangs off `home`, not `root`.
        """
        return self.home / "agent_home" / "wiki" / "shared"


    @property
    def user_catalog(self) -> Path:
        # Through the resolver, so writer and reader cannot disagree (see above).
        return user_catalog_dir(self.home)

    @property
    def plugins_dir(self) -> Path:
        """Plugin clones, `CH_HOME/plugins` since Wave 4a — **not** under
        `agent_home` any more (D8).

        A plugin is deploy-wide (the owner installed it, it changes every turn's
        prompt prefix), and leaving the clones inside the owner's `agent_home`
        would mean a non-owner's turn could only load one if its read guard
        admitted the owner's whole tree. `ensure()` performs the one-time move.
        """
        return self.home / "plugins"

    @property
    def drafts_dir(self) -> Path:
        return self.home / "catalog-drafts"

    @property
    def pending_approvals_dir(self) -> Path:
        return self.home / "pending-approvals"

    @property
    def secrets_path(self) -> Path:
        return self.home / "secrets.env"

    # --- the one side effect --------------------------------------------------

    def ensure(self) -> "Workspace":
        """Create and seed this user's tree. Idempotent; returns self.

        For the owner this is exactly what `get_home()` always did, which is why
        `get_home()` is now one line over it. For everybody else it is the same
        tree under `root`. `write_persona` self-heals on every call; `settings.json`,
        the bundled skills and agents go through ONE `SeedManifest` per tree (D8):
        refreshed while the user never touched them, left alone once they did,
        left absent once they deleted or archived them. A tree from before the
        manifest existed is "legacy" until the first boot records its pristine
        files — or, for a pristine copy of an OLDER bundle
        (`_PREVIOUSLY_SHIPPED_SEEDS`), refreshes it on that first boot; a legacy
        file that matches no shipped version is left alone and logged once.

        The deploy-wide directories are created only by the owner's call: they
        belong to the deployment, and a second user's first message should not be
        what brings `pending-approvals/` into existence.
        """
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.cli_config_dir.mkdir(parents=True, exist_ok=True)
        # The directory only: the wiki's CONTENT is memory's to seed (core may not
        # import a module), and `WikiStore.seed` is idempotent on an empty tree.
        self.wiki_root.mkdir(parents=True, exist_ok=True)
        if self.root == self.home:
            self._ensure_deploy_wide()
        manifest = SeedManifest(self.agent_home)
        seed_skills(self.skills_dir, manifest=manifest)
        seed_agents(self.agents_dir, manifest=manifest)
        seed_settings(self.claude_dir, manifest=manifest)
        manifest.save()
        write_persona(self.claude_dir)
        seed_claude_md(self.agent_home)
        return self

    def _ensure_deploy_wide(self) -> None:
        """What belongs to the deployment rather than to a person (D8)."""
        # Through the resolver, not a third spelling of the same path.
        for sub in ("mcp", "plugins"):
            (self.user_catalog / sub).mkdir(parents=True, exist_ok=True)
        self.pending_approvals_dir.mkdir(parents=True, exist_ok=True)
        self._move_legacy_plugins()
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self._seed_shared_wiki()

    def _seed_shared_wiki(self) -> None:
        """`shared/` exists from the first boot, with an `index.md` that says what
        it is for. Idempotent on the index file: the owner owns every byte after
        that, exactly like the rest of their wiki."""
        self.shared_wiki_root.mkdir(parents=True, exist_ok=True)
        index = self.shared_wiki_root / "index.md"
        if not index.exists():
            index.write_text(SHARED_WIKI_INDEX_SEED, encoding="utf-8")

    def _move_legacy_plugins(self) -> None:
        """One-time: `agent_home/plugins` -> `<home>/plugins` (D8).

        Every process (agent, worker, scheduler) runs `ensure()` for the owner at
        boot, so three of these can race. `os.replace` is atomic, the loser gets an
        `OSError`, and the only thing that matters afterwards is whether the
        destination is there — which is what is re-checked. Logged at WARNING with
        both paths and the entry count because it moves a deployment's plugin
        clones out from under any path a human has written down.
        """
        legacy = self.agent_home / "plugins"
        if not legacy.is_dir() or self.plugins_dir.exists():
            return
        try:
            entries = len(list(legacy.iterdir()))
        except OSError:
            entries = -1
        try:
            os.replace(legacy, self.plugins_dir)
        except OSError:
            if self.plugins_dir.is_dir():
                log.warning("plugin clones were moved to %s by another process",
                            self.plugins_dir)
            else:
                log.warning("could not move plugin clones from %s to %s; they stay "
                            "where they are and a non-owner turn will not load them",
                            legacy, self.plugins_dir, exc_info=True)
            return
        log.warning("moved %s plugin clone(s) from %s to %s: plugins are deploy-wide "
                    "since Wave 4a", entries, legacy, self.plugins_dir)


class WorkspaceResolver:
    """Which workspace a user — or a lane — runs in. The port every module receives.

    Core may not import a module, so the two lookups arrive as async callables that
    `main.build_app` binds to the ACL: `workspace_dir_of(user_id) -> str` and
    `deploy_owner() -> int`. This class therefore knows the layout and the rule, and
    nothing about identity.

    Two resolutions, and the difference between them matters:

    * `for_user(user_id)` asks the table. It is what the blob mirror and the cron
      executor need, because they hold an `owner_id` and no key.
    * `for_lane(session_key, channel, user_id)` **reads no table at all**. It applies
      `lane_owner` — the same rule `sessions.owner_id` is written from and the same
      rule the `0005` backfill spelled in SQL — to the key, given the deploy owner.
      That is what lets a brand-new lane whose `sessions` row does not exist yet
      resolve exactly like an old one, and what makes it impossible for the row and
      the running turn to disagree about whose tree the turn is in.

    `ensure()` runs once per user per process: the tree is created and seeded lazily,
    on first use, not eagerly at boot from the allowlist (open question 1). Two
    workers ensuring the same brand-new user at the same moment is normal and safe —
    both write the same bytes atomically, and `SeedManifest.save` merges the two
    processes' entries rather than letting the last writer drop the other's.
    """

    def __init__(self, home: Path, *,
                 workspace_dir_of: Callable[[int], Awaitable[str]],
                 deploy_owner: Callable[[], Awaitable[int]]):
        self._home = home
        self._workspace_dir_of = workspace_dir_of
        self._deploy_owner = deploy_owner
        self._cache: dict[int, Workspace] = {}
        self._owner_id: int | None = None

    @property
    def home(self) -> Path:
        return self._home

    async def owner_id(self) -> int:
        """The deploy owner, cached.

        Cached only once it is a real id: on a deploy nobody has claimed yet the
        answer is 0, and caching that would leave every process resolving group and
        cron lanes to nobody for the rest of its life.
        """
        if self._owner_id:
            return self._owner_id
        owner = int(await self._deploy_owner() or 0)
        if owner:
            self._owner_id = owner
        return owner

    async def for_user(self, user_id: int) -> Workspace:
        """This user's workspace, created and seeded on first use in this process.

        Propagates whatever `workspace_dir_of` raises for an unknown id
        (`UnknownUser`): falling back to the owner's tree here would be the exact
        cross-user leak this wave exists to prevent, and it would be silent.
        """
        cached = self._cache.get(user_id)
        if cached is not None:
            return cached
        workspace = Workspace.for_user(self._home,
                                       await self._workspace_dir_of(user_id))
        workspace.ensure()
        # Cached AFTER ensure: a failed seeding must not leave a workspace object
        # behind that every later turn trusts.
        self._cache[user_id] = workspace
        return workspace

    async def for_lane(self, session_key: str, channel: str,
                       user_id: int) -> Workspace:
        """The workspace a turn in this lane runs in. No table read (see above)."""
        return await self.for_user(
            lane_owner(session_key, channel, user_id, await self.owner_id()))

    async def mirror_root(self, owner_id: int) -> Path:
        """Where a blob row's on-disk mirror lives — `agent_home` of the row's OWNER.

        Takes the row's `owner_id`, never the requester's: the download route
        re-resolves the path against whoever the row says owns it, which is what
        stops a forged or guessed file id reading out of somebody else's tree.
        """
        return (await self.for_user(owner_id)).agent_home
