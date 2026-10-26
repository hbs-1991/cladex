---
id: decision.claude-agent-sdk
kind: decision
status: current
summary: Build on the Claude Agent SDK; implement only what it does not provide.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:d11ca5e1c459fd994ded6702c3c43598bb3a793ca1820e4f76751d985e13e69f
evidence:
  - path: docs/PRD.md
  - path: pyproject.toml
  - path: src/claude_hermes/modules/turns/application/turn_service.py
  - path: src/claude_hermes/modules/turns/infrastructure/sdk_runner.py
relations:
  - system.overview
---
# Decision: Build on the Claude Agent SDK

## Context

claude-hermes is an independent reimplementation of the ideas behind
NousResearch/hermes-agent. Hermes hand-rolls its agent loop, provider adapters,
plugin system (~2466 lines) and MCP client (~5627 lines) because it sits directly
on the OpenAI SDK (PRD §1, extensions design spec "Background").

## Decision

Target the Claude Agent SDK (Python, `claude-agent-sdk>=0.2`) and Claude models
only. The SDK provides the agent loop, streaming, retries, tool dispatch, context
compaction, prompt caching, skills runtime, subagents, hooks/permissions, session
transcripts, external MCP server configs and plugins. The project implements only
the four things the SDK does not provide: the messaging gateway, the learning
loop, scheduled automations, and cross-session recall (PRD §1, §7).

Provider-agnosticism is an explicit non-goal (PRD §2.2).

## Consequences

- ~10 focused modules instead of Hermes' ~3000 files (PRD §7).
- SDK API drift is a named risk: the SDK version is pinned in `pyproject.toml`
  and CI catches breakage on upgrade (PRD §9).
- New capability lands narrow-waist-first: prompt/skill → in-process MCP tool →
  external MCP server → core change (PRD P2).
