/** Turn-event fixtures shared by the runtime and store tests. */

import type { EventType, Payloads, TurnEvent } from "@/api/types";

let nextId = 1;
export function ev<T extends EventType>(turn: string, type: T, payload: Payloads[T], persisted = true): TurnEvent<T> {
  return {
    id: persisted ? nextId++ : null, session_key: "web:1:x", turn_id: turn, seq: nextId, ts: "2026-07-12T10:00:00+00:00",
    type, payload,
  };
}
export const END_OK: Payloads["turn.end"] = { status: "ok", cost_usd: 0.01, sdk_session_id: "s", error: null };

export const plainTurn = () => [
  ev("t1", "turn.start", { channel: "web", user_id: 1 }),
  ev("t1", "user.message", { text: "hi", attachments: [] }),
  ev("t1", "text.delta", { delta: "Hel" }, false),
  ev("t1", "text.delta", { delta: "lo" }, false),
  ev("t1", "text.block", { text: "Hello" }),
  ev("t1", "tool.call", { call_id: "c1", name: "Read", input: { file_path: "a.md" } }),
  ev("t1", "tool.result", { call_id: "c1", output: "body", is_error: false, duration_ms: 12 }),
  ev("t1", "text.block", { text: "Done." }),
  ev("t1", "turn.end", END_OK),
];
