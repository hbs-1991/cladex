from claude_hermes.integrations.claude_sdk.guards import bash_guard, check_command


def test_check_command_blocks_catastrophic():
    assert check_command("rm -rf /") is not None
    assert check_command("rm -rf / --no-preserve-root") is not None
    assert check_command("mkfs.ext4 /dev/sda1") is not None
    assert check_command("dd if=/dev/zero of=/dev/sda") is not None
    assert check_command(":(){ :|:& };:") is not None
    assert check_command("cat /app/.env") is not None
    assert check_command("cat ~/.env") is not None


def test_check_command_allows_normal_work():
    assert check_command("ls -la && rm -rf ./build") is None
    assert check_command("grep -r TODO src/") is None
    assert check_command("git commit -m 'env update'") is None


async def test_bash_guard_hook_envelope():
    deny = await bash_guard(
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}, "t1", None
    )
    out = deny["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert out["hookEventName"] == "PreToolUse"
    allow = await bash_guard(
        {"tool_name": "Bash", "tool_input": {"command": "ls"}}, "t2", None
    )
    assert allow == {}
