/** Store slice: connectors (MCP servers the agent may use) and the actions on them. */

import type { StateCreator } from "zustand";
import { api } from "@/api/client";
import type { ConnectorWire } from "@/api/types";

export type ConnectorsSlice = {
  connectors: ConnectorWire[]; connectorsLoaded: boolean;
  loadConnectors: () => Promise<void>;
  replaceConnector: (c: ConnectorWire) => void;
  connectConnector: (name: string) => Promise<void>;
  enableConnector: (name: string) => Promise<void>;
  disableConnector: (name: string) => Promise<void>;
  /** Forgets the registry row and credentials; a catalog entry stays listed as available. */
  removeConnector: (name: string) => Promise<void>;
};

export const createConnectorsSlice: StateCreator<ConnectorsSlice, [], [], ConnectorsSlice> = (set) => {
  const replace = (c: ConnectorWire) => set((s) => ({ connectors: s.connectors.map((x) => (x.name === c.name ? c : x)) }));
  return {
    connectors: [], connectorsLoaded: false,
    async loadConnectors() { set({ connectors: (await api.connectors.list()).connectors, connectorsLoaded: true }); },
    replaceConnector: replace,
    async connectConnector(name) { replace(await api.connectors.connect(name)); },
    async enableConnector(name) { replace(await api.connectors.enable(name)); },
    async disableConnector(name) { replace(await api.connectors.disable(name)); },
    async removeConnector(name) {
      await api.connectors.remove(name);
      set((s) => ({ connectors: s.connectors.flatMap((c) => c.name !== name ? [c]
        : c.source === "custom" ? [] : [{ ...c, state: "available", tools: null, last_error: null, secrets: c.secrets.map((x) => ({ ...x, set: false })) }]) }));
    },
  };
};
