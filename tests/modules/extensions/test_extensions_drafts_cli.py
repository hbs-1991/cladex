"""`python -m claude_hermes.main drafts list|show|delete`.

The operator channel, and the one `scripts/approve_draft.py` parses. The tab-separated
`list` format is therefore a contract, not a rendering: the host script splits on it to
decide what to show a human and what to copy into `catalog/`.
"""
from __future__ import annotations

import io

from claude_hermes.modules.extensions.presentation import cli

from tests.ext_helpers import drafts_for

STDIO = """manifest_version: 1
name: someserver
label: Some Server
description: Does a thing.
version: 1.0.0
transport: stdio
command: npx
args: ["-y", "@vendor/mcp-someserver"]
scopes: [turn]
"""
PLUGIN = """manifest_version: 1
name: someplugin
label: Some Plugin
description: Does a thing.
version: 1.0.0
repo: https://github.com/owner/repo
ref: v1.2.0
scopes: [turn]
"""


async def run(argv, drafts):
    out, err = io.StringIO(), io.StringIO()
    code = await cli.run(argv, drafts, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


async def test_list_is_tab_separated_with_the_kind_and_the_name(engine):
    drafts = drafts_for(engine)
    await drafts.save(STDIO)
    await drafts.save(PLUGIN)
    code, out, _err = await run(["list"], drafts)
    assert code == 0
    rows = [line.split("\t") for line in out.splitlines()]
    assert rows == [
        ["pending", "plugin", "someplugin", "Some Plugin"],
        ["pending", "mcp", "someserver", "Some Server"],
    ]


async def test_list_shows_a_rejected_draft_with_its_reason(engine):
    """A draft that the validator has since tightened against must not simply
    vanish from the operator's screen."""
    drafts = drafts_for(engine)
    await drafts.put_raw("plugin", "stale", PLUGIN.replace("ref: v1.2.0", "ref: main")
                                                  .replace("someplugin", "stale"))
    code, out, _err = await run(["list"], drafts)
    bucket, kind, name, reason = out.strip().split("\t")
    assert (code, bucket, kind, name) == (0, "rejected", "plugin", "stale")
    assert "ref" in reason


async def test_the_label_cannot_redraw_the_operators_terminal(engine):
    """The label came out of a README an attacker may have written, and it is about
    to be printed on the one screen a human is supposed to read carefully."""
    drafts = drafts_for(engine)
    await drafts.save(STDIO.replace("label: Some Server",
                                    'label: "Safe\\e[2K\\rcommand: npx"'))
    _code, out, _err = await run(["list"], drafts)
    assert "\x1b" not in out and "\r" not in out
    assert len(out.splitlines()) == 1


async def test_show_writes_the_bytes_and_nothing_else(engine):
    """What `approve_draft.py` copies into catalog/ verbatim — so no header, and no
    newline this did not read out of the row."""
    drafts = drafts_for(engine)
    await drafts.save(STDIO)
    code, out, _err = await run(["show", "someserver"], drafts)
    assert code == 0
    assert out == await drafts.read("mcp", "someserver")


async def test_show_finds_a_plugin_too_without_being_told_the_kind(engine):
    drafts = drafts_for(engine)
    await drafts.save(PLUGIN)
    code, out, _err = await run(["show", "someplugin"], drafts)
    assert code == 0 and "someplugin" in out


async def test_show_of_an_unknown_name_fails_loudly_on_stderr(engine):
    """Exit code and stderr, not stdout: the caller is redirecting stdout into a
    catalog file, and a `Not found` landing there would be the whole bug."""
    drafts = drafts_for(engine)
    code, out, err = await run(["show", "ghost"], drafts)
    assert code == 1 and out == "" and "ghost" in err


async def test_delete_clears_both_spellings(engine):
    """The kind is not on the command line, and a rejected draft may have no
    parseable manifest to ask."""
    drafts = drafts_for(engine)
    await drafts.save(STDIO)
    code, _out, _err = await run(["delete", "someserver"], drafts)
    assert code == 0
    assert await drafts.read("mcp", "someserver") is None
    assert await run(["delete", "someserver"], drafts) == (0, "deleted someserver\n", "")


async def test_an_unknown_or_malformed_subcommand_prints_the_usage(engine):
    drafts = drafts_for(engine)
    for argv in ([], ["nope"], ["show"], ["show", "a", "b"], ["delete"]):
        code, out, err = await run(argv, drafts)
        assert code == 2 and out == "" and cli.USAGE in err, argv
