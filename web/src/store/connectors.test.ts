import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({ api: { connectors: { list: vi.fn(), connect: vi.fn(), disable: vi.fn(), remove: vi.fn() } } }));

import { api } from "@/api/client";
import type { ConnectorWire } from "@/api/types";
import { useStore } from "@/store";

const notion: ConnectorWire = {
  name: "notion", label: "Notion", description: "d", source: "catalog", transport: "http", auth: "oauth",
  secrets: [], state: "available", tools: null, tool_names: [], installed_at: null, endpoint: "https://mcp.example.com/mcp", last_error: null, scopes: ["turn"],
};

beforeEach(() => { useStore.setState(useStore.getInitialState()); });

it("loads the list and replaces a row from a mutation's reply", async () => {
  vi.mocked(api.connectors.list).mockResolvedValue({ connectors: [notion] });
  vi.mocked(api.connectors.connect).mockResolvedValue({ ...notion, state: "connected", tools: 12 });
  await useStore.getState().loadConnectors();
  expect(useStore.getState().connectorsLoaded).toBe(true);
  await useStore.getState().connectConnector("notion");
  expect(useStore.getState().connectors[0]).toMatchObject({ state: "connected", tools: 12 });
  vi.mocked(api.connectors.remove).mockResolvedValue(undefined);
  await useStore.getState().removeConnector("notion");
  expect(useStore.getState().connectors[0]!.state).toBe("available");   // the catalog entry stays, unconnected
});
