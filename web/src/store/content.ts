/** Store slice: the calendar — the owner's content plan, the routines' fires and
 *  runs for the range on screen, and the two routines the plan leans on — the
 *  drafter that writes a post for an approved theme, and the publisher.
 *
 *  Two module endpoints feed it and nothing on the server joins them: the
 *  scheduler answers `GET /api/routines/calendar` for anyone signed in (scoped to
 *  their own routines), and the content module answers `GET /api/content` for the
 *  deploy owner alone. So the content calls are made only when `me.role` is
 *  `owner`, and whether the content plan exists at all is learned once, from
 *  `GET /api/content/publisher`: the router is not mounted when `content_enabled`
 *  is off, and its 404 turns the calendar into a calendar of routines.
 *
 *  Every mutation re-reads the range rather than patching rows in place. An edit
 *  can move an item between the dated list and the backlog, or out of the range
 *  entirely, and the server's answer for the range is the only one that is
 *  certainly right. A read that was overtaken by a newer one (the reader clicked
 *  "next" twice) is dropped when it lands.
 *
 *  The drafter is read next to the publisher but never decides anything: an
 *  older server without the drafter route still has a working plan, so a failed
 *  drafter read leaves `drafter` at `null` and nothing else. */

import type { StateCreator } from "zustand";
import { ApiError, api } from "@/api/client";
import type {
  ApproveThemesWire, CalendarWire, ContentInput, ContentItemWire, ContentRetry, Me, PublisherWire,
} from "@/api/types";

export type CalendarRange = { from: string; to: string };

export type ContentSlice = {
  calRange: CalendarRange | null;
  calItems: ContentItemWire[];
  calBacklog: ContentItemWire[];
  calRoutines: CalendarWire | null;
  calStatus: "idle" | "loading" | "ready" | "error";
  /** `null` until known: true when the content router answers, false when it is
   *  not mounted (404) or the reader is not the owner. */
  contentEnabled: boolean | null;
  publisher: PublisherWire | null;
  drafter: PublisherWire | null;
  loadCalendar: (range: CalendarRange) => Promise<void>;
  reloadCalendar: () => Promise<void>;
  loadPublisher: () => Promise<void>;
  setPublisher: (jobId: number) => Promise<PublisherWire>;
  setDrafter: (jobId: number) => Promise<PublisherWire>;
  createItem: (body: ContentInput) => Promise<ContentItemWire>;
  updateItem: (id: number, body: Partial<ContentInput>) => Promise<ContentItemWire>;
  removeItem: (id: number) => Promise<void>;
  approveItem: (id: number) => Promise<ContentItemWire>;
  unapproveItem: (id: number) => Promise<ContentItemWire>;
  retryItem: (id: number, body: ContentRetry) => Promise<ContentItemWire>;
  approveTheme: (id: number) => Promise<ContentItemWire>;
  unapproveTheme: (id: number) => Promise<ContentItemWire>;
  approveThemes: (from: string, to: string) => Promise<ApproveThemesWire>;
  retryDrafting: (id: number) => Promise<ContentItemWire>;
};

const EMPTY: CalendarWire = { fires: [], dense: [], runs: [] };

export const createContentSlice: StateCreator<ContentSlice & { me: Me | null }, [], [], ContentSlice> = (set, get) => {
  let seq = 0;   // the newest range read; an older answer is dropped when it lands

  /** Whether this reader should ask the content module anything at all. */
  const wantsContent = async (): Promise<boolean> => {
    if (get().contentEnabled === null) await get().loadPublisher();   // settles to false for a non-owner
    return get().contentEnabled === true;
  };

  /** Run a mutation, then re-read the range so the grid shows what the server has. */
  const mutate = async <T,>(call: () => Promise<T>): Promise<T> => {
    const out = await call();
    await get().reloadCalendar();
    return out;
  };

  return {
    calRange: null, calItems: [], calBacklog: [], calRoutines: null, calStatus: "idle",
    contentEnabled: null, publisher: null, drafter: null,

    async loadCalendar(range) {
      const mine = ++seq;
      set({ calRange: range, calStatus: "loading" });
      try {
        const content = await wantsContent();
        const [routines, plan] = await Promise.all([
          // A scheduler that is not mounted is no reason to hide the content plan.
          api.routines.calendar(range.from, range.to).catch((e: unknown) => {
            if (e instanceof ApiError && e.status === 404) return EMPTY;
            throw e;
          }),
          content ? api.content.list(range.from, range.to) : Promise.resolve(null),
        ]);
        if (mine !== seq) return;
        set({
          calRoutines: routines, calItems: plan?.items ?? [], calBacklog: plan?.backlog ?? [], calStatus: "ready",
        });
      } catch {
        if (mine === seq) set({ calStatus: "error" });
      }
    },

    async reloadCalendar() {
      const range = get().calRange;
      if (range) await get().loadCalendar(range);
    },

    async loadPublisher() {
      if (get().me?.role !== "owner") { set({ contentEnabled: false, publisher: null, drafter: null }); return; }
      // Asked alongside, settled after: the publisher's answer decides whether there is a plan.
      const drafter = Promise.resolve().then(() => api.content.drafter()).catch(() => null);
      try {
        set({ publisher: await api.content.publisher(), contentEnabled: true });
      } catch (e) {
        // 404 is "the content router is not mounted"; anything else is a failure
        // of this read, not a verdict, and leaves the question open.
        if (e instanceof ApiError && e.status === 404) set({ contentEnabled: false, publisher: null, drafter: null });
        else throw e;
        return;
      }
      set({ drafter: await drafter });
    },

    async setPublisher(jobId) {
      const p = await api.content.setPublisher(jobId);
      set({ publisher: p });
      return p;
    },

    async setDrafter(jobId) {
      const d = await api.content.setDrafter(jobId);
      set({ drafter: d });
      return d;
    },

    createItem: (body) => mutate(() => api.content.create(body)),
    updateItem: (id, body) => mutate(() => api.content.update(id, body)),
    removeItem: (id) => mutate(() => api.content.remove(id)),
    approveItem: (id) => mutate(() => api.content.approve(id)),
    unapproveItem: (id) => mutate(() => api.content.unapprove(id)),
    retryItem: (id, body) => mutate(() => api.content.retry(id, body)),
    approveTheme: (id) => mutate(() => api.content.approveTheme(id)),
    unapproveTheme: (id) => mutate(() => api.content.unapproveTheme(id)),
    approveThemes: (from, to) => mutate(() => api.content.approveThemes(from, to)),
    retryDrafting: (id) => mutate(() => api.content.retryDrafting(id)),
  };
};
