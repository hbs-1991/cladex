/** Adding a plugin to the catalog from the browser. What matters is that the pasted
 *  link fills the fields the owner would otherwise mistype, that a branch cannot be
 *  submitted as a pin, and that what is sent is a draft — not an install. */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  ApiError: class extends Error {
    status: number; code: string;
    constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; }
  },
  api: { drafts: { create: vi.fn() } },
}));
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }) }));

import { api } from "@/api/client";
import { useStore } from "@/store";
import { AddPluginDialog } from "./AddPluginDialog";

const SHA = "11ac5eeaca27c764913a06f1e61a7cb712790b1a";
const LINK = `https://github.com/anthropics/knowledge-work-plugins/tree/${SHA}/marketing`;

beforeEach(() => {
  vi.clearAllMocks();
  useStore.setState({ drafts: [], rejectedDrafts: [] });
});

const mount = () => render(<AddPluginDialog open onOpenChange={() => {}} />);

it("fills the manifest out of a pasted link and queues a draft", async () => {
  vi.mocked(api.drafts.create).mockResolvedValue({
    name: "marketing", kind: "plugin", label: "marketing", description: "Campaign work.",
    transport: null, repo: "https://github.com/anthropics/knowledge-work-plugins", ref: SHA,
  });
  mount();
  await userEvent.click(screen.getByLabelText("Link to the plugin"));
  await userEvent.paste(LINK);
  expect(screen.getByLabelText("Repository")).toHaveValue("https://github.com/anthropics/knowledge-work-plugins");
  expect(screen.getByLabelText("Commit or tag")).toHaveValue(SHA);
  expect(screen.getByLabelText("Directory in the repository")).toHaveValue("marketing");
  expect(screen.getByLabelText("Name")).toHaveValue("marketing");

  await userEvent.type(screen.getByLabelText("What it is for"), "Campaign work.");
  await userEvent.click(screen.getByRole("button", { name: "Queue for approval" }));
  expect(api.drafts.create).toHaveBeenCalledWith({
    name: "marketing", repo: "https://github.com/anthropics/knowledge-work-plugins",
    ref: SHA, subdir: "marketing", label: "marketing", description: "Campaign work.",
  });
});

it("refuses a branch as a pin before the request is made", async () => {
  mount();
  await userEvent.click(screen.getByLabelText("Link to the plugin"));
  await userEvent.paste("https://github.com/o/r/tree/main/radar");
  // The link parsed, but `main` is not a pin, so that field is left for the owner.
  expect(screen.getByLabelText("Repository")).toHaveValue("https://github.com/o/r");
  expect(screen.getByLabelText("Commit or tag")).toHaveValue("");
  await userEvent.type(screen.getByLabelText("What it is for"), "Radar.");
  expect(screen.getByRole("button", { name: "Queue for approval" })).toBeDisabled();

  await userEvent.type(screen.getByLabelText("Commit or tag"), "main");
  expect(screen.getByRole("button", { name: "Queue for approval" })).toBeDisabled();
  await userEvent.clear(screen.getByLabelText("Commit or tag"));
  await userEvent.type(screen.getByLabelText("Commit or tag"), "v1.4.0");
  expect(screen.getByRole("button", { name: "Queue for approval" })).toBeEnabled();
  expect(api.drafts.create).not.toHaveBeenCalled();
});

it("shows the validator's own words when the server refuses", async () => {
  const { ApiError } = await import("@/api/client");
  vi.mocked(api.drafts.create).mockRejectedValue(
    new ApiError(400, "bad_manifest", "plugin/radar: 'ref' must be a release tag"));
  mount();
  await userEvent.click(screen.getByLabelText("Link to the plugin"));
  await userEvent.paste("https://github.com/o/r/tree/v1.0.0/radar");
  await userEvent.type(screen.getByLabelText("What it is for"), "Radar.");
  await userEvent.click(screen.getByRole("button", { name: "Queue for approval" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("'ref' must be a release tag");
});
