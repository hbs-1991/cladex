import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  ApiError: class extends Error {
    status: number; code: string;
    constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; }
  },
  api: { skills: { list: vi.fn(), get: vi.fn(), save: vi.fn(), pin: vi.fn(), unpin: vi.fn(), archive: vi.fn(), restore: vi.fn() } },
}));
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }) }));

import { ApiError, api } from "@/api/client";
import type { SkillDetail } from "@/api/types";
import { useStore } from "@/store";
import { SkillPage } from "./SkillPage";

const CONTENT = "---\nname: weekly-report\ndescription: The Friday report.\n---\n\n## Steps\n\nAsk the team.\n";
const detail: SkillDetail = {
  name: "weekly-report", title: "Weekly report", description: "The Friday report.", broken: false,
  source: "agent", state: "active", pinned: false, use_count: 4, patch_count: 2,
  created_at: "2026-09-01T09:00:00+00:00", last_used: "2026-09-09T09:00:00+00:00", last_patched: "2026-09-08T09:00:00+00:00",
  files: 2, bytes: 2048, content: CONTENT, etag: "abc123", tree: [{ path: "references/x.md", bytes: 1024 }],
};

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  useStore.setState({ me: { user_id: 1, role: "owner", default_model: null, default_effort: null } });
  vi.mocked(api.skills.get).mockResolvedValue(detail);
  vi.mocked(api.skills.list).mockResolvedValue({ skills: [detail] });   // the row the badges and the menu read
});

const mount = (name = "weekly-report") => render(
  <MemoryRouter initialEntries={[`/customize/skills/${name}`]}>
    <Routes><Route path="/customize/skills/:name" element={<SkillPage />} /></Routes>
  </MemoryRouter>,
);

it("renders SKILL.md as markdown, with the stats and the other files", async () => {
  mount();
  expect(await screen.findByRole("heading", { name: "Weekly report", level: 2 })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Steps", level: 2 })).toBeInTheDocument();   // ## in the body
  expect(screen.getByText("Ask the team.")).toBeInTheDocument();
  expect(screen.getByText("used 4 times")).toBeInTheDocument();
  expect(screen.getByText("references/x.md")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Skills/ })).toHaveAttribute("href", "/customize/skills");
});

it("edits SKILL.md and saves it with the etag it read", async () => {
  mount();
  await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
  const box = screen.getByLabelText("SKILL.md");
  expect(box).toHaveValue(CONTENT);
  vi.mocked(api.skills.save).mockResolvedValue({ ...detail, content: `${CONTENT}More.`, etag: "def456", patch_count: 3 });
  await userEvent.type(box, "More.");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.skills.save).toHaveBeenCalledWith("weekly-report", { content: `${CONTENT}More.`, etag: "abc123" }));
  await waitFor(() => expect(screen.queryByLabelText("SKILL.md")).not.toBeInTheDocument());   // back to the rendered body
  expect(screen.getByText(/More\./)).toBeInTheDocument();
});

it("saves on Ctrl+S without leaving the keyboard", async () => {
  mount();
  await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
  vi.mocked(api.skills.save).mockResolvedValue({ ...detail, content: CONTENT });
  await userEvent.click(screen.getByLabelText("SKILL.md"));
  await userEvent.keyboard("{Control>}s{/Control}");
  await waitFor(() => expect(api.skills.save).toHaveBeenCalledWith("weekly-report", { content: CONTENT, etag: "abc123" }));
});

it("says so when the agent changed the skill mid-edit, and reloads the buffer on request", async () => {
  mount();
  await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
  await userEvent.type(screen.getByLabelText("SKILL.md"), "Mine.");
  vi.mocked(api.skills.save).mockRejectedValue(new ApiError(409, "stale_edit", "changed"));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("The agent changed this skill while you were editing.")).toBeInTheDocument();
  expect(screen.getByLabelText("SKILL.md")).toHaveValue(`${CONTENT}Mine.`);               // nothing typed is thrown away yet

  vi.mocked(api.skills.get).mockResolvedValue({ ...detail, content: `${CONTENT}Theirs.`, etag: "def456" });
  await userEvent.click(screen.getByRole("button", { name: "Reload" }));
  await waitFor(() => expect(screen.getByLabelText("SKILL.md")).toHaveValue(`${CONTENT}Theirs.`));
  expect(screen.queryByText("The agent changed this skill while you were editing.")).not.toBeInTheDocument();
});

it("refuses to archive a pinned skill until it is unpinned", async () => {
  vi.mocked(api.skills.get).mockResolvedValue({ ...detail, pinned: true });
  vi.mocked(api.skills.list).mockResolvedValue({ skills: [{ ...detail, pinned: true }] });
  mount();
  await userEvent.click(await screen.findByRole("button", { name: "Actions" }));
  expect(screen.getByRole("menuitem", { name: "Unpin" })).toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: "Archive" })).not.toBeInTheDocument();
  vi.mocked(api.skills.unpin).mockResolvedValue({ ...detail, pinned: false });
  await userEvent.click(screen.getByRole("menuitem", { name: "Unpin" }));
  await userEvent.click(await screen.findByRole("button", { name: "Actions" }));
  expect(await screen.findByRole("menuitem", { name: "Archive" })).toBeInTheDocument();
});

it("offers a plain user Edit and Actions — the page is their own library", async () => {
  useStore.setState({ me: { user_id: 2, role: "user", default_model: null, default_effort: null } });
  mount();
  expect(await screen.findByRole("heading", { name: "Weekly report", level: 2 })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Actions" })).toBeInTheDocument();
});

it("says so when the name is unknown", async () => {
  vi.mocked(api.skills.get).mockRejectedValue(new ApiError(404, "not_found", "no"));
  mount("nope");
  expect(await screen.findByText("No such skill.")).toBeInTheDocument();
});
