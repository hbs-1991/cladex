/** Store slice: plugins (Claude Code plugins from the reviewed catalog) and the actions on them. */

import type { StateCreator } from "zustand";
import { api } from "@/api/client";
import type { PluginPreview, PluginWire } from "@/api/types";

export type PluginsSlice = {
  plugins: PluginWire[]; pluginsLoaded: boolean;
  loadPlugins: () => Promise<void>;
  replacePlugin: (p: PluginWire) => void;
  /** Phase one: fetch at the manifest's ref and read what came back. Registers
   *  nothing — the clone is inert until `confirmInstallPlugin`. */
  fetchPlugin: (name: string) => Promise<PluginPreview>;
  /** Phase two: register what was read, against the token bound to it. */
  confirmInstallPlugin: (name: string, token: string) => Promise<void>;
  /** Cancel a fetched-but-unconfirmed plugin: the clone goes, no row existed. */
  cancelInstallPlugin: (name: string) => Promise<void>;
  enablePlugin: (name: string) => Promise<void>;
  disablePlugin: (name: string) => Promise<void>;
  /** Drops the registry row and the clone; a catalog entry stays listed as available. */
  removePlugin: (name: string) => Promise<void>;
};

export const createPluginsSlice: StateCreator<PluginsSlice, [], [], PluginsSlice> = (set) => {
  const replace = (p: PluginWire) => set((s) => ({ plugins: s.plugins.map((x) => (x.name === p.name ? p : x)) }));
  return {
    plugins: [], pluginsLoaded: false,
    async loadPlugins() { set({ plugins: (await api.plugins.list()).plugins, pluginsLoaded: true }); },
    replacePlugin: replace,
    fetchPlugin: (name) => api.plugins.install(name),
    async confirmInstallPlugin(name, token) { replace(await api.plugins.confirmInstall(name, token)); },
    async cancelInstallPlugin(name) { await api.plugins.remove(name); },
    async enablePlugin(name) { replace(await api.plugins.enable(name)); },
    async disablePlugin(name) { replace(await api.plugins.disable(name)); },
    async removePlugin(name) {
      await api.plugins.remove(name);
      // A custom entry is its own manifest (an approved draft), so removing it removes
      // the row from the list too; a catalog entry falls back to "installable again".
      set((s) => ({ plugins: s.plugins.flatMap((p) => p.name !== name ? [p]
        : p.source === "custom" ? [] : [{ ...p, state: "available", present: false, components: null, last_error: null, installed_at: null }]) }));
    },
  };
};
