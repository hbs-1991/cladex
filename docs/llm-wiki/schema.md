---
id: system.wiki-schema
kind: system
status: current
summary: Schema and maintenance rules for the project wiki.
verification:
  base_commit: a017bfb58ce36bd8f87340a483567a5fa45fac7f
  evidence_fingerprint: sha256:87b1d9e98979b7fe40d1c11e89c06641af04f7f633deae90fa439abc6414c92d
evidence:
  - path: llm-wiki.yaml
relations: []
---
# Wiki Schema

## Authority

1. Code, tests, schemas, migrations, and configuration describe current behavior.
2. Approved specifications and ADRs describe intended behavior and rationale.
3. Wiki pages are derived synthesis.
4. Conversations and failed hypotheses are not evidence.

## Required page metadata

- `id`
- `kind`
- `status`
- `summary`
- `verification.base_commit`
- `verification.evidence_fingerprint`
- `evidence`

## Material updates

Update the wiki for behavior, boundaries, dependencies, contracts, invariants, operations, architectural decisions, and confirmed reusable failure modes.

Do not record formatting, transient debugging, failed hypotheses, or unchanged implementation details.
