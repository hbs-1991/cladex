/** Server-sent events tail for a thread, with cursor-based reconnect. */

import type { TurnEvent } from "./types";

export type StreamState = "open" | "reconnecting" | "error";
const RETRY_MS = 1500;

/** Tail a thread's events from `after`. Reconnects from the last persisted id it saw. */
export function subscribeEvents(
  threadId: string,
  after: number,
  onEvent: (ev: TurnEvent) => void,
  onState: (s: StreamState) => void = () => {},
): () => void {
  let cursor = after;
  let source: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  const connect = () => {
    source = new EventSource(`/api/threads/${encodeURIComponent(threadId)}/stream?after=${cursor}`);
    source.onopen = () => onState("open");
    source.addEventListener("turn", (e: MessageEvent) => {
      const ev = JSON.parse(e.data) as TurnEvent;
      if (ev.id !== null && ev.id > cursor) cursor = ev.id;
      onEvent(ev);
    });
    source.onerror = () => {
      source?.close();
      if (stopped) return;
      onState("reconnecting");
      timer = setTimeout(connect, RETRY_MS);
    };
  };
  connect();

  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    source?.close();
  };
}
