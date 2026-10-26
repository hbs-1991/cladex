import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { ModelWire } from "@/api/types";
import { useStore } from "@/store";
import { RoutineForm } from "./RoutineForm";

const model = (over: Partial<ModelWire>): ModelWire => ({
  id: "", label: "", window: 1_000_000, price: { in: 3, out: 15 }, cache_write: 1.25,
  efforts: ["low", "medium", "high"], default_effort: "medium", ...over });

const MODELS = [
  model({ id: "claude-sonnet-5", label: "Sonnet 5" }),
  model({ id: "claude-opus-5", label: "Opus 5", price: { in: 15, out: 75 }, efforts: ["medium", "high", "max"], default_effort: "high" }),
];

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  // "ready" keeps `loadModels` from putting a fetch on the wire in a jsdom test.
  useStore.setState({ models: MODELS, modelsStatus: "ready", deployDefault: { model: "claude-sonnet-5", effort: null } });
});

it("submits name, instructions, the chosen frequency and timeout, and shows a server error", async () => {
  const onSubmit = vi.fn(async () => { throw Object.assign(new Error("Bad schedule: unknown format"), { code: "bad_request" }); });
  render(<RoutineForm onSubmit={onSubmit} />);
  await userEvent.type(screen.getByLabelText(/Name/), "Daily brief");
  await userEvent.type(screen.getByLabelText(/Instructions/), "Put the briefing together");
  await userEvent.clear(screen.getByLabelText("Timeout, seconds"));
  await userEvent.type(screen.getByLabelText("Timeout, seconds"), "300");
  expect(screen.getByRole("combobox", { name: "Frequency" })).toHaveTextContent("Weekdays at 08:00");   // the default frequency, named
  expect(screen.queryByLabelText("Schedule")).toBeNull();                 // no raw cron field for a preset
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(onSubmit).toHaveBeenCalledWith({ name: "Daily brief", schedule: "0 8 * * 1-5", prompt: "Put the briefing together", timeout_seconds: 300,
    model: null, effort: null });
  expect(await screen.findByRole("alert")).toHaveTextContent("Bad schedule");
});

it("keeps a schedule no preset covers as a raw field, and submits it", async () => {
  const onSubmit = vi.fn(async () => {});
  render(<RoutineForm initial={{ name: "Ping", prompt: "check", schedule: "*/5 * * * *", timeout_seconds: 60 }} onSubmit={onSubmit} />);
  expect(screen.getByRole("combobox", { name: "Frequency" })).toHaveTextContent("Custom schedule…");
  const raw = screen.getByLabelText("Schedule");
  expect(raw).toHaveValue("*/5 * * * *");
  await userEvent.clear(raw);
  await userEvent.type(raw, "every 2h");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(onSubmit).toHaveBeenCalledWith({ name: "Ping", schedule: "every 2h", prompt: "check", timeout_seconds: 60, model: null, effort: null });
});

it("defaults the model to unset and submits the pair the form was given", async () => {
  const onSubmit = vi.fn(async () => {});
  render(<RoutineForm initial={{ name: "Ping", prompt: "check", schedule: "0 8 * * 1-5", model: "claude-opus-5", effort: "max" }} onSubmit={onSubmit} />);
  expect(screen.getByRole("combobox", { name: "Model" })).toHaveTextContent("Opus 5");
  expect(screen.getByRole("combobox", { name: "Effort" })).toHaveTextContent("Max");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ model: "claude-opus-5", effort: "max" }));
});

it("drops an effort the newly chosen model does not take, and names its Auto", async () => {
  const onSubmit = vi.fn(async () => {});
  render(<RoutineForm initial={{ name: "Ping", prompt: "check", schedule: "0 8 * * 1-5", model: "claude-opus-5", effort: "max" }} onSubmit={onSubmit} />);
  await userEvent.click(screen.getByRole("combobox", { name: "Model" }));
  await userEvent.click(await screen.findByRole("option", { name: "Sonnet 5" }));
  // Sonnet 5 has no `max`, so the pair falls back to Auto — named with the
  // model's own default rather than left as a level the server would refuse.
  expect(screen.getByRole("combobox", { name: "Effort" })).toHaveTextContent("Auto (Medium)");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ model: "claude-sonnet-5", effort: null }));
});

it("does not let an empty form be saved", () => {
  render(<RoutineForm onSubmit={vi.fn(async () => {})} />);
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
});

it("offers an on-demand frequency and submits it as `manual`", async () => {
  const onSubmit = vi.fn(async () => {});
  render(<RoutineForm initial={{ name: "Publisher", prompt: "publish" }} onSubmit={onSubmit} />);
  await userEvent.click(screen.getByRole("combobox", { name: "Frequency" }));
  await userEvent.click(await screen.findByRole("option", { name: "On demand" }));
  expect(screen.queryByLabelText("Schedule")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ schedule: "manual" }));
});

it("shows an on-demand routine as such when editing it", () => {
  render(<RoutineForm initial={{ name: "Publisher", prompt: "publish", schedule: "manual" }} onSubmit={vi.fn(async () => {})} />);
  expect(screen.getByRole("combobox", { name: "Frequency" })).toHaveTextContent("On demand");
});
