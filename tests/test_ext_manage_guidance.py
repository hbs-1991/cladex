"""The ext_manage tool description must tell the model to re-run 'add' on a retry.

This is a regression test for a real failure, not a hypothetical one. The fatsecret
entry once declared credential names the owner had not set; `add` correctly reported
the missing secret, the owner fixed the manifest AND had the credentials on disk, and
the next `add fatsecret` still answered "send /secret FATSECRET_CLIENT_SECRET". The
tool was never re-run — the model answered from the earlier turn in its own context.

That failure mode is structural, not incidental. /secret is gateway-intercepted, so a
secret arriving is *invisible* to the model: nothing in the conversation ever
contradicts a stale missing-secret list, and the catalog can change under it too.
So the instruction to always re-run has to live in the tool description.

Asserted against the source of the module that DECLARES the tool rather than a live
list_tools round-trip: the description is an inline literal, and the point here is
only that the guidance cannot be quietly deleted. The literal is parsed with ast and
evaluated, not string-matched in the raw source — it is written as implicit
concatenation across many lines, so a raw search would miss any phrase that happens
to straddle two adjacent literals.

The file read is `modules/extensions/presentation/agent_tool.py`, not the MCP server:
Wave 2b made that module's `TOOL_DESCRIPTION` the one definition, carried to
`hermes_server.build_hermes_server` on the port (`ToolSpec`). Pointing this test at
the server was what forced a second, byte-identical copy of the paragraph to live
there.
"""

import ast
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "src" / "claude_hermes" / "modules"
       / "extensions" / "presentation" / "agent_tool.py")


def _ext_manage_description() -> str:
    """The ext_manage description, concatenated exactly as Python builds it."""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "TOOL_DESCRIPTION"
                        for t in node.targets)):
            return ast.literal_eval(node.value)
    raise AssertionError("no TOOL_DESCRIPTION assignment found in agent_tool.py")


def test_the_description_requires_rerunning_add_on_a_retry():
    d = _ext_manage_description()
    assert "re-run action='add'" in d, (
        "ext_manage must tell the model to re-run 'add' when the user asks again; "
        "without it a stale missing-secret answer gets repeated forever."
    )


def test_the_description_forbids_repeating_a_stale_missing_secret_list():
    d = _ext_manage_description()
    assert "Never repeat an earlier missing-secret list" in d


def test_the_description_explains_why_the_model_cannot_see_a_secret_arrive():
    """Without the reason, the rule reads as arbitrary and gets optimized away."""
    d = _ext_manage_description()
    assert "handled outside the conversation" in d


def test_the_no_key_in_chat_rules_survive():
    """The re-run guidance was inserted next to these; a careless edit could drop them."""
    d = _ext_manage_description()
    assert "never ask them to type a key into the conversation" in d
    assert "never repeat a key back" in d
