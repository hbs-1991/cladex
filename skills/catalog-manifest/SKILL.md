---
name: catalog-manifest
description: Use when the owner asks to add a new MCP server or plugin that is not yet in the catalog — i.e. before ext_manage(action="draft"). Explains the manifest shape and the validation rules, and how to self-check a draft with scripts/validate_manifest.py so a broken manifest never reaches the owner's approval screen.
---

# Writing a catalog manifest

A catalog entry is a small YAML file. An MCP server is either a remote endpoint or a
local command; a plugin is a git repo at a pinned ref. The owner will see the exact
`command`/`args` (or `url`, or `repo`/`ref`/`subdir`) and must approve it — so get it
right and show them something clean.

## The rules (enforced; a draft that breaks one is refused)

- `manifest_version: 1`. Unknown top-level keys are an error, not a warning.
- MCP `transport` is one of `stdio | http | sse`.
- `stdio`: `command` must be `npx`, `uvx`, or `python`. No shells. `args` is a **list of
  strings**, never one shell string — no pipes, no `&&`, no substitution.
- `http`/`sse`: `url` must be `https://`.
- Plugin `repo` must be an `https://` URL. It is handed to git, where `ext::…` or a
  leading `-` would be a command rather than an address.
- Plugin `ref` must be a release tag (`v1.2.3`) or a full 40-char commit sha — never a
  branch like `main` (a branch moves after the owner reviewed it). Both work; a sha is
  the stronger pin.
- Plugin `subdir` (optional) — the directory **inside** the repo that is the plugin.
  Plain relative path only (`plugins/plugin-dev`); no `..`, no leading `/`, no drive
  letter. Omit it when the repo root is the plugin.
- Every `${VAR}` you reference must be declared under `auth`.
- `scopes` ⊆ `{turn, cron}`.

## Is the repo root the plugin, or is it a marketplace?

Check before you draft — this is the mistake that wastes the owner's time:

- **The repo root is the plugin** when `.claude-plugin/plugin.json` sits at the top. Omit
  `subdir`.
- **It is a marketplace** when the root has `.claude-plugin/marketplace.json` and the
  plugins live under `plugins/<name>/` — e.g. `anthropics/claude-plugins-official`. Then
  `subdir: plugins/<name>` is **required**; without it the install fails, because the root
  is not a plugin.

## Cost — ask before you add

A plugin's skills, agents and commands go into the system prompt and are paid for on
every API call for as long as it is installed. Say that plainly to the owner if the
plugin is large or is a toolkit they may not actually use, and prefer `scopes: [turn]`
over adding `cron`.

## Self-check BEFORE you draft

Write the manifest to a temp file and run the validator — the same code the catalog runs:

    python scripts/validate_manifest.py /tmp/manifest.yaml

`PASS` prints the executable surface the owner will read. `FAIL: <reason>` tells you
exactly what to fix. Only once it passes, file it with `ext_manage(action="draft", …)`
and tell the owner to review and `/approve <name>`.

## Example (remote MCP server)

    manifest_version: 1
    name: example
    label: Example
    description: What this server is for.
    version: 1.0.0
    transport: http
    url: https://mcp.example.com/mcp
    scopes: [turn]

## Example (a plugin inside a marketplace repo)

    manifest_version: 1
    name: plugin-dev
    label: Plugin Developer Toolkit
    description: Skills for authoring Claude Code plugins.
    version: 1.0.0
    repo: https://github.com/anthropics/claude-plugins-official
    ref: b5eddebc6444d73108941ee698f25fa8759b8710
    subdir: plugins/plugin-dev
    scopes: [turn]
