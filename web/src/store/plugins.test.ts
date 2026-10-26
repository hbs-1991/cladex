import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: { plugins: { list: vi.fn(), install: vi.fn(), confirmInstall: vi.fn(), enable: vi.fn(), disable: vi.fn(), remove: vi.fn() } },
}));

import { api } from "@/api/client";
import type { PluginWire } from "@/api/types";
import { useStore } from "@/store";

const marketing: PluginWire = {
  name: "marketing", label: "Marketing", description: "d", source: "catalog",
  repo: "https://github.com/anthropics/knowledge-work-plugins", ref: "0".repeat(40), subdir: "marketing",
  version: "1.2.0", scopes: ["turn", "cron"], state: "available", present: false,
  last_error: null, installed_at: null, components: null,
};
const custom: PluginWire = { ...marketing, name: "mine", label: "Mine", source: "custom" };

const installed: PluginWire = {
  ...marketing, state: "installed", present: true, installed_at: "2026-09-10T00:00:00Z",
  components: { commands: ["a"], skills: ["b"], agents: [], hooks: false, mcp: false, author: "Anthropic", plugin_version: "1.2.0" },
};

beforeEach(() => { useStore.setState(useStore.getInitialState()); });

it("loads the list and replaces a row from a mutation's reply", async () => {
  vi.mocked(api.plugins.list).mockResolvedValue({ plugins: [marketing] });
  vi.mocked(api.plugins.install).mockResolvedValue({
    name: "marketing", label: "Marketing", surface: "name:        marketing", token: "tok",
    servers: [], components: installed.components,
  });
  vi.mocked(api.plugins.confirmInstall).mockResolvedValue(installed);
  await useStore.getState().loadPlugins();
  expect(useStore.getState().pluginsLoaded).toBe(true);
  // Phase one reads; only the confirm puts the row in the list.
  const preview = await useStore.getState().fetchPlugin("marketing");
  expect(preview.token).toBe("tok");
  expect(useStore.getState().plugins[0]!.state).toBe("available");
  await useStore.getState().confirmInstallPlugin("marketing", preview.token);
  expect(useStore.getState().plugins).toHaveLength(1);            // replaced in place, not appended
  expect(useStore.getState().plugins[0]).toMatchObject({ state: "installed", present: true });

  vi.mocked(api.plugins.disable).mockResolvedValue({ ...installed, state: "disabled" });
  await useStore.getState().disablePlugin("marketing");
  expect(useStore.getState().plugins[0]!.state).toBe("disabled");
  vi.mocked(api.plugins.enable).mockResolvedValue(installed);
  await useStore.getState().enablePlugin("marketing");
  expect(useStore.getState().plugins[0]!.state).toBe("installed");
});

it("removing drops a custom entry and resets a catalog entry to available", async () => {
  vi.mocked(api.plugins.list).mockResolvedValue({ plugins: [installed, { ...custom, state: "installed", present: true }] });
  vi.mocked(api.plugins.remove).mockResolvedValue(undefined);
  await useStore.getState().loadPlugins();

  await useStore.getState().removePlugin("mine");
  expect(useStore.getState().plugins.map((p) => p.name)).toEqual(["marketing"]);   // the manifest went with it

  await useStore.getState().removePlugin("marketing");
  expect(useStore.getState().plugins[0]).toMatchObject({
    state: "available", present: false, components: null, installed_at: null, last_error: null,
  });
});
