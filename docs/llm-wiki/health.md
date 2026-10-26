---
id: health.current
kind: health
status: current
summary: Unresolved wiki contradictions, stale evidence, and knowledge gaps.
verification:
  base_commit: a017bfb58ce36bd8f87340a483567a5fa45fac7f
  evidence_fingerprint: sha256:87b1d9e98979b7fe40d1c11e89c06641af04f7f633deae90fa439abc6414c92d
evidence:
  - path: llm-wiki.yaml
relations: []
---
# Wiki Health

## Known gaps

- **operations/** has no pages yet: deployment runbook (VPS specifics,
  `chown 1000:1000 ./data`), backup/restore procedure and BotFather setup
  live only in README.
- **Persona delivery is fixed for CLAUDE.md only** (see
  [[quality.failure-modes]]): the managed persona now ships as
  `.claude/persona.md`, rewritten every boot and imported by the CLAUDE.md
  stub. Seeded `skills/` and `agents/` still never refresh in live
  deployments — by design (agent-owned procedural memory), but a mandatory
  fix to a bundled skill still has no delivery path.
- **`scripts/deploy.sh` still asserts `data/state.db`** in the archive it takes,
  and the relational store is PostgreSQL now: on a host built after the move that
  file never exists, so the script would refuse its own backup and stop with the
  stack down. The dump it should assert is in [[system.operations]].
- **The `modules/` tree is one layer deep**: only `domain/models.py` per module
  while the code that uses those tables still lives in the flat package. That is
  the planned intermediate state after wave 1
  ([[decision.postgres-and-modular-core]]), but a reader who expects the four
  DDD layers will not find them yet.
- The `docs/superpowers/` plans and specs predate some implementations;
  where they conflict with code, code wins (wiki authority rule).
