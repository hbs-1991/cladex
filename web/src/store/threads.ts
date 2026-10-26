/** Store slice: the thread list, its paging cursor and the selected thread. */

import type { StateCreator } from "zustand";
import { api } from "@/api/client";
import type { ThreadWire } from "@/api/types";

export type ThreadsSlice = {
  list: ThreadWire[]; next: string | null; loaded: boolean; currentId: string | null;
  load: () => Promise<void>; loadMore: () => Promise<void>; create: () => Promise<string>;
  select: (id: string | null) => void; rename: (id: string, title: string) => Promise<void>;
  archive: (id: string) => Promise<void>; unarchive: (id: string) => Promise<void>;
  patchLocal: (id: string, patch: Partial<ThreadWire>) => void;
};

export const createThreadsSlice: StateCreator<ThreadsSlice, [], [], ThreadsSlice> = (set, get) => ({
  list: [], next: null, loaded: false, currentId: null,
  async load() {
    const page = await api.threads.list();
    set({ list: page.threads, next: page.next, loaded: true });
  },
  async loadMore() {
    const { next, list } = get();
    if (!next) return;
    const page = await api.threads.list({ before: next });
    set({ list: [...list, ...page.threads], next: page.next });
  },
  async create() {
    const t = await api.threads.create();
    set((s) => ({ list: [t, ...s.list], currentId: t.id }));
    return t.id;
  },
  select(id) { set({ currentId: id }); },
  async rename(id, title) {
    get().patchLocal(id, { title });
    const t = await api.threads.rename(id, title);
    get().patchLocal(id, t);
  },
  async archive(id) {
    await api.threads.archive(id);
    set((s) => ({ list: s.list.filter((t) => t.id !== id), currentId: s.currentId === id ? null : s.currentId }));
  },
  async unarchive(id) {
    await api.threads.unarchive(id);
    await get().load();
  },
  patchLocal(id, patch) {
    set((s) => ({ list: s.list.map((t) => (t.id === id ? { ...t, ...patch } : t)) }));
  },
});
