"""The bodies behind the `mcp__hermes__*` tools.

Each file holds one handler and nothing else, so the registration table in
`hermes_server.py` stays readable as a list of gates. Handlers that belong to
another module (cron, skills, extensions) are one-line adapters over that
module's port — the body itself is not ours to hold.
"""
