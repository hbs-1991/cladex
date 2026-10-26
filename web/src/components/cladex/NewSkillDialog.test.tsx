/** The two ways a skill gets made. The manual form is covered through the tab
 *  (`SkillsTab.test.tsx`); what is worth pinning here is the handover: the chat
 *  opens on a new thread with the request in the composer, unsent. */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  ApiError: class extends Error {
    status: number; code: string;
    constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; }
  },
  api: { threads: { create: vi.fn() }, skills: { create: vi.fn() } },
}));

import { api } from "@/api/client";
import { useStore } from "@/store";
import { NewSkillDialog } from "./NewSkillDialog";

/** Stands in for the chat page: shows where we landed and what the composer got. */
function Landed() {
  const { state } = useLocation();
  return <div data-testid="landed">{String((state as { draft?: string } | null)?.draft ?? "")}</div>;
}

beforeEach(() => {
  vi.clearAllMocks();
  useStore.setState({ list: [], currentId: null, skills: [] });
});

const mount = () => render(
  <MemoryRouter initialEntries={["/customize/skills"]}>
    <Routes>
      <Route path="/customize/skills" element={<NewSkillDialog open onOpenChange={() => {}} />} />
      <Route path="/t/:id" element={<Landed />} />
    </Routes>
  </MemoryRouter>,
);

it("hands the job to a chat, with the skill-authoring request already written", async () => {
  vi.mocked(api.threads.create).mockResolvedValue({
    id: "th-7", title: "New chat", created_at: "2026-09-12T08:00:00+00:00",
    updated_at: "2026-09-12T08:00:00+00:00", last_message_at: null, archived: false,
  } as never);
  mount();
  await userEvent.click(screen.getByRole("button", { name: /Write it with the agent/ }));
  await userEvent.type(screen.getByLabelText("What should the skill do?"), "  File the Friday report  ");
  await userEvent.click(screen.getByRole("button", { name: "Open in chat" }));

  const landed = await screen.findByTestId("landed");
  expect(api.threads.create).toHaveBeenCalledTimes(1);
  expect(landed).toHaveTextContent("Load the skill-authoring skill first");
  expect(landed).toHaveTextContent("It should: File the Friday report");   // trimmed, appended
  expect(api.skills.create).not.toHaveBeenCalled();                        // nothing written yet
});

it("will not open a chat with nothing in it", async () => {
  mount();
  await userEvent.click(screen.getByRole("button", { name: /Write it with the agent/ }));
  expect(screen.getByRole("button", { name: "Open in chat" })).toBeDisabled();
  await userEvent.type(screen.getByLabelText("What should the skill do?"), "x");
  expect(screen.getByRole("button", { name: "Open in chat" })).toBeEnabled();
});

it("goes back to the choice, keeping neither draft", async () => {
  mount();
  await userEvent.click(screen.getByRole("button", { name: /Write it myself/ }));
  expect(screen.getByLabelText("SKILL.md")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  await waitFor(() => expect(screen.queryByLabelText("SKILL.md")).not.toBeInTheDocument());
  expect(screen.getByRole("button", { name: /Write it with the agent/ })).toBeInTheDocument();
});
