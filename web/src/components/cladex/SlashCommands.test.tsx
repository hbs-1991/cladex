import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const draft = { text: "" };
const setText = vi.fn();
vi.mock("@assistant-ui/react", () => ({
  useAuiState: (sel: (s: { composer: { text: string } }) => unknown) => sel({ composer: { text: draft.text } }),
  useAui: () => ({ composer: { setText } }),
}));
vi.mock("@/api/client", () => ({ api: { commands: { list: vi.fn() } } }));

import { api } from "@/api/client";
import { useStore } from "@/store";
import { useSlashCommands } from "./SlashCommands";

const wire = [
  { name: "compact", description: "", source: "sdk" as const },
  { name: "wiki-ingest", description: "File a source", source: "skill" as const },
  { name: "tools:deploy", description: "Deploy", source: "plugin" as const },
];
/** What the CLI actually reports: `model` and `effort` beside `models`. */
const withModelCommands = [
  { name: "model", description: "Set the model", source: "sdk" as const },
  { name: "effort", description: "Set the effort", source: "sdk" as const },
  { name: "models", description: "List models", source: "sdk" as const },
];

function Harness() {
  const slash = useSlashCommands();
  return (
    <div>
      {slash.menu}
      <textarea aria-label="in" onKeyDown={slash.onKeyDown} />
    </div>
  );
}

const input = () => screen.getByLabelText("in");
const options = () => screen.queryAllByRole("option").map((o) => o.textContent);

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  draft.text = "";
  setText.mockClear();
  vi.mocked(api.commands.list).mockReset().mockResolvedValue({ commands: wire });
});

it("fetches the list on the first slash, once, and narrows it as the user types", async () => {
  const { rerender } = render(<Harness />);
  expect(api.commands.list).not.toHaveBeenCalled();
  expect(options()).toEqual([]);
  draft.text = "/";
  rerender(<Harness />);
  await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(3));
  expect(screen.getByText("Built-in command")).toBeInTheDocument();     // an empty description names the source
  draft.text = "/wi";
  rerender(<Harness />);
  expect(options()).toEqual(["/wiki-ingestFile a source↵"]);
  draft.text = "/wiki-ingest now";
  rerender(<Harness />);
  expect(options()).toEqual([]);
  expect(api.commands.list).toHaveBeenCalledTimes(1);
});

it("arrows move the highlight, Enter and Tab insert `/name `, Escape closes until the draft changes", async () => {
  draft.text = "/";
  const { rerender } = render(<Harness />);
  await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(3));
  expect(screen.getByRole("option", { name: /compact/ })).toHaveAttribute("data-active", "true");
  expect(fireEvent.keyDown(input(), { key: "ArrowDown" })).toBe(false);          // consumed
  expect(screen.getByRole("option", { name: /wiki-ingest/ })).toHaveAttribute("data-active", "true");
  expect(fireEvent.keyDown(input(), { key: "Enter" })).toBe(false);              // not a send
  expect(setText).toHaveBeenLastCalledWith("/wiki-ingest ");
  fireEvent.keyDown(input(), { key: "ArrowUp" });
  fireEvent.keyDown(input(), { key: "ArrowUp" });                                // wraps to the last row
  expect(screen.getByRole("option", { name: /tools:deploy/ })).toHaveAttribute("data-active", "true");
  fireEvent.keyDown(input(), { key: "Tab" });
  expect(setText).toHaveBeenLastCalledWith("/tools:deploy ");
  expect(fireEvent.keyDown(input(), { key: "Escape" })).toBe(false);
  expect(options()).toEqual([]);
  draft.text = "/c";
  rerender(<Harness />);
  expect(options()).toEqual(["/compactBuilt-in command↵"]);
});

it("a command typed out in full lets Enter send it", async () => {
  draft.text = "/Compact";
  render(<Harness />);
  await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(1));
  expect(fireEvent.keyDown(input(), { key: "Enter" })).toBe(true);               // left to the composer
  expect(setText).not.toHaveBeenCalled();
});

it("a click inserts the command", async () => {
  draft.text = "/t";
  render(<Harness />);
  await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(1));
  fireEvent.click(screen.getByRole("option", { name: /tools:deploy/ }));
  expect(setText).toHaveBeenCalledWith("/tools:deploy ");
});

it("never offers /model or /effort, and Enter on one opens the picker instead of sending", async () => {
  vi.mocked(api.commands.list).mockResolvedValue({ commands: withModelCommands });
  draft.text = "/";
  const { rerender } = render(<Harness />);
  // The CLI reports all three; only the one that is a real turn is offered.
  await waitFor(() => expect(options()).toEqual(["/modelsList models↵"]));

  draft.text = "/model";
  rerender(<Harness />);
  expect(fireEvent.keyDown(input(), { key: "Enter" })).toBe(false);          // consumed: never sent
  expect(useStore.getState().modelPickerOpen).toBe(true);
  expect(setText).not.toHaveBeenCalled();                                    // and the draft is left alone

  // `/models` is an ordinary command: typed out in full, Enter sends it.
  useStore.getState().setModelPickerOpen(false);
  draft.text = "/models";
  rerender(<Harness />);
  expect(fireEvent.keyDown(input(), { key: "Enter" })).toBe(true);
  expect(useStore.getState().modelPickerOpen).toBe(false);
});

it("a failed fetch leaves the menu empty and is not retried on every keystroke", async () => {
  vi.mocked(api.commands.list).mockRejectedValue(new Error("404"));
  draft.text = "/";
  const { rerender } = render(<Harness />);
  await waitFor(() => expect(useStore.getState().commandsStatus).toBe("ready"));
  expect(options()).toEqual([]);
  draft.text = "/c";
  rerender(<Harness />);
  expect(fireEvent.keyDown(input(), { key: "Enter" })).toBe(true);
  expect(api.commands.list).toHaveBeenCalledTimes(1);
});
