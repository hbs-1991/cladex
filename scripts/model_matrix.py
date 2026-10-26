"""Live model matrix for the pinned SDK/CLI pair: what each candidate model actually
does on THIS CLI, under THIS auth. Run once per SDK/CLI bump, before a model enters
`models:` in config.yaml (docs/deploy.md, "Offering a model").

Not part of the package or the test suite: it calls the real API on whatever
credentials the environment holds and prints one JSON document. In production that is
`CLAUDE_CODE_OAUTH_TOKEN`, so run it in an image built from the branch:

    docker run --rm --env-file token.env <image> python scripts/model_matrix.py

Every chain of calls gets a fresh `CLAUDE_CONFIG_DIR`, the way a new user's lane does,
so a one-time consent that lives in a config dir shows up here as the prompt it would
be for them. `CLAUDE_CODE_NO_MODEL_FALLBACK=1` is set throughout: a model the CLI
would quietly swap must fail here, not answer as something else.

What it records, per candidate: the CLI's own catalog entry (window, 1M suffix,
pricing tier, effort capabilities, default effort — read out of the bundled binary),
the answer with and without `[1m]`, each effort level, a resume of one session on
every other model (and the cache figures that come back), and what a rejected model
looks like: the `ResultError` fields, the errored `AssistantMessage`, and whether the
prompt is in the transcript twice after the retry.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path

import claude_agent_sdk
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query
from claude_agent_sdk._cli_version import __cli_version__

CANDIDATES = ["claude-sonnet-5", "claude-opus-5", "claude-opus-5-5", "claude-fable-5-1"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
PROMPT = "Reply with the single word: ok"
REJECTED = "claude-opus-9-9"          # an id no CLI knows, for the rejection signature
CREDIT_WORDS = re.compile(r"usage credits|consent|extra usage", re.I)


# --- the CLI's own catalog ------------------------------------------------------------

def _bundled_cli() -> Path | None:
    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled"
    for name in ("claude", "claude.exe"):
        if (bundled / name).is_file():
            return bundled / name
    return None


def _entry(blob: bytes, model_id: str) -> str | None:
    """The JS object literal `{id:"<model_id>",...}` in the minified binary, by brace
    matching — grep cannot do it, the entries span template-literal newlines."""
    start = blob.find(b'{id:"%s",' % model_id.encode())
    if start < 0:
        return None
    depth = 0
    for end in range(start, min(start + 8000, len(blob))):
        ch = blob[end:end + 1]
        if ch == b"{":
            depth += 1
        elif ch == b"}":
            depth -= 1
            if depth == 0:
                return blob[start:end + 1].decode("utf-8", "replace")
    return None


def catalog(ids: list[str]) -> dict:
    cli = _bundled_cli()
    if cli is None:
        return {"error": "no bundled CLI found"}
    blob = cli.read_bytes()
    out: dict = {}
    for model_id in ids:
        text = _entry(blob, model_id)
        if text is None:
            out[model_id] = None
            continue

        def field(pattern: str) -> str | None:
            m = re.search(pattern, text)
            return m.group(1) if m else None

        caps = field(r"capabilities:\[([^\]]*)\]")
        window = field(r"window:([0-9.e+]+)")
        out[model_id] = {
            "display_name": field(r'display_name:"([^"]*)"'),
            "window": int(float(window)) if window else None,
            "native_1m": "native_1m:!0" in text,
            "supports_1m_suffix": "supports_1m_suffix:!0" in text,
            "pricing": field(r'pricing:"([^"]*)"'),
            "capabilities": re.findall(r'"([^"]+)"', caps) if caps else [],
            "default_effort": field(r'default_effort:"([^"]*)"'),
            "fallback_chain": field(r"fallback_chain:(\[[^\]]*\])"),
        }
    return out


# --- one call -------------------------------------------------------------------------

async def probe(model: str, config_dir: Path, *, effort: str | None = None,
                resume: str | None = None, fork: bool = False) -> dict:
    """One `query()` and everything it said about which model ran and why it failed."""
    cwd = Path(tempfile.mkdtemp(prefix="matrix-cwd-"))
    options = ClaudeAgentOptions(
        model=model, max_turns=1, setting_sources=[], tools=[], cwd=str(cwd),
        resume=resume, fork_session=fork,
        env={"CLAUDE_CONFIG_DIR": str(config_dir), "CLAUDE_CODE_NO_MODEL_FALLBACK": "1"},
        **({"effort": effort} if effort else {}),
    )
    rec: dict = {"requested": model, "effort": effort, "resumed": bool(resume),
                 "answered": [], "assistant_errors": [], "text": ""}
    try:
        async for msg in query(prompt=PROMPT, options=options):
            if isinstance(msg, AssistantMessage) and msg.parent_tool_use_id is None:
                rec["answered"].append(msg.model)
                if msg.error:
                    rec["assistant_errors"].append(str(msg.error))
                rec["text"] += "".join(b.text for b in msg.content if isinstance(b, TextBlock))
            elif isinstance(msg, ResultMessage):
                rec.update({
                    "session_id": msg.session_id, "subtype": msg.subtype,
                    "is_error": msg.is_error, "api_error_status": msg.api_error_status,
                    "terminal_reason": msg.terminal_reason, "errors": msg.errors,
                    "result": (msg.result or "")[:300], "cost_usd": msg.total_cost_usd,
                    "usage": {k: (msg.usage or {}).get(k) for k in (
                        "input_tokens", "cache_read_input_tokens",
                        "cache_creation_input_tokens", "output_tokens")},
                    "model_usage": {k: {"contextWindow": v.get("contextWindow"),
                                        "canonicalModel": v.get("canonicalModel")}
                                    for k, v in (msg.model_usage or {}).items()},
                })
        rec["ok"] = not rec.get("is_error", False)
    except Exception as e:     # noqa: BLE001 — recording the failure IS the point
        rec["ok"] = False
        rec["exception"] = {
            "type": type(e).__name__, "text": str(e)[:300],
            **{a: getattr(e, a, None) for a in (
                "subtype", "errors", "api_error_status", "terminal_reason")},
            "result": (getattr(e, "result", None) or "")[:300],
        }
    rec["text"] = rec["text"][:200]
    blob = json.dumps(rec, default=str)
    rec["mentions_credits_or_consent"] = bool(CREDIT_WORDS.search(blob))
    # `<synthetic>` is the CLI's own stand-in message for an API error, not a model
    real = [a for a in rec["answered"] if a != "<synthetic>"]
    rec["substituted"] = any(a.split("[")[0] != model.split("[")[0] for a in real)
    return rec


def _fresh_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="matrix-cfg-"))


def _prompt_count(config_dir: Path, session_id: str | None) -> int | None:
    """How many user entries in the session's transcript carry PROMPT."""
    if not session_id:
        return None
    for path in config_dir.rglob(f"{session_id}.jsonl"):
        count = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("type") == "user" and PROMPT in json.dumps(row.get("message", "")):
                count += 1
        return count
    return None


# --- the matrix -----------------------------------------------------------------------

async def run(models: list[str], *, efforts: bool) -> dict:
    out: dict = {
        "sdk": claude_agent_sdk.__version__,
        "cli": __cli_version__,
        "catalog": catalog(models),
        "plain_and_1m": [], "efforts": [], "switch": [], "rejection": {},
    }
    runs = {}
    for model in models:
        for spelling in (model, f"{model}[1m]"):
            rec = await probe(spelling, _fresh_dir())
            out["plain_and_1m"].append(rec)
            if spelling == model:
                runs[model] = rec["ok"]

    if efforts:
        for model in (m for m in models if runs.get(m)):
            for effort in EFFORTS:
                out["efforts"].append(await probe(model, _fresh_dir(), effort=effort))

    # One session, resumed (forked) on every other model: does it run, and is the
    # cache warm or cold afterwards?
    base_dir = _fresh_dir()
    base = await probe(models[0], base_dir)
    out["switch"].append({"base": base})
    if base["ok"]:
        for model in models[1:]:
            rec = await probe(model, base_dir, resume=base["session_id"], fork=True)
            out["switch"].append({"from": models[0], "to": model, "probe": rec})
    if "claude-opus-5" in models:
        opus_dir = _fresh_dir()
        first = await probe("claude-opus-5", opus_dir)
        if first["ok"]:
            again = await probe("claude-opus-5[1m]", opus_dir,
                                resume=first["session_id"], fork=True)
            out["switch"].append({"from": "claude-opus-5", "to": "claude-opus-5[1m]",
                                  "probe": again})

    # What a rejection looks like, and what a retry on a good model leaves behind.
    rej_dir = _fresh_dir()
    seed = await probe(models[0], rej_dir)
    if seed["ok"]:
        rejected = await probe(REJECTED, rej_dir, resume=seed["session_id"])
        retry = await probe(models[0], rej_dir, resume=seed["session_id"])
        out["rejection"] = {
            "rejected": rejected, "retry_on_good_model": retry,
            "prompt_entries_in_transcript": _prompt_count(rej_dir, seed["session_id"]),
            "note": "3 entries = seed + rejected + retry; 2 means the rejected prompt "
                    "never reached the transcript",
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--models", nargs="+", default=CANDIDATES,
                        help="candidate ids; the first is the base for the switch chain")
    parser.add_argument("--no-efforts", action="store_true", help="skip the effort sweep")
    parser.add_argument("--catalog-only", action="store_true",
                        help="read the CLI's catalog and make no API call")
    args = parser.parse_args()
    if args.catalog_only:
        result = {"sdk": claude_agent_sdk.__version__, "catalog": catalog(args.models)}
    else:
        result = asyncio.run(run(args.models, efforts=not args.no_efforts))
    json.dump(result, sys.stdout, indent=1, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
