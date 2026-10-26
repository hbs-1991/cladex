/** Pure fold of a thread's turn events into assistant-ui messages and thread state. */

import type { ThreadMessageLike } from "@assistant-ui/react";
import type { AttachmentWire, FileWire, Payloads, TurnEvent } from "@/api/types";

export const ASK_TOOL = "ask_user";
export const FILE_TOOL = "send_document";

type Part = ThreadMessageLike["content"] extends string | readonly (infer P)[] ? P : never;
type ToolPart = Extract<Part, { type: "tool-call" }>;

/** How full the model's context window is: the last API call's prompt against the window. */
export type ContextUsage = {
  model: string; window: number; used: number;
  input: number; cacheRead: number; cacheCreation: number;
};

export type Folded = {
  messages: ThreadMessageLike[];
  isRunning: boolean;
  pendingAsk: { qid: string; question: string; options: string[] } | null;
  files: FileWire[];
  title: string | null;
  lastError: string | null;
  context: ContextUsage | null;           // null until a turn on this SDK session reports usage
};

type Draft = {
  turnId: string;
  channel: string;
  meta: Record<string, unknown>;
  parts: Part[];
  buffer: string;            // deltas since the last text.block
  thinking: string;          // deltas since the last thinking block
  status: ThreadMessageLike["status"];
  createdAt: Date;
};

function attachment(a: AttachmentWire) {
  return {
    id: a.file_id ?? a.name,
    type: a.media_type.startsWith("image/") ? ("image" as const) : ("document" as const),
    name: a.name,
    contentType: a.media_type,
    status: { type: "complete" as const },
    content: [],
  };
}

/** The figure a `turn.end` carries, if it carries one: the model whose last call
 *  filled the most, and only with a window to measure it against. */
function contextOf(usage: Payloads["turn.end"]["usage"]): ContextUsage | null {
  let best: ContextUsage | null = null;
  for (const [model, u] of Object.entries(usage ?? {})) {
    if (!u.context_window) continue;
    const used = u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens;
    if (!best || used > best.used) {
      best = { model, window: u.context_window, used,
        input: u.input_tokens, cacheRead: u.cache_read_input_tokens, cacheCreation: u.cache_creation_input_tokens };
    }
  }
  return best;
}

function assistantMessage(d: Draft): ThreadMessageLike {
  const parts = [...d.parts];
  // A response streams its reasoning before its text, so the provisional
  // reasoning part goes ahead of the provisional text part.
  if (d.thinking) parts.push({ type: "reasoning", text: d.thinking } as Part);
  if (d.buffer) parts.push({ type: "text", text: d.buffer } as Part);
  return {
    id: `${d.turnId}:a`,
    role: "assistant",
    content: parts,
    createdAt: d.createdAt,
    status: d.status,
    metadata: { custom: d.meta },
  };
}

/**
 * Structural equality with identity as the fast path: everything a fold reads
 * off an event keeps the event's own identity, so an unchanged message compares
 * equal in a handful of `Object.is` calls. Only the wrappers a fold allocates
 * fresh — the `Date`, the metadata, a tool call's artifact — are walked.
 */
function same(a: unknown, b: unknown, depth = 6): boolean {
  if (Object.is(a, b)) return true;
  if (depth === 0 || typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (a instanceof Date || b instanceof Date) return a instanceof Date && b instanceof Date && a.getTime() === b.getTime();
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a), kb = Object.keys(b);
  return ka.length === kb.length
    && ka.every((k) => same((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k], depth - 1));
}

/**
 * Hand back the message objects of the previous fold wherever this fold says the
 * same thing.
 *
 * A single reasoning delta re-folds the thread from its first event, so every
 * message it has ever held comes back as a new object. assistant-ui caches the
 * conversion of a message by that object's identity (a `WeakMap` keyed on it),
 * so a fresh object means the whole thread is re-converted and re-rendered —
 * every markdown body, every highlighted code block — twenty times a second
 * while the model reasons. That is what made a smooth stream of deltas arrive on
 * screen in visible jumps.
 */
export function reuseMessages(prev: ThreadMessageLike[], next: ThreadMessageLike[]): ThreadMessageLike[] {
  for (let i = 0; i < next.length && i < prev.length; i++) {
    if (same(prev[i], next[i])) next[i] = prev[i]!;
  }
  return next;
}

export function foldEvents(events: TurnEvent[]): Folded {
  const messages: ThreadMessageLike[] = [];
  const files: FileWire[] = [];
  let title: string | null = null;
  let lastError: string | null = null;
  let pendingAsk: Folded["pendingAsk"] = null;
  let context: ContextUsage | null = null;
  let session: string | null = null;           // the SDK session the figure describes
  let draft: Draft | null = null;
  let draftIndex = -1;                         // where the draft's assistant message sits

  const commit = () => {
    if (draft) messages[draftIndex] = assistantMessage(draft);
  };

  for (const e of events) {
    switch (e.type) {
      case "turn.start": {
        const p = e.payload as TurnEvent<"turn.start">["payload"];
        draft = {
          turnId: e.turn_id, channel: p.channel,
          meta: { channel: p.channel, ...(p.job_id !== undefined ? { job_id: p.job_id, run_id: p.run_id } : {}) },
          parts: [], buffer: "", thinking: "", status: { type: "running" }, createdAt: new Date(e.ts),
        };
        draftIndex = messages.length;
        messages.push(assistantMessage(draft));
        break;
      }
      case "user.message": {
        if (!draft) break;
        const p = e.payload as TurnEvent<"user.message">["payload"];
        // The user message goes *before* the assistant draft of the same turn.
        messages.splice(draftIndex, 0, {
          id: `${draft.turnId}:u`, role: "user", createdAt: new Date(e.ts),
          content: p.text ? [{ type: "text", text: p.text }] : [],
          attachments: p.attachments.map(attachment),
        });
        draftIndex += 1;
        break;
      }
      case "text.delta": {
        if (!draft) break;
        draft.buffer += (e.payload as TurnEvent<"text.delta">["payload"]).delta;
        commit();
        break;
      }
      case "text.block": {
        if (!draft) break;
        draft.buffer = "";
        draft.parts.push({ type: "text", text: (e.payload as TurnEvent<"text.block">["payload"]).text } as Part);
        commit();
        break;
      }
      case "thinking.delta": {
        if (!draft) break;
        draft.thinking += (e.payload as TurnEvent<"thinking.delta">["payload"]).delta;
        commit();
        break;
      }
      case "thinking": {
        if (!draft) break;
        draft.thinking = "";
        draft.parts.push({ type: "reasoning", text: (e.payload as TurnEvent<"thinking">["payload"]).text } as Part);
        commit();
        break;
      }
      case "tool.call": {
        if (!draft) break;
        const p = e.payload as TurnEvent<"tool.call">["payload"];
        draft.parts.push({ type: "tool-call", toolCallId: p.call_id, toolName: p.name, args: p.input,
          argsText: JSON.stringify(p.input) } as ToolPart);
        commit();
        break;
      }
      case "tool.result": {
        if (!draft) break;
        const p = e.payload as TurnEvent<"tool.result">["payload"];
        const idx = draft.parts.findIndex((x) => x.type === "tool-call" && (x as ToolPart).toolCallId === p.call_id);
        if (idx >= 0) {
          draft.parts[idx] = { ...(draft.parts[idx] as ToolPart), result: p.output, isError: p.is_error,
            artifact: { duration_ms: p.duration_ms } } as ToolPart;
        }
        commit();
        break;
      }
      case "ask.open": {
        if (!draft) break;
        const p = e.payload as TurnEvent<"ask.open">["payload"];
        pendingAsk = { qid: p.qid, question: p.question, options: p.options };
        draft.parts.push({ type: "tool-call", toolCallId: `ask:${p.qid}`, toolName: ASK_TOOL,
          args: { question: p.question, options: p.options }, argsText: "" } as ToolPart);
        commit();
        break;
      }
      case "ask.answer": {
        if (!draft) break;
        const p = e.payload as TurnEvent<"ask.answer">["payload"];
        if (pendingAsk?.qid === p.qid) pendingAsk = null;
        const idx = draft.parts.findIndex((x) => x.type === "tool-call" && (x as ToolPart).toolCallId === `ask:${p.qid}`);
        if (idx >= 0) draft.parts[idx] = { ...(draft.parts[idx] as ToolPart), result: { answer: p.answer, source: p.source } } as ToolPart;
        commit();
        break;
      }
      case "file": {
        const p = e.payload as TurnEvent<"file">["payload"];
        files.push(p);
        if (!draft) break;
        // Every delivered file, picture included, is one part; `FileCard` decides
        // whether it shows as a preview or as a card. Not an assistant-ui `image`
        // part: the external store only keeps those whose url is a data:, https:
        // or blob: one, and a file of ours is a same-origin path — the part was
        // dropped on the way in and the picture never reached the thread.
        draft.parts.push({ type: "tool-call", toolCallId: `file:${p.file_id}`, toolName: FILE_TOOL, args: p,
          argsText: "", result: "sent" } as ToolPart);
        commit();
        break;
      }
      case "thread.title":
        title = (e.payload as TurnEvent<"thread.title">["payload"]).title;
        break;
      case "turn.end": {
        const p = e.payload as TurnEvent<"turn.end">["payload"];
        // The figure describes one SDK session's window. A new session id (a
        // `/new`, a stale-resume retry) starts from an empty window, so the old
        // figure is dropped before this turn's — if it brought one — replaces it.
        // A turn with no id (cancelled, failed, a cron delivery) says nothing.
        if (p.sdk_session_id) {
          if (session !== null && p.sdk_session_id !== session) context = null;
          session = p.sdk_session_id;
        }
        context = contextOf(p.usage) ?? context;
        if (!draft) break;
        draft.meta = { ...draft.meta, duration_ms: Math.max(0, new Date(e.ts).getTime() - draft.createdAt.getTime()),
          // Which model ACTUALLY answered, never the one the turn asked for —
          // that is what `fallback_from` names when the two differ. The keys are
          // only added when the event carries them, so a turn recorded before
          // this feature keeps the metadata it had and shows no label at all.
          ...(p.model !== undefined
            ? { model: p.model, effort: p.effort ?? null, fallback_from: p.fallback_from ?? null }
            : {}) };
        if (p.status === "ok") draft.status = { type: "complete", reason: "stop" };
        else if (p.status === "cancelled") draft.status = { type: "incomplete", reason: "cancelled" };
        else { draft.status = { type: "incomplete", reason: "error", error: p.error ?? "error" }; lastError = p.error ?? "error"; }
        if (pendingAsk) pendingAsk = null;
        commit();
        draft = null;
        break;
      }
    }
  }
  return { messages, isRunning: draft !== null, pendingAsk, files, title, lastError, context };
}
