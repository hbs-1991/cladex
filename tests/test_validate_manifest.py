import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "validate_manifest",
    Path(__file__).resolve().parents[1] / "scripts" / "validate_manifest.py",
)
vm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vm)

GOOD = """
manifest_version: 1
name: remote
label: Remote
description: A remote server.
version: 1.0.0
transport: http
url: https://mcp.example.com/mcp
scopes: [turn]
"""


def test_a_valid_manifest_passes_and_prints_its_surface():
    code, out = vm.check(GOOD)
    assert code == 0
    assert "url:" in out and "https://mcp.example.com/mcp" in out


def test_a_shell_command_is_rejected():
    bad = GOOD.replace(
        "transport: http\nurl: https://mcp.example.com/mcp",
        "transport: stdio\ncommand: bash\nargs: [\"-c\", \"echo hi\"]",
    )
    code, out = vm.check(bad)
    assert code == 1 and "allowlist" in out


def test_a_non_https_url_is_rejected():
    code, out = vm.check(GOOD.replace("https://", "http://"))
    assert code == 1 and "https" in out
