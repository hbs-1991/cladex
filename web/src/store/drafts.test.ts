import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: {
    drafts: { list: vi.fn(), review: vi.fn(), approve: vi.fn(), discard: vi.fn() },
    plugins: { list: vi.fn() },
    connectors: { list: vi.fn() },
  },
}));

import { api } from "@/api/client";
import type { DraftWire } from "@/api/types";
import { useStore } from "@/store";

const pluginDraft: DraftWire = {
  name: "acme", kind: "plugin", label: "Acme", description: "d",
  transport: null, repo: "https://github.com/acme/acme", ref: "1".repeat(40),
};
const mcpDraft: DraftWire = {
  name: "linear", kind: "mcp", label: "Linear", description: "d",
  transport: "http", repo: null, ref: null,
};

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  vi.mocked(api.plugins.list).mockResolvedValue({ plugins: [] });
  vi.mocked(api.connectors.list).mockResolvedValue({ connectors: [] });
});

it("loads pending and rejected drafts", async () => {
  vi.mocked(api.drafts.list).mockResolvedValue({ drafts: [pluginDraft, mcpDraft], rejected: [{ file: "x.yaml", reason: "bad ref" }] });
  await useStore.getState().loadDrafts();
  expect(useStore.getState().draftsLoaded).toBe(true);
  expect(useStore.getState().drafts).toHaveLength(2);
  expect(useStore.getState().rejectedDrafts[0]).toMatchObject({ file: "x.yaml", reason: "bad ref" });
});

it("approving drops the draft and reloads the tab that now owns it", async () => {
  vi.mocked(api.drafts.list).mockResolvedValue({ drafts: [pluginDraft, mcpDraft], rejected: [] });
  vi.mocked(api.drafts.review).mockResolvedValue({
    name: "acme", kind: "plugin", label: "Acme", surface: "line one\n## not markdown", token: "deadbeef", local_command: false,
  });
  vi.mocked(api.drafts.approve).mockResolvedValue({ name: "acme", kind: "plugin" });
  await useStore.getState().loadDrafts();

  const review = await useStore.getState().reviewDraft("acme");
  expect(review.token).toBe("deadbeef");
  await useStore.getState().approveDraft("acme", review.token);
  expect(useStore.getState().drafts.map((d) => d.name)).toEqual(["linear"]);
  expect(api.plugins.list).toHaveBeenCalledTimes(1);      // the approved entry is now installable
  expect(api.connectors.list).not.toHaveBeenCalled();

  vi.mocked(api.drafts.approve).mockResolvedValue({ name: "linear", kind: "mcp" });
  await useStore.getState().approveDraft("linear", "cafe");
  expect(useStore.getState().drafts).toEqual([]);
  expect(api.connectors.list).toHaveBeenCalledTimes(1);
});

it("discarding drops the draft — or the rejected file of the same stem — without reloading anything", async () => {
  vi.mocked(api.drafts.list).mockResolvedValue({ drafts: [pluginDraft], rejected: [{ file: "broken.yaml", reason: "bad ref" }] });
  vi.mocked(api.drafts.discard).mockResolvedValue(undefined);
  await useStore.getState().loadDrafts();

  await useStore.getState().discardDraft("acme");
  expect(useStore.getState().drafts).toEqual([]);
  expect(api.plugins.list).not.toHaveBeenCalled();

  await useStore.getState().discardDraft("broken");
  expect(useStore.getState().rejectedDrafts).toEqual([]);
});
