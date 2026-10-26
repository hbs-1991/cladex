---
name: wiki-ingest
description: File an external source into the wiki as a page.
---

# Wiki ingest

Turn a source the user handed you — a URL, a file, a long pasted note — into a page in your wiki,
without losing the source itself. Follow this whenever a source is worth remembering; skip it for
anything you read once and throw away.

## When to Use

- The user sends a link or a file and the content is durable knowledge, not a passing question.
- You fetched something you will want to reason from again (a spec, a doc, an article).
- Ask first when it is ambiguous: "worth filing in the wiki?" A one-line question is cheaper than
  a wrong page.

## Prerequisites

- `wiki/` exists next to you, with `index.md`, `log.md` and the namespaces `user/ projects/
  topics/ env/ raw/`.
- Tools: `WebFetch` (or `Read` for a file), `Write`, `Edit`, `Glob`, `Grep`.

## Procedure

1. **Fetch.** `WebFetch` the URL, or `Read` the file.
2. **Store the source, verbatim.** Write it to `wiki/raw/<slug>.md`, where `<slug>` is
   lowercase-hyphenated. `raw/` is immutable: if the file already exists, pick a new slug — never
   overwrite one, and never `Edit` one.
3. **Find where it belongs.** `Grep` the wiki for the entities it names. An existing page you can
   deepen beats a new page that duplicates it.
4. **Write the page** under `topics/` (or `projects/`, `env/` if that is what it is about):

   ```markdown
   ---
   title: <what this is>
   summary: <one line — this is what lands in the index>
   tags: [<topic>, ...]
   updated: <YYYY-MM-DD>
   source: raw/<slug>.md
   ---
   <your distillation — the claims that matter, not a retelling>

   See also [[topics/related-page]].
   ```

5. **Link it in.** Add a `[[link]]` to the new page from every page that should point at it. A page
   nothing links to is an orphan, and lint will flag it.
6. **Update `wiki/index.md`** — one line: `- topics/<slug>.md — <summary>`.
7. **Append to `wiki/log.md`** — one line: `- <date> ingest: <source> → topics/<slug>.md`.

## Pitfalls

- **Do not skip step 2.** A page whose source is gone cannot be re-checked, and lint's staleness
  check dies with it.
- **Do not retell the source.** Distil it: what is now true, what changed your mind, what you will
  need. The full text is already in `raw/`.
- **Do not create a near-duplicate page.** Grep first (step 3).
- **Do not stop halfway.** A page without an index line is invisible to your next session — and a
  hook will block the turn until you fix it.

## Verification

`Read wiki/index.md` and confirm your page is listed. `Grep` for `[[<your page>]]` and confirm at
least one other page points at it.
