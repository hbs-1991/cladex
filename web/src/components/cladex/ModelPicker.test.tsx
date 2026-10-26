import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return { ApiError: actual.ApiError, api: {
    listModels: vi.fn(), setThreadModel: vi.fn(), threads: { get: vi.fn() },
  } };
});

import { ApiError, api } from "@/api/client";
import type { ModelWire, ThreadDetailWire } from "@/api/types";
import { ContextUsageContext } from "@/runtime/context-usage";
import { ThreadIdContext } from "@/runtime/thread-id";
import { useStore } from "@/store";
import { ModelPicker } from "./ModelPicker";

const ID = "web:1:x";

const model = (over: Partial<ModelWire>): ModelWire => ({
  id: "", label: "", window: 1_000_000, price: { in: 3, out: 15 }, cache_write: 1.25,
  efforts: ["low", "medium", "high"], default_effort: "medium", ...over });

const MODELS = [
  model({ id: "claude-sonnet-5", label: "Sonnet 5" }),
  model({ id: "claude-opus-5", label: "Opus 5", price: { in: 15, out: 75 }, efforts: ["medium", "high", "max"], default_effort: "high" }),
  model({ id: "claude-haiku-4-5", label: "Haiku 4.5", window: 200_000, price: { in: 1, out: 5 } }),
];

const detail = (over: Partial<ThreadDetailWire> = {}): ThreadDetailWire => ({
  id: ID, title: null, channel: "web", archived: false, running: false,
  last_event_at: "", created_at: "", model: null, effort: null,
  effective_model: "claude-sonnet-5", effective_effort: null, ...over });

/** A last turn that filled 500K of the window — enough to shut Haiku out. */
const USAGE = { model: "claude-sonnet-5", window: 1_000_000, used: 500_000, input: 1_000, cacheRead: 490_000, cacheCreation: 9_000 };

function mount(usage: typeof USAGE | null = USAGE) {
  return render(
    <ThreadIdContext.Provider value={ID}>
      <ContextUsageContext.Provider value={usage}><ModelPicker /></ContextUsageContext.Provider>
    </ThreadIdContext.Provider>,
  );
}

/** Not `getByRole`: an open Radix menu is modal, so it hides the rest of the tree
 *  from the accessibility tree — and half of these assertions read the pill while
 *  the menu is still open. */
const pill = () => document.querySelector('[data-slot="cladex_model-pill"]');
const openPicker = async () => {
  await waitFor(() => expect(pill()).not.toBeNull());
  await userEvent.click(pill()!);
};

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  vi.mocked(api.listModels).mockReset().mockResolvedValue({ models: MODELS, deploy_default: { model: "claude-sonnet-5", effort: null } });
  vi.mocked(api.threads.get).mockReset().mockResolvedValue(detail());
  vi.mocked(api.setThreadModel).mockReset();
});

it("names the lane's effective pair, and re-reads the thread every time it opens", async () => {
  mount();
  await waitFor(() => expect(pill()).toHaveTextContent("Sonnet 5 · Auto"));
  expect(api.threads.get).toHaveBeenCalledTimes(1);

  // Telegram moved the pair while this tab was idle: opening must show that, not
  // what the tab happened to read when it mounted.
  vi.mocked(api.threads.get).mockResolvedValue(detail({ model: "claude-opus-5", effort: "high" }));
  await openPicker();
  await waitFor(() => expect(pill()).toHaveTextContent("Opus 5 · High"));
  expect(api.threads.get).toHaveBeenCalledTimes(2);
});

it("lists every model with its price and the effective model's efforts", async () => {
  mount();
  await waitFor(() => expect(pill()).toHaveTextContent("Sonnet 5"));
  await openPicker();
  expect(await screen.findByRole("menuitemradio", { name: /Sonnet 5/ })).toHaveTextContent("$3 / $15");
  expect(screen.getByRole("menuitemradio", { name: /Opus 5/ })).toHaveTextContent("$15 / $75");
  // Sonnet 5's own levels, plus its Auto named with the model's default.
  expect(screen.getByRole("menuitemradio", { name: "Auto (Medium)" })).toBeInTheDocument();
  expect(screen.getAllByRole("menuitemradio").map((x) => x.textContent))
    .toEqual(expect.arrayContaining(["Low", "Medium", "High"]));
  expect(screen.queryByRole("menuitemradio", { name: "Max" })).toBeNull();   // Opus 5's level, not Sonnet 5's
});

it("disables a model this chat no longer fits, and never on an unknown window", async () => {
  useStore.setState({ models: [...MODELS, model({ id: "x", label: "Unknown", window: 0 })], modelsStatus: "ready" });
  mount();
  await openPicker();
  expect(await screen.findByRole("menuitemradio", { name: /Haiku 4\.5/ })).toHaveAttribute("data-disabled");
  expect(screen.getByRole("menuitemradio", { name: /Unknown/ })).not.toHaveAttribute("data-disabled");
  expect(screen.getByText(/Greyed out/)).toBeInTheDocument();
});

it("switches the pair, keeps an effort the new model takes, and prices the cold re-read", async () => {
  vi.mocked(api.threads.get).mockResolvedValue(detail({ model: "claude-sonnet-5", effort: "high" }));
  vi.mocked(api.setThreadModel).mockResolvedValue({ model: "claude-opus-5", effort: "high",
    effective_model: "claude-opus-5", effective_effort: "high" });
  mount();
  await waitFor(() => expect(pill()).toHaveTextContent("Sonnet 5 · High"));
  await openPicker();
  await userEvent.click(await screen.findByRole("menuitemradio", { name: /Opus 5/ }));
  expect(api.setThreadModel).toHaveBeenCalledWith(ID, { model: "claude-opus-5", effort: "high" });
  expect(pill()).toHaveTextContent("Opus 5 · High");
  // 500K tokens re-read at Opus 5's input price with the cache-write multiplier.
  expect(await screen.findByText("The next reply re-reads ~500K tokens on Opus 5: ≈$9.38.")).toBeInTheDocument();
});

it("sends an effort-only change as the effective model plus the effort", async () => {
  vi.mocked(api.setThreadModel).mockResolvedValue({ model: "claude-sonnet-5", effort: "low",
    effective_model: "claude-sonnet-5", effective_effort: "low" });
  mount();
  await waitFor(() => expect(pill()).toHaveTextContent("Sonnet 5 · Auto"));
  await openPicker();
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "Low" }));
  // The lane had no explicit pair: picking a level pins the model it applies to.
  expect(api.setThreadModel).toHaveBeenCalledWith(ID, { model: "claude-sonnet-5", effort: "low" });
});

it("clears the pair back to the lane's default, and shows what the server refuses", async () => {
  vi.mocked(api.threads.get).mockResolvedValue(detail({ model: "claude-opus-5", effort: "high" }));
  vi.mocked(api.setThreadModel).mockResolvedValue({ model: null, effort: null,
    effective_model: "claude-sonnet-5", effective_effort: null });
  mount();
  await waitFor(() => expect(pill()).toHaveTextContent("Opus 5"));
  await openPicker();
  await userEvent.click(await screen.findByRole("menuitem", { name: "Use my default" }));
  expect(api.setThreadModel).toHaveBeenCalledWith(ID, { model: null, effort: null });
  expect(pill()).toHaveTextContent("Sonnet 5 · Auto");

  vi.mocked(api.setThreadModel).mockRejectedValue(new ApiError(400, "bad_effort", "no"));
  await userEvent.click(screen.getByRole("menuitemradio", { name: "High" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("That model does not take this effort level.");
});

it("opens when a `/model` draft asks for it, and falls back to the deploy default", async () => {
  vi.mocked(api.threads.get).mockRejectedValue(new ApiError(404, "not_found", "no such thread"));
  mount(null);
  // The thread row does not exist yet, so the pill names what a turn would run on.
  await waitFor(() => expect(pill()).toHaveTextContent("Sonnet 5 · Auto"));
  useStore.getState().setModelPickerOpen(true);
  expect(await screen.findByRole("menuitemradio", { name: /Opus 5/ })).toBeInTheDocument();
});
