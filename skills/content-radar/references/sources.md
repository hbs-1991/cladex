# Source lanes

Six lanes, swept in this order. The order matters: the first two are primary
sources, so a candidate found there needs no corroboration, while a candidate that
only appears in lanes 4–6 must be traced back before it can enter a brief.

Every lane gets at least one query per run. A brief assembled from one loud lane is
a brief about that lane, not about the field.

## 1. Vendor primary sources

The changelogs and engineering blogs of the labs and platforms, read directly:

- Anthropic — news, engineering blog, Claude Code and Agent SDK changelogs
- OpenAI — releases and platform changelog
- Google DeepMind, Meta AI, Mistral — release notes
- The agent-platform layer: LangChain/LangGraph, LlamaIndex, Vercel AI SDK,
  Cursor, Cline, OpenHands

Query shape: `<vendor> changelog <this month>` / `<vendor> release notes`.
Why first: a version number and a dated changelog entry are checkable facts, which
is the only kind of claim worth building a post on.

## 2. Repositories and releases

New or newly-relevant repos in agent tooling, MCP servers, evaluation harnesses,
orchestration frameworks. GitHub trending for the day, plus release feeds of
projects already in use.

Query shape: `github trending <language> agents`, `<project> release <version>`.
What makes one post-worthy: it solves a problem the user has actually had, or its
design contains a decision worth arguing with. A star count is not a reason.

## 3. Papers and technical reports

arXiv cs.AI / cs.CL / cs.SE from the window, plus lab technical reports. Only the
ones with a practical consequence: agent architectures, tool-use and context
engineering, evaluation methodology, cost/latency work.

Query shape: `arxiv agent <topic> <month year>`.
Rule: read the abstract and the limitations section. A paper's headline number is
usually its best case; the limitations are where the postable insight lives.

## 4. Practitioner writing

Engineering posts by people running agents in production — the "we ran this for six
months and here is what broke" genre. Highest signal for the user's own voice,
because it is the register he writes in.

Query shape: `building agents in production lessons <month year>`,
`agentic workflow postmortem`.

## 5. Aggregators

Hacker News front page and its `/newest` for AI-agent terms, r/LocalLLaMA,
r/MachineLearning, the AI-tooling newsletters. Used as a **discovery** surface only.
Anything found here must be traced to its primary source before it enters a brief —
that trace is step 3 of the procedure, and it is not optional.

## 6. The user's own surface area

What is happening in the areas he already builds in, which is where his standing to
comment is strongest:

- Claude Agent SDK / Claude Code — hooks, skills, subagents, MCP
- Telegram-bot and personal-agent patterns
- Self-improving / memory-carrying agents, context engineering
- Local-SEO and Google Business Profile tooling (the startup's domain)
- Content automation pipelines

Query shape: `<one of the above> <this month>`.
A find here outranks a louder story elsewhere: a post where the author has built
the thing beats a post about the biggest news of the day.

## Query hygiene

- Put the current month and year in the query. Without them search happily returns
  a 2024 blog post as today's news.
- Two or three narrow queries beat one broad one. `agentic development` returns
  marketing; `MCP server for <specific job> released` returns artifacts.
- Search in English. The Russian-language surface for this field is derivative and
  lags by days.
- If a lane returns nothing new, say so in the brief. An empty lane is information;
  a lane quietly skipped is a hole in the sweep nobody can see.
