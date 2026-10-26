import { describe, expect, it } from "vitest";
import type { Payloads } from "@/api/types";
import { ASK_TOOL, FILE_TOOL, foldEvents, reuseMessages } from "./fold";
import { END_OK, ev, plainTurn } from "./fixtures";

describe("foldEvents", () => {
  it("builds one user and one assistant message per turn with parts in order", () => {
    const { messages, isRunning } = foldEvents(plainTurn());
    expect(isRunning).toBe(false);
    expect(messages.map((m) => m.role)).toEqual(["user", "assistant"]);
    expect(messages[0]!.id).toBe("t1:u");
    expect(messages[0]!.content).toEqual([{ type: "text", text: "hi" }]);
    const parts = messages[1]!.content as readonly { type: string }[];
    expect(parts.map((p) => p.type)).toEqual(["text", "tool-call", "text"]);
    expect(parts[0]).toEqual({ type: "text", text: "Hello" });   // the block replaced the deltas
    expect(parts[1]).toMatchObject({ type: "tool-call", toolCallId: "c1", toolName: "Read",
      args: { file_path: "a.md" }, result: "body", isError: false });
    expect(messages[1]!.status).toEqual({ type: "complete", reason: "stop" });
  });

  it("streams deltas into a provisional text part while running", () => {
    const events = plainTurn().slice(0, 4);           // up to the second delta
    const { messages, isRunning } = foldEvents(events);
    expect(isRunning).toBe(true);
    expect(messages[1]!.content).toEqual([{ type: "text", text: "Hello" }]);
    expect(messages[1]!.status).toEqual({ type: "running" });
  });

  it("keeps a running tool call result-less", () => {
    const events = plainTurn().slice(0, 6);           // through tool.call
    const parts = foldEvents(events).messages[1]!.content as readonly { type: string; result?: unknown }[];
    expect(parts[1]).toMatchObject({ type: "tool-call", toolName: "Read" });
    expect(parts[1]!.result).toBeUndefined();
  });

  it("renders ask.open as a standalone human tool and closes it on ask.answer", () => {
    const open = [
      ev("t2", "turn.start", { channel: "web", user_id: 1 }),
      ev("t2", "user.message", { text: "q", attachments: [] }),
      ev("t2", "ask.open", { qid: "q1", question: "Which?", options: ["A", "B"] }),
    ];
    let out = foldEvents(open);
    expect(out.pendingAsk).toEqual({ qid: "q1", question: "Which?", options: ["A", "B"] });
    const part = (out.messages[1]!.content as readonly { toolCallId: string; toolName: string; args: unknown }[])[0]!;
    expect(part).toMatchObject({ toolCallId: "ask:q1", toolName: ASK_TOOL, args: { question: "Which?", options: ["A", "B"] } });
    out = foldEvents([...open, ev("t2", "ask.answer", { qid: "q1", answer: "B", source: "user" })]);
    expect(out.pendingAsk).toBeNull();
    expect((out.messages[1]!.content as readonly { result?: unknown }[])[0]!.result).toEqual({ answer: "B", source: "user" });
  });

  it("renders file events as standalone send_document parts and collects files", () => {
    const file = { file_id: "f1", name: "r.md", media_type: "text/markdown", size: 4, url: "/api/files/f1", telegram_sent: false };
    const out = foldEvents([
      ev("t3", "turn.start", { channel: "web", user_id: 1 }),
      ev("t3", "user.message", { text: "make", attachments: [] }),
      ev("t3", "file", file),
      ev("t3", "turn.end", END_OK),
    ]);
    expect(out.files).toEqual([file]);
    expect((out.messages[1]!.content as readonly { toolName: string; args: unknown }[])[0]).toMatchObject({ toolName: FILE_TOOL, args: file });
  });

  it("hands a generated picture to the same part as any other file", () => {
    const png = { file_id: "f2", name: "cat.png", media_type: "image/png", size: 9, url: "/api/files/f2", telegram_sent: false };
    const out = foldEvents([
      ev("t3", "turn.start", { channel: "web", user_id: 1 }),
      ev("t3", "file", png),
    ]);
    expect(out.files).toEqual([png]);
    // An assistant-ui `image` part would be dropped here: its url is same-origin,
    // and the external store keeps only data:, https: and blob: ones.
    expect(out.messages[0]!.content[0]).toMatchObject({ toolName: FILE_TOOL, args: png });
  });

  it("marks error and cancelled turns as incomplete", () => {
    const base = [ev("t4", "turn.start", { channel: "web", user_id: 1 }), ev("t4", "user.message", { text: "x", attachments: [] })];
    const err = foldEvents([...base, ev("t4", "turn.end", { ...END_OK, status: "error", error: "api down" })]);
    expect(err.messages[1]!.status).toEqual({ type: "incomplete", reason: "error", error: "api down" });
    expect(err.lastError).toBe("api down");
    const cancelled = foldEvents([...base, ev("t4", "text.delta", { delta: "par" }, false),
      ev("t4", "turn.end", { ...END_OK, status: "cancelled" })]);
    expect(cancelled.messages[1]!.status).toEqual({ type: "incomplete", reason: "cancelled" });
    expect(cancelled.messages[1]!.content).toEqual([{ type: "text", text: "par" }]);   // partial text stays
  });

  it("streams thinking into a provisional reasoning part that the block then replaces", () => {
    const start = [ev("t7", "turn.start", { channel: "web", user_id: 1 }), ev("t7", "user.message", { text: "hi", attachments: [] })];
    const live = foldEvents([...start,
      ev("t7", "thinking.delta", { delta: "Need to " }, false), ev("t7", "thinking.delta", { delta: "read a.md" }, false),
      ev("t7", "text.delta", { delta: "Let me" }, false)]);
    expect(live.messages[1]!.content).toEqual([
      { type: "reasoning", text: "Need to read a.md" }, { type: "text", text: "Let me" }]);   // reasoning first
    const settled = foldEvents([...start,
      ev("t7", "thinking.delta", { delta: "Need to read a.md" }, false),
      ev("t7", "thinking", { text: "Need to read a.md" }),
      ev("t7", "text.block", { text: "Let me look." }),
      { ...ev("t7", "turn.end", END_OK), ts: "2026-07-12T10:11:01+00:00" }]);
    const parts = settled.messages[1]!.content as readonly { type: string; text?: string }[];
    expect(parts.map((p) => p.type)).toEqual(["reasoning", "text"]);   // no duplicate from the delta buffer
    expect(parts[0]!.text).toBe("Need to read a.md");
    expect(settled.messages[1]!.metadata).toMatchObject({ custom: { duration_ms: 661_000 } });
  });

  it("records which model actually answered, and nothing at all for a turn from before it", () => {
    const start = [ev("t8", "turn.start", { channel: "web", user_id: 1, model: "claude-opus-5", effort: "high" }),
      ev("t8", "user.message", { text: "hi", attachments: [] })];
    // A fallback: the turn asked for Opus 5.5 and Opus 5 answered — the label
    // must name the one that ran, which is what `turn.end` carries.
    const ran = foldEvents([...start, ev("t8", "turn.end",
      { ...END_OK, model: "claude-opus-5", effort: "high", fallback_from: "claude-opus-5-5" })]);
    expect(ran.messages[1]!.metadata).toMatchObject({ custom: { model: "claude-opus-5", effort: "high", fallback_from: "claude-opus-5-5" } });

    const auto = foldEvents([...start, ev("t8", "turn.end", { ...END_OK, model: "claude-sonnet-5", effort: null })]);
    expect(auto.messages[1]!.metadata).toMatchObject({ custom: { model: "claude-sonnet-5", effort: null, fallback_from: null } });

    // A turn recorded before this feature carries no such keys, and gets none.
    const old = foldEvents([...start, ev("t8", "turn.end", END_OK)]);
    expect((old.messages[1]!.metadata as { custom: Record<string, unknown> }).custom).not.toHaveProperty("model");
  });

  it("is a pure function of the event list: replay equals live", () => {
    const events = plainTurn();
    const live = events.reduce((acc, e) => { foldEvents([...acc, e]); return [...acc, e]; }, [] as typeof events);
    expect(foldEvents(live)).toEqual(foldEvents(events));
  });

  it("carries user attachments, thread titles and cron turns", () => {
    const out = foldEvents([
      ev("t5", "turn.start", { channel: "cron", user_id: 0, job_id: 3, run_id: "r1" }),
      ev("t5", "text.block", { text: "report" }),
      ev("t5", "turn.end", END_OK),
      ev("", "thread.title", { title: "Daily brief" }),
      ev("t6", "turn.start", { channel: "web", user_id: 1 }),
      ev("t6", "user.message", { text: "2", attachments: [{ file_id: "f9", name: "a.png", media_type: "image/png", size: 3, url: "/api/files/f9" }] }),
    ]);
    expect(out.title).toBe("Daily brief");
    expect(out.messages[0]!.role).toBe("assistant");                           // a cron report has no user message
    expect(out.messages[0]!.metadata).toMatchObject({ custom: { channel: "cron", job_id: 3, run_id: "r1" } });
    expect(out.messages[1]!.attachments).toEqual([{ id: "f9", type: "image", name: "a.png", contentType: "image/png",
      status: { type: "complete" }, content: [] }]);
  });
});

it("keeps the identity of every message a new delta did not change", () => {
  const before = [
    ev("t1", "turn.start", { channel: "web", user_id: 1 }),
    ev("t1", "user.message", { text: "hi", attachments: [] }),
    ev("t1", "tool.call", { call_id: "c1", name: "Read", input: { file_path: "a.md" } }),
    ev("t1", "tool.result", { call_id: "c1", output: "x", is_error: false, duration_ms: 5 }),
    ev("t1", "turn.end", END_OK),
    ev("t2", "turn.start", { channel: "web", user_id: 1 }),
    ev("t2", "user.message", { text: "and now?", attachments: [] }),
    ev("t2", "thinking.delta", { delta: "Let me " }),
  ];
  const first = foldEvents(before);
  const second = reuseMessages(first.messages, foldEvents([...before, ev("t2", "thinking.delta", { delta: "think." })]).messages);
  // The finished turn and the question that opened this one are untouched; only
  // the message the delta lands in is a new object.
  expect(second[0]).toBe(first.messages[0]);
  expect(second[1]).toBe(first.messages[1]);
  expect(second[2]).toBe(first.messages[2]);
  expect(second[3]).not.toBe(first.messages[3]);
  expect((second[3]!.content as readonly { text: string }[])[0]!.text).toBe("Let me think.");
});

describe("context window usage", () => {
  const usage = { m: { context_window: 200_000, input_tokens: 12, cache_read_input_tokens: 1300,
    cache_creation_input_tokens: 50, output_tokens: 40 } };
  const turn = (id: string, end: Partial<Payloads["turn.end"]>) => [
    ev(id, "turn.start", { channel: "web", user_id: 1 }),
    ev(id, "user.message", { text: "q", attachments: [] }),
    ev(id, "turn.end", { ...END_OK, ...end }),
  ];

  it("is unknown until a turn reports usage, then the last call's prompt against the window", () => {
    expect(foldEvents(turn("t1", {})).context).toBeNull();               // END_OK carries no usage
    expect(foldEvents(turn("t1", { usage })).context).toEqual({
      model: "m", window: 200_000, used: 1362, input: 12, cacheRead: 1300, cacheCreation: 50,
    });
  });

  it("keeps the figure through a failed turn and a cron delivery, which report none", () => {
    const out = foldEvents([
      ...turn("t1", { usage }),
      ...turn("t2", { status: "error", error: "x", sdk_session_id: null, cost_usd: null }),
      ev("t3", "turn.start", { channel: "cron", user_id: 0, job_id: 1, run_id: "r" }),
      ev("t3", "turn.end", { ...END_OK, sdk_session_id: null, usage: null }),
    ]);
    expect(out.context).toMatchObject({ used: 1362 });
  });

  it("forgets it when the SDK session changes", () => {
    expect(foldEvents([...turn("t1", { usage }), ...turn("t2", { sdk_session_id: "s2" })]).context).toBeNull();
    const fresh = { m: { ...usage.m, input_tokens: 1, cache_read_input_tokens: 2, cache_creation_input_tokens: 3 } };
    expect(foldEvents([...turn("t1", { usage }), ...turn("t2", { sdk_session_id: "s2", usage: fresh })]).context)
      .toMatchObject({ used: 6 });
  });

  it("needs a window to be a figure", () => {
    expect(foldEvents(turn("t1", { usage: { m: { ...usage.m, context_window: null } } })).context).toBeNull();
  });
});
