---
id: contract.catalog-manifest
kind: contract
status: current
summary: Extension manifest format and the paranoid validation rules in catalog.parse.
verification:
  base_commit: 12a34600230e865258f800d867d8b78c87ac9671
  evidence_fingerprint: sha256:4b440c08bf44554b31e6b7fa5ce7719eb3ba46a11b61bdfb64e245fd9f9a6181
evidence:
  - path: src/claude_hermes/modules/extensions/domain/manifest.py
  - path: src/claude_hermes/modules/extensions/infrastructure/catalog_repository.py
  - path: catalog/mcp/fetch.yaml
  - path: catalog/mcp/filesystem.yaml
  - path: scripts/validate_manifest.py
relations:
  - component.extensions
  - decision.readonly-catalog-boundary
---
# Contract: catalog manifest

One YAML file per extension under `catalog/mcp/` or `catalog/plugins/` (repo,
reviewed, read-only in the container) or `user-catalog/...` (chat-approved,
state). `catalog.parse` (modules/extensions/domain/manifest.py) is the single validator used by the
loader, drafts and the approval paths; `scripts/validate_manifest.py` wraps it
for humans and the drafting agent.

## Common fields (both kinds)

`manifest_version` (== 1), `name`, `label`, `description`, `version`,
`scopes` (subset of `{turn, cron}`, default `[turn]`), optional `auth` list.
An auth entry has a `kind`, default `secret` (`name` + `prompt` required; also
`url`, `header`, `format`) or `oauth` (`prompt` forbidden; `header` must be
`Authorization` and `format` exactly `Bearer ${NAME}`; refused on `stdio`) —
the token is obtained by signing in from the web app and mirrored into
`secrets.env` under `${NAME}` ([[component.extensions]]). Unknown top-level
keys are an error.

## MCP entries

`transport` ∈ `{stdio, http, sse}`.

- stdio: `command` ∈ allowlist `{npx, uvx, python}` — never a shell; `args`
  must be a list of strings, never a shell string; `env` a mapping.
- http/sse: `url` must start with `https://`.

## Plugin entries

- `repo` must start with `https://` (it becomes git argv; `ext::`, leading
  `-`, `http://`, `file://`, `git@`, bare paths refused).
- `ref` must be immutable: full 40-hex commit sha or a release tag
  (`v?\d…`); moving refs (`main`, `master`, `head`, `trunk`, `develop`,
  `latest`) and branch names refused.
- `subdir` optional; plain relative path, each segment starting alphanumeric
  (kills `..`, `-flag`, `.hidden`, absolute paths, drive letters, UNC).

## Cross-cutting rules

- Every `${VAR}` in `url`, `args`, `env` values or `auth.format` must be
  declared as an `auth[].name`.
- `name` matches `^[a-z0-9][a-z0-9_-]*$` — it is the registry primary key
  and the plugin directory name (an rmtree target).
- Repo catalog wins over user-catalog on name collision (`_merge`,
  modules/extensions/infrastructure/catalog_repository.py) — a chat-authored entry can never shadow a reviewed one.

What the validator cannot judge — whether the package itself is trustworthy —
is exactly what the human approval step is for (README, "Adding a catalog
entry").
