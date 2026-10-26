import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { ComposerVoiceControl } from "./composer-voice";

/** The slice of runtime state the control reads; mutated per test and re-rendered. */
const state = {
  thread: { capabilities: { dictation: true } },
  composer: { dictation: undefined as { status: { type: string } } | undefined },
};

vi.mock("@assistant-ui/react", () => ({
  useAuiState: (select: (s: unknown) => unknown) => select(state),
  AuiIf: ({ condition, children }: { condition: (s: unknown) => boolean; children: ReactNode }) =>
    condition(state) ? <>{children}</> : null,
  // `asChild` primitives hand their child a click handler composed with the child's own;
  // here the child's own handler is all that matters, so the child renders as is.
  ComposerPrimitive: {
    Dictate: ({ children }: { children: ReactNode }) => <>{children}</>,
    StopDictation: ({ children }: { children: ReactNode }) => <>{children}</>,
  },
}));

beforeEach(() => {
  vi.useFakeTimers();
  state.thread.capabilities.dictation = true;
  state.composer.dictation = undefined;
});
afterEach(() => vi.useRealTimers());

it("offers the mic while idle and nothing else", () => {
  render(<ComposerVoiceControl />);
  expect(screen.getByRole("button", { name: "Start dictation" })).toBeInTheDocument();
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.queryByRole("button", { name: "Stop dictation" })).toBeNull();
});

it("renders nothing without a dictation adapter", () => {
  state.thread.capabilities.dictation = false;
  render(<ComposerVoiceControl />);
  expect(screen.queryByRole("button")).toBeNull();
});

it("listens: the waveform, a timer that counts, and the stop button", () => {
  state.composer.dictation = { status: { type: "running" } };
  render(<ComposerVoiceControl />);
  const status = screen.getByRole("status", { name: "Listening" });
  expect(status).toHaveAttribute("data-recording", "true");
  expect(status.querySelectorAll(".aui-composer-voice-bar")).toHaveLength(14);
  expect(status).toHaveTextContent("0:00");
  act(() => vi.advanceTimersByTime(3000));
  expect(status).toHaveTextContent("0:03");
  expect(screen.getByRole("button", { name: "Stop dictation" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Start dictation" })).toBeNull();
});

it("does not count while the browser is still asking for the microphone", () => {
  state.composer.dictation = { status: { type: "starting" } };
  render(<ComposerVoiceControl />);
  act(() => vi.advanceTimersByTime(3000));
  expect(screen.getByRole("status", { name: "Listening" })).toHaveTextContent("0:00");
});

it("settles into transcribing after stop, then returns to the mic when the session ends", () => {
  state.composer.dictation = { status: { type: "running" } };
  const { rerender } = render(<ComposerVoiceControl />);
  act(() => vi.advanceTimersByTime(2000));
  fireEvent.click(screen.getByRole("button", { name: "Stop dictation" }));

  const status = screen.getByRole("status", { name: "Transcribing…" });
  expect(status).not.toHaveAttribute("data-recording");
  expect(status).not.toHaveTextContent("0:02");
  expect(status).toHaveTextContent("Transcribing…");
  // still the stop button: the runtime keeps the session until the transcript lands
  expect(screen.getByRole("button", { name: "Stop dictation" })).toBeInTheDocument();

  state.composer.dictation = undefined;
  rerender(<ComposerVoiceControl />);
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.getByRole("button", { name: "Start dictation" })).toBeInTheDocument();

  // a new session starts clean: recording again, timer from zero
  state.composer.dictation = { status: { type: "running" } };
  rerender(<ComposerVoiceControl />);
  expect(screen.getByRole("status", { name: "Listening" })).toHaveTextContent("0:00");
});
