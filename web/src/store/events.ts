/** Store slice: per-thread turn events, their SSE subscription and the memoised fold. */

import type { StateCreator } from "zustand";
import { api } from "@/api/client";
import { subscribeEvents, type StreamState } from "@/api/sse";
import type { TurnEvent } from "@/api/types";
import { foldEvents, reuseMessages, type Folded } from "@/runtime/fold";

export type EventsSlice = {
  byThread: Record<string, TurnEvent[]>;
  cursor: Record<string, number>;
  stream: Record<string, StreamState>;
  /** True while a thread's history is on the wire. The thread view reads it
   *  through the runtime's `isLoading` and shows a skeleton instead of the
   *  welcome block, which is the wrong thing to show a lane that has history. */
  loading: Record<string, boolean>;
  open: (id: string) => Promise<void>;
  close: (id: string) => void;
  push: (id: string, ev: TurnEvent) => void;
  /** `live` marks events arriving on the stream rather than replayed history. It
   *  only decides whether a cleared `thread.model` is worth a fresh read of the
   *  thread — replaying a year of history must not put one request per event on
   *  the wire. */
  pushMany: (id: string, evs: TurnEvent[], live?: boolean) => void;
  folded: (id: string) => Folded;
};

/** What the slice reaches for in the sibling slices it is composed with. Optional
 *  because a test may create this slice on its own. */
type Neighbours = {
  patchLocal?: (id: string, p: object) => void;
  patchThreadModel?: (id: string, pair: { model: string | null; effort: string | null }) => void;
  loadThreadModel?: (id: string) => Promise<void>;
};

/**
 * One slot per thread that is open, holding the live subscription.
 *
 * `cancelled` is the whole point of the object. `open` has to await the history
 * before it can subscribe from the right cursor, and `close` can land inside that
 * await — React's StrictMode does exactly that on every mount (effect, cleanup,
 * effect), and so does a person clicking through the sidebar faster than a long
 * thread loads. The old code reserved the slot with a no-op and deleted it on
 * close, so the second `open` started over while the first was still in flight and
 * then had its `EventSource` overwritten by the second's — orphaned, never closed,
 * and still pushing. Deltas carry no id, so `push` cannot dedupe them and the text
 * arrived twice; worse, every leaked stream holds one of the browser's six
 * connections per origin until the tab is reloaded, which is what made the whole
 * app crawl after a handful of thread visits.
 *
 * So the slot outlives the await, `close` marks it rather than dropping it, and a
 * load that comes back into a cancelled slot subscribes to nothing.
 */
type Slot = { cancelled: boolean; stop: (() => void) | null };

const slots = new Map<string, Slot>();
/**
 * History requests in flight, shared by thread id.
 *
 * A cancelled `open` cannot un-send its request, and the reply is exactly what the
 * `open` that replaced it is about to ask for. One entry per thread turns
 * StrictMode's double mount — and a fast remount — back into a single download of
 * what can be megabytes of turn history.
 */
const loads = new Map<string, Promise<TurnEvent[]>>();
const foldCache = new Map<string, { events: TurnEvent[]; folded: Folded }>();
const EMPTY = foldEvents([]);

function history(id: string, after: number): Promise<TurnEvent[]> {
  const shared = loads.get(id);
  if (shared) return shared;
  const load = api.threads.events(id, after).then((r) => r.events);
  loads.set(id, load);
  // The entry is cleared on a branch of its own rather than by the caller, because
  // there may be several callers or — if every one of them was cancelled — none.
  // `catch` first so that branch cannot become an unhandled rejection of its own;
  // the failure itself still reaches whoever is awaiting `load`.
  void load.catch(() => {}).then(() => { if (loads.get(id) === load) loads.delete(id); });
  return load;
}

export const createEventsSlice: StateCreator<EventsSlice & Neighbours, [], [], EventsSlice> = (set, get) => ({
  byThread: {}, cursor: {}, stream: {}, loading: {},
  async open(id) {
    if (slots.has(id)) return;
    const slot: Slot = { cancelled: false, stop: null };
    slots.set(id, slot);
    set((st) => ({ loading: { ...st.loading, [id]: true } }));
    try {
      const events = await history(id, get().cursor[id] ?? 0);
      if (slot.cancelled) return;
      get().pushMany(id, events);
    } catch {
      // A thread that will not load (404 on a lane with no row yet, a dropped
      // network) must not leave the caller with an unhandled rejection and a
      // reservation nothing can clear: record it, release the slot so a remount
      // can retry, and let ConnectionBar say so.
      if (slots.get(id) === slot) slots.delete(id);
      if (!slot.cancelled) set((st) => ({ stream: { ...st.stream, [id]: "error" } }));
      return;
    } finally {
      // Only the slot that is still the current one clears the flag. A cancelled
      // `open` is followed by the one that replaced it, which has already set the
      // flag again — clearing it here would flash the welcome block through the
      // gap and then replace it with the skeleton.
      if (!slot.cancelled) set((st) => ({ loading: { ...st.loading, [id]: false } }));
    }
    if (slot.cancelled) return;          // closed while loading: open no stream
    slot.stop = subscribeEvents(id, get().cursor[id] ?? 0,
      (e) => get().push(id, e),
      (s) => set((st) => ({ stream: { ...st.stream, [id]: s } })));
  },
  close(id) {
    const slot = slots.get(id);
    if (!slot) return;
    slot.cancelled = true;               // a load still in flight must not subscribe
    slot.stop?.();
    slots.delete(id);
  },
  push(id, e) {
    get().pushMany(id, [e], true);
  },
  pushMany(id, evs, live = false) {
    if (evs.length === 0) return;
    set((s) => {
      const list = s.byThread[id] ?? [];
      let cursor = s.cursor[id] ?? 0;
      const added: TurnEvent[] = [];
      // A batch indexes what is already there once, instead of scanning it per
      // event: loading a thread's history one `push` at a time is quadratic, and
      // a long lane spent over a tenth of a second in this function alone. A
      // single event — every live delta — keeps the plain scan, which costs the
      // same walk without building an index for one lookup.
      const seen = evs.length > 1 ? new Set<number>() : null;
      if (seen) for (const x of list) if (x.id !== null) seen.add(x.id);
      for (const e of evs) {
        if (e.id !== null) {
          // Replayed after a reconnect.
          if (seen ? seen.has(e.id) : list.some((x) => x.id === e.id)) continue;
          seen?.add(e.id);
          cursor = Math.max(cursor, e.id);
        }
        added.push(e);
      }
      if (added.length === 0) return s;
      return {
        byThread: { ...s.byThread, [id]: [...list, ...added] },
        cursor: { ...s.cursor, [id]: cursor },
      };
    });
    // The sidebar patches follow the store write, in arrival order. A duplicate
    // that `set` dropped is replayed history, and re-applying its patch says the
    // same thing — cheaper than threading the survivors back out of `set`.
    for (const e of evs) {
      if (e.type === "thread.title") get().patchLocal?.(id, { title: (e.payload as { title: string }).title });
      if (e.type === "turn.start") get().patchLocal?.(id, { running: true, last_event_at: e.ts });
      if (e.type === "turn.end") get().patchLocal?.(id, { running: false });
      if (e.type === "thread.model") {
        // The event carries the EXPLICIT pair and nothing else, exactly like
        // `thread.title`. A named model is the effective one, so patching the pair
        // is the whole update; a cleared one (`model: null`) falls back to
        // something only the server can resolve — the session's pair, the owner's
        // default, the deploy default — so a LIVE clear is worth one fresh read.
        // Replayed history never is: the read that follows it says the same thing.
        const pair = e.payload as { model: string | null; effort: string | null };
        get().patchThreadModel?.(id, pair);
        if (live && pair.model === null) void get().loadThreadModel?.(id);
      }
    }
  },
  folded(id) {
    const events = get().byThread[id];
    if (!events) return EMPTY;
    const hit = foldCache.get(id);
    if (hit && hit.events === events) return hit.folded;
    const folded = foldEvents(events);
    // Keep the identity of every message the new fold did not change: it is what
    // stops a reasoning delta from re-rendering the whole thread (fold.ts).
    if (hit) folded.messages = reuseMessages(hit.folded.messages, folded.messages);
    foldCache.set(id, { events, folded });
    return folded;
  },
});
