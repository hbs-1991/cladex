---
id: decision.wiki-index-not-store
kind: decision
status: current
summary: Memory stays markdown the model writes with native Write/Edit; PostgreSQL gets a derived index over it (`wiki_pages` + `memory_search`), not the memory itself — and no embeddings, no periodic job, no entity extraction.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:e7ebfc85f07bddd6f753d39ec40681e704458a8113b0b1c2f51d2a2ecf8eb634
evidence:
  - path: docs/PRD.md
  - path: src/claude_hermes/modules/memory/domain/tables.py
  - path: src/claude_hermes/modules/memory/application/services/index.py
  - path: src/claude_hermes/modules/memory/infrastructure/page_index.py
  - path: src/claude_hermes/modules/memory/infrastructure/wiki_store.py
  - path: src/claude_hermes/modules/memory/README.md
  - path: src/claude_hermes/modules/turns/infrastructure/tool_handlers/memory.py
  - path: src/claude_hermes/modules/turns/infrastructure/hermes_server.py
  - path: src/claude_hermes/core/db/migrations/versions/0007_wiki_pages.py
  - path: src/claude_hermes/main.py
  - path: docker-compose.yml
relations:
  - contract.state-db
  - component.core-runtime
  - component.learning
  - decision.prompt-cache-discipline
  - decision.multi-user-isolation
  - decision.postgres-and-modular-core
---
# Decision: the wiki is the memory; the database is an index over it

## Context

The wiki outgrew its own front door. Only `wiki/index.md` reaches the prompt —
8000 characters, truncated loudly above that — and everything else is found by
the model guessing a path or grepping a tree. Past that budget a page is
effectively invisible: it exists, it is correct, and nothing points at it. The
obvious fix, once PostgreSQL is already in the stack, is to put memory *in* the
database and give the model a `memory_save` / `memory_get` pair.

That is the fix this decision refuses.

## Decision

**The markdown under each `Workspace.wiki_root` stays the one source of truth.**
The model keeps writing it with native `Write`/`Edit`. PostgreSQL gets
`wiki_pages`, a derived index: one row per `(owner_id, path)`, re-read from disk,
plus one read-only tool, `mcp__hermes__memory_search`
([[contract.state-db]]).

Four principles say why, and each of them would have been broken by a store:

- **P2, the narrow waist.** New capability lands as prompt → skill → an
  in-process MCP tool gated by config → a core change, and every always-on tool
  schema is paid for on every API call. Native `Write`/`Edit` already exist in
  the prefix and cost nothing extra; a memory store needs at least a write tool
  and a read tool, and their schemas are billed on every turn whether memory is
  touched or not. The index buys one tool with two fields.
- **NFR-4, auditable plain text.** Memory content is user-visible plain text by
  requirement. A file the owner can `cat`, diff and edit over SSH stays that; a
  row in a table is readable only through the application that wrote it.
- **NFR-5, `tar ./data` is a complete backup.** The PostgreSQL data lives in the
  `pgdata` **named volume**, outside `./data` — a backup is already three things
  (the dump, the bucket, the volume). Memory moved into that volume would be the
  one thing a `./data` archive silently no longer carries. Because the index is
  *derived*, the archive stays honest: the markdown is in `./data`, and a lost
  table is rebuilt by a boot.
- **P6, declarative memory.** The wiki's shape — namespaces, an index page, a
  log, wikilinks between pages — is what makes it declarative and curatable by
  the lint pass. A row store would have made "memory" whatever the model happened
  to pass to a `save` call, and P4's provenance rules and the curator's editing
  have nothing to bite on.

**The index is a cache, and disk always wins.** Nothing reads a row to decide
what a page says: `touch()` re-reads the file, a path whose file is gone becomes
a delete, `reindex()` converges the table to the tree minus `raw/` (a fetched
source is not memory, from either trigger), and indexing swallows and logs per
page before it swallows per call — a missing, stale or failed row is never an
error for a turn, a lint or a boot, and one note that will not decode does not
leave the rest of a sweep stale. Dropping the table and rebooting is a supported
repair, which is the property that makes the whole trade safe.

## What the index buys

- **Search past the 8000-character index.** FTS over title, summary and body,
  ranked by `ts_rank` with a `ts_headline` snippet — a page outside the prompt
  budget is reachable by what it *says*, not by where the model guesses it is.
- **Link hops for free.** The outgoing wikilinks are already parsed into a
  `TEXT[]`, so one GIN index answers both directions: the pages a hit links to,
  and the pages that link to a hit, up to two hops. The wiki was already a graph;
  nothing new had to be authored to traverse it.
- **Per-owner scope as a WHERE clause.** The wiki is per user
  ([[decision.multi-user-isolation]]), and `owner_id = :me OR shared` is the
  whole visibility rule — no second mechanism, no filtering in the handler.
- **It adds no HTTP surface and no write path.** `GET /api/wiki/*` stays
  owner-only and unchanged; the tool only reads.

## What was deliberately deferred

- **Embeddings / pgvector.** Production runs `postgres:16-alpine`, which ships no
  `pgvector` extension, so a vector column is an image change before it is a
  schema change. FTS over a two-person wiki is not the bottleneck semantic search
  would fix. A vector column arrives with its own migration when the image does.
- **A periodic reindex.** The Stop-hook flush (turn and review fork), the
  post-lint reindex and the boot sweep converge the table; a page edited outside
  a turn — by hand, over SSH — is picked up at the next boot. A timer would buy
  that window back and cost a recurring job whose failure mode is silent.
- **Graphiti / mem0-style entity extraction.** Extracting entities and
  relationships with a model, on a schedule, is the wrong altitude for a
  household deploy: it is spend on the owner's account for traffic nobody
  metered (P3's rule about learning competing with the user, applied to money),
  it invents a second authority over what memory *means* next to the markdown the
  model wrote, and the curator's lint pass — an LLM reading the same pages, in
  band with the wiki's own rules — already does the job that matters at this
  size. Revisit when the wiki has enough pages that a human cannot skim its
  index.

## Costs

- **A second description of every page exists.** It can be stale — between a
  `Write` and the Stop hook, or whenever a flush fails — and `memory_search` will
  quote the older snippet. This is why the review fork flushes too: it is the
  wiki's main rewriter, and without its flush the tool quotes pages it has
  already replaced ([[component.learning]]).
- **One more always-on tool schema**, paid on every call of every private and web
  lane — the P2 tax this decision accepts, held to two fields and one sentence.
- **One deploy-wide session reset** to land it, and one per later flip of
  `memory_search_enabled` ([[decision.prompt-cache-discipline]]).
- **Search is lexical.** A page about "the flat" is not found by "apartment". The
  model is told to read `wiki/index.md` before concluding a page is missing, and
  that instruction is the only thing standing in for recall.
