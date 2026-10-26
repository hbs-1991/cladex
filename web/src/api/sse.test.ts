import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { subscribeEvents } from "./sse";

class FakeSource {
  static instances: FakeSource[] = [];
  url: string;
  listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(url: string) { this.url = url; FakeSource.instances.push(this); }
  addEventListener(type: string, cb: (e: MessageEvent) => void) {
    (this.listeners[type] ??= []).push(cb);
  }
  emit(type: string, data: unknown, lastEventId = "") {
    for (const cb of this.listeners[type] ?? []) {
      cb({ data: JSON.stringify(data), lastEventId } as MessageEvent);
    }
  }
  close() { this.closed = true; }
}

beforeEach(() => {
  FakeSource.instances = [];
  vi.stubGlobal("EventSource", FakeSource);
  vi.useFakeTimers();
});
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it("connects with the cursor, forwards events, and reconnects from the last id", () => {
  const seen: number[] = [];
  const states: string[] = [];
  const stop = subscribeEvents("web:1:x", 5, (ev) => seen.push(ev.id ?? -1), (s) => states.push(s));
  const first = FakeSource.instances[0]!;
  expect(first.url).toBe("/api/threads/web%3A1%3Ax/stream?after=5");
  first.onopen?.();
  first.emit("turn", { id: 6, type: "turn.start", payload: {} }, "6");
  first.emit("turn", { id: null, type: "text.delta", payload: { delta: "x" } }, "");
  first.onerror?.();
  vi.advanceTimersByTime(1500);
  const second = FakeSource.instances[1]!;
  expect(second.url).toBe("/api/threads/web%3A1%3Ax/stream?after=6");
  expect(seen).toEqual([6, -1]);
  expect(states).toEqual(["open", "reconnecting"]);
  stop();
  expect(second.closed).toBe(true);
});
