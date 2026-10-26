import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  ApiError: class extends Error {
    status: number; code: string;
    constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; }
  },
  api: { wiki: { graph: vi.fn(), page: vi.fn() } },
}));

import { api } from "@/api/client";
import type { WikiGraphWire, WikiPageDetail } from "@/api/types";
import { useStore } from "@/store";
import { Memory } from "./Memory";

const NUTRITION = "---\ntitle: Nutrition targets\nsummary: calories\n---\n\n"
  + "Targets. See [[user/biometrics]] for the numbers behind them.\n";

const graph: WikiGraphWire = {
  pages: [
    { path: "user/nutrition", ns: "user", title: "Nutrition targets", summary: "calories",
      tags: ["nutrition"], updated: "2026-07-28", links: ["user/biometrics"], in_index: true },
    { path: "user/biometrics", ns: "user", title: "Biometrics", summary: "body",
      tags: [], updated: "2026-07-20", links: [], in_index: false },
    { path: "content/linkedin", ns: "content", title: "LinkedIn voice", summary: "",
      tags: [], updated: "", links: [], in_index: true },
  ],
  namespaces: ["content", "user"],
  missing: [],
};
const detail = (path: string, content: string): WikiPageDetail => ({
  ...graph.pages.find((p) => p.path === path)!, content, etag: "e".repeat(64),
});

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  vi.mocked(api.wiki.graph).mockResolvedValue(graph);
  vi.mocked(api.wiki.page).mockImplementation(async (path: string) =>
    detail(path, path === "user/nutrition" ? NUTRITION : "---\ntitle: Biometrics\n---\n\nHeight and weight.\n"));
});

const mount = (entry = "/memory") => render(
  <MemoryRouter initialEntries={[entry]}>
    <Routes><Route path="/memory" element={<Memory />} /></Routes>
  </MemoryRouter>,
);

it("loads the graph and draws a node for every page", async () => {
  mount();
  expect(await screen.findByRole("button", { name: "Nutrition targets" })).toBeInTheDocument();
  expect(api.wiki.graph).toHaveBeenCalledTimes(1);
  expect(screen.getAllByTestId("wiki-hub")).toHaveLength(2);
  expect(screen.getByText(/3 pages/)).toBeInTheDocument();
});

it("re-reads the wiki when the tab comes back into view", async () => {
  mount();
  await screen.findByRole("button", { name: "Nutrition targets" });
  document.dispatchEvent(new Event("visibilitychange"));        // jsdom reports "visible"
  await waitFor(() => expect(api.wiki.graph).toHaveBeenCalledTimes(2));
});

it("opens the page named in the URL, with its frontmatter shown as chrome", async () => {
  mount("/memory?page=user/nutrition");
  expect(await screen.findByRole("heading", { name: "Nutrition targets" })).toBeInTheDocument();
  expect(api.wiki.page).toHaveBeenCalledWith("user/nutrition");
  expect(screen.getByText("calories")).toBeInTheDocument();
  expect(screen.getByText("nutrition")).toBeInTheDocument();               // a tag badge
  // The YAML block is stripped from the body, not printed above it.
  expect(screen.queryByText(/summary: calories/)).not.toBeInTheDocument();
  expect(screen.getByText(/Targets\./)).toBeInTheDocument();
});

it("clicking a node puts the page in the URL and opens it", async () => {
  mount();
  await userEvent.click(await screen.findByRole("button", { name: "Biometrics" }));
  await waitFor(() => expect(api.wiki.page).toHaveBeenCalledWith("user/biometrics"));
  expect(await screen.findByText("Height and weight.")).toBeInTheDocument();
  // The page the agent forgot to index says so in the sheet as well as on the graph.
  expect(screen.getAllByText("Written but not listed in index.md").length).toBeGreaterThan(0);
});

it("a wikilink inside the body walks to the page it names", async () => {
  mount("/memory?page=user/nutrition");
  const link = await screen.findByRole("link", { name: "user/biometrics" });
  await userEvent.click(link);
  await waitFor(() => expect(api.wiki.page).toHaveBeenCalledWith("user/biometrics"));
  expect(await screen.findByText("Height and weight.")).toBeInTheDocument();
});

it("shows the empty state when the agent has written nothing down", async () => {
  vi.mocked(api.wiki.graph).mockResolvedValue({ pages: [], namespaces: [], missing: [] });
  mount();
  expect(await screen.findByText(/Nothing written down yet/)).toBeInTheDocument();
  expect(screen.queryByTestId("wiki-page")).not.toBeInTheDocument();
});
