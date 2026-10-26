/** Store slice: the offered models, and each open thread's (model, effort) pair. */

import type { StateCreator } from "zustand";
import { api } from "@/api/client";
import type { ModelWire, ModelsWire, ThreadModelWire } from "@/api/types";

/** The pair to SHOW for a lane.
 *
 *  An explicit pair is by definition the effective one — `resolve()` reads the
 *  thread row first — so the pill can trust it without asking the server again.
 *  That is what lets the `thread.model` event carry the explicit pair alone and
 *  still update an open tab; only a CLEARED pair needs a fresh read, because
 *  what the lane falls back to (its session, the user's default, the deploy
 *  default) is not something the browser can work out. */
export function effectivePair(tm: ThreadModelWire): { model: string; effort: string | null } {
  return tm.model ? { model: tm.model, effort: tm.effort } : { model: tm.effective_model, effort: tm.effective_effort };
}

export type ModelsSlice = {
  models: ModelWire[];
  deployDefault: ModelsWire["deploy_default"] | null;
  modelsStatus: "idle" | "loading" | "ready";
  /** Fetched once per tab: the catalog is deployment state, not lane state. A
   *  failed fetch settles as "ready" with an empty list — the pill then names
   *  the raw model id, which is still true, instead of spinning. */
  loadModels: () => Promise<void>;
  threadModel: Record<string, ThreadModelWire>;
  loadThreadModel: (id: string) => Promise<void>;
  setThreadModel: (id: string, choice: { model: string | null; effort: string | null }) => Promise<void>;
  /** What a `thread.model` event patches: the explicit pair only. */
  patchThreadModel: (id: string, pair: { model: string | null; effort: string | null }) => void;
};

export const createModelsSlice: StateCreator<ModelsSlice, [], [], ModelsSlice> = (set, get) => ({
  models: [], deployDefault: null, modelsStatus: "idle", threadModel: {},

  async loadModels() {
    if (get().modelsStatus !== "idle") return;
    set({ modelsStatus: "loading" });
    try {
      const wire = await api.listModels();
      set({ models: wire.models, deployDefault: wire.deploy_default, modelsStatus: "ready" });
    } catch { set({ modelsStatus: "ready" }); }
  },

  async loadThreadModel(id) {
    // A lane whose thread row does not exist yet answers 404. That is not an
    // error here: the pill falls back to the deploy default until a turn has
    // created the row.
    try {
      const d = await api.threads.get(id);
      set((s) => ({ threadModel: { ...s.threadModel, [id]: {
        model: d.model, effort: d.effort, effective_model: d.effective_model, effective_effort: d.effective_effort } } }));
    } catch { /* keep whatever is known */ }
  },

  async setThreadModel(id, choice) {
    // The reply carries all four keys — including the `effective_*` a cleared
    // pair falls back to — so nothing has to be guessed or re-read here.
    const tm = await api.setThreadModel(id, choice);
    set((s) => ({ threadModel: { ...s.threadModel, [id]: tm } }));
  },

  patchThreadModel(id, pair) {
    set((s) => {
      const known = s.threadModel[id];
      if (!known) return s;                       // nothing to patch into yet; the next read brings it
      return { threadModel: { ...s.threadModel, [id]: { ...known, model: pair.model, effort: pair.effort } } };
    });
  },
});
