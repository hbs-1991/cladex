# Security policy

CladeX runs an autonomous agent with shell access inside a container, stores
encrypted third-party credentials and is reachable from the internet, so security
reports matter a lot to us.

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately through GitHub:
**Security → Report a vulnerability** on this repository
(<https://github.com/hbs-1991/cladex/security/advisories/new>).

Include what an attacker can do, the steps to reproduce, and the commit or
version you tested. We aim to acknowledge a report within a few days and will
keep you updated until a fix is released. Credit is given in the advisory unless
you prefer otherwise.

## Scope

In scope: the backend (`src/`), the web UI (`web/`), the Docker setup and the
bundled catalog and skills. The security model — the read-only catalog mount as
the hard boundary, gateway-intercepted privileged actions, per-user isolation —
is described in `docs/llm-wiki/decisions/` and `docs/llm-wiki/quality/invariants.md`;
a way around any of those is a vulnerability.

Out of scope: vulnerabilities in third-party MCP servers or plugins listed in the
catalog (report them upstream), and issues that need an already-compromised host.
