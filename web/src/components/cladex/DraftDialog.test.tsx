import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  ApiError: class extends Error {
    status: number; code: string;
    constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; }
  },
  api: {
    drafts: { list: vi.fn(), review: vi.fn(), approve: vi.fn(), discard: vi.fn() },
    plugins: { list: vi.fn() },
    connectors: { list: vi.fn() },
  },
}));
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }) }));

import { ApiError, api } from "@/api/client";
import type { DraftWire } from "@/api/types";
import { useStore } from "@/store";
import { DraftDialog } from "./DraftDialog";
import { PendingDrafts } from "./PendingDrafts";

const draft: DraftWire = { name: "acme", kind: "plugin", label: "Acme", description: "A drafted plugin.", transport: null, repo: "https://github.com/acme/p", ref: "v1.2.0" };

/** Lines that a markdown renderer would eat: the surface must survive them. */
const SURFACE = "plugin: acme\n# not a heading\n- run: npx -y @acme/cli --serve\n**stays literal**\n";

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  useStore.setState({ me: { user_id: 1, role: "owner", default_model: null, default_effort: null } });
  vi.mocked(api.drafts.review).mockResolvedValue({ name: "acme", kind: "plugin", label: "Acme", surface: SURFACE, token: "a1b2c3d4", local_command: false });
  vi.mocked(api.drafts.list).mockResolvedValue({ drafts: [draft], rejected: [] });
  vi.mocked(api.plugins.list).mockResolvedValue({ plugins: [] });
  vi.mocked(api.drafts.approve).mockResolvedValue({ name: "acme", kind: "plugin" });
  vi.mocked(api.drafts.discard).mockResolvedValue(undefined);
});

const mount = (onOpenChange = vi.fn()) => {
  render(<DraftDialog draft={draft} open onOpenChange={onOpenChange} />);
  return onOpenChange;
};
const surfaceEl = () => document.querySelector("pre");

it("reads the draft once and renders its surface verbatim, never as markdown", async () => {
  mount();
  await waitFor(() => expect(surfaceEl()).not.toBeNull());
  expect(api.drafts.review).toHaveBeenCalledTimes(1);
  expect(api.drafts.review).toHaveBeenCalledWith("acme");
  const pre = surfaceEl()!;
  expect(pre.textContent).toBe(SURFACE);                 // byte for byte, including the "# " line
  expect(pre.querySelector("h1")).toBeNull();
  expect(pre.querySelector("strong")).toBeNull();
  expect(screen.queryByText("Local command")).toBeNull();
});

it("draws the warning strip for a draft that runs a local command", async () => {
  vi.mocked(api.drafts.review).mockResolvedValue({ name: "acme", kind: "plugin", label: "Acme", surface: SURFACE, token: "t", local_command: true });
  mount();
  expect(await screen.findByText("Local command")).toBeInTheDocument();
  expect(screen.getByText(/runs a command on the machine CladeX runs on/)).toBeInTheDocument();
});

it("approves with the token from the review call and closes", async () => {
  const onOpenChange = mount();
  await waitFor(() => expect(surfaceEl()).not.toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "Approve" }));
  await waitFor(() => expect(api.drafts.approve).toHaveBeenCalledWith("acme", "a1b2c3d4"));
  expect(onOpenChange).toHaveBeenCalledWith(false);
});

it("re-reads the draft when it changed under the token", async () => {
  vi.mocked(api.drafts.approve).mockRejectedValueOnce(new ApiError(409, "stale_draft", "the draft changed"));
  vi.mocked(api.drafts.review).mockResolvedValueOnce({ name: "acme", kind: "plugin", label: "Acme", surface: SURFACE, token: "old", local_command: false })
    .mockResolvedValueOnce({ name: "acme", kind: "plugin", label: "Acme", surface: "plugin: acme (v2)\n", token: "new", local_command: false });
  const onOpenChange = mount();
  await waitFor(() => expect(surfaceEl()).not.toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(await screen.findByText("The draft changed since you read it. Read it again before approving.")).toBeInTheDocument();
  await waitFor(() => expect(api.drafts.review).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(surfaceEl()!.textContent).toBe("plugin: acme (v2)\n"));
  expect(onOpenChange).not.toHaveBeenCalledWith(false);
});

it("discards the draft and closes", async () => {
  const onOpenChange = mount();
  await waitFor(() => expect(surfaceEl()).not.toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "Discard" }));
  await waitFor(() => expect(api.drafts.discard).toHaveBeenCalledWith("acme"));
  expect(onOpenChange).toHaveBeenCalledWith(false);
});

const mountSection = (kind: "mcp" | "plugin") =>
  render(<MemoryRouter><PendingDrafts kind={kind} /></MemoryRouter>);

it("shows the queue only on the tab of the draft's kind", async () => {
  mountSection("plugin");
  expect(await screen.findByText("Pending review")).toBeInTheDocument();
  expect(screen.getByText("Acme")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Review" }));
  expect(await screen.findByRole("dialog", { name: "Review “Acme”" })).toBeInTheDocument();
});

it("stays out of the way when nothing of that kind is waiting, and names the drafts that will not parse", async () => {
  const { container, unmount } = mountSection("mcp");
  await waitFor(() => expect(useStore.getState().draftsLoaded).toBe(true));
  expect(container).toBeEmptyDOMElement();
  unmount();

  useStore.setState(useStore.getInitialState());
  useStore.setState({ me: { user_id: 1, role: "owner", default_model: null, default_effort: null } });
  vi.mocked(api.drafts.list).mockResolvedValue({ drafts: [], rejected: [{ file: "broken.yaml", reason: "ref is not a sha" }] });
  mountSection("mcp");
  expect(await screen.findByText("Will not parse")).toBeInTheDocument();
  expect(screen.getByText("broken.yaml: ref is not a sha")).toBeInTheDocument();
});

it("hides the queue from a plain user, who cannot approve anything", async () => {
  useStore.setState({ me: { user_id: 2, role: "user", default_model: null, default_effort: null } });
  const { container } = mountSection("plugin");
  await waitFor(() => expect(container).toBeEmptyDOMElement());
  expect(api.drafts.list).not.toHaveBeenCalled();
});
