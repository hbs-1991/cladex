/** Store slice: queued catalog drafts and the two-step review → approve path.
 *
 *  One slice serves both tabs: an `mcp` draft is reviewed on Connectors, a
 *  `plugin` draft on Plugins, and the component filters by kind. */

import type { StateCreator } from "zustand";
import { api } from "@/api/client";
import type { DraftKind, DraftReview, DraftWire, PluginDraftCreate, RejectedDraft } from "@/api/types";
import type { ConnectorsSlice } from "./connectors";
import type { PluginsSlice } from "./plugins";

export type DraftsSlice = {
  drafts: DraftWire[]; rejectedDrafts: RejectedDraft[]; draftsLoaded: boolean;
  loadDrafts: () => Promise<void>;
  /** Queues a plugin manifest the owner typed. It joins the same review queue a
   *  chat-authored draft joins — nothing is installed and nothing is approved. */
  createPluginDraft: (body: PluginDraftCreate) => Promise<DraftWire>;
  /** Snapshots the draft server-side and returns the surface plus a token bound to its bytes. */
  reviewDraft: (name: string) => Promise<DraftReview>;
  /** Posts back the token from `reviewDraft`; the approved entry lands in the catalog. */
  approveDraft: (name: string, token: string) => Promise<DraftKind>;
  discardDraft: (name: string) => Promise<void>;
};

/** `<stem>.yaml` — a rejected draft is named by file, an approval by name. */
const stem = (file: string) => file.replace(/\.[^.]+$/, "");

export const createDraftsSlice: StateCreator<DraftsSlice & PluginsSlice & ConnectorsSlice, [], [], DraftsSlice> = (set, get) => {
  const drop = (name: string) => set((s) => ({
    drafts: s.drafts.filter((d) => d.name !== name),
    rejectedDrafts: s.rejectedDrafts.filter((r) => stem(r.file) !== name),
  }));
  return {
    drafts: [], rejectedDrafts: [], draftsLoaded: false,
    async loadDrafts() {
      const { drafts, rejected } = await api.drafts.list();
      set({ drafts, rejectedDrafts: rejected, draftsLoaded: true });
    },
    async createPluginDraft(body) {
      const d = await api.drafts.create(body);
      // Replace rather than append: filing the same name twice overwrites the draft
      // on disk, so a second row here would be a queue entry that does not exist.
      set((s) => ({ drafts: [...s.drafts.filter((x) => x.name !== d.name), d] }));
      return d;
    },
    reviewDraft: (name) => api.drafts.review(name),
    async approveDraft(name, token) {
      const { kind } = await api.drafts.approve(name, token);
      drop(name);
      // The manifest is in the catalog now, so the tab that owns this kind has a
      // new installable row it cannot know about otherwise.
      await (kind === "plugin" ? get().loadPlugins() : get().loadConnectors());
      return kind;
    },
    async discardDraft(name) { await api.drafts.discard(name); drop(name); },
  };
};
