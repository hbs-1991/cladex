import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ASK_TOOL, FILE_TOOL } from "@/runtime/fold";
import { Thinking } from "./Thinking";

type Part = { type: string; toolName?: string; text?: string; status?: { type: string } };
const parts: Part[] = [];
vi.mock("@assistant-ui/react", () => ({
  useAuiState: (select: (s: unknown) => unknown) =>
    select({ message: { parts }, thread: { messages: [{ content: thoughts }] } }),
}));

/** What the model has been thinking in this thread, which is where the line takes
 *  its language from when the turn has produced nothing of its own yet. */
let thoughts: Part[] = [];

const only = (...p: Part[]) => { parts.length = 0; parts.push(...p); thoughts = []; };
const running = { type: "running" };
const complete = { type: "complete" };

it("names the wait while nothing has arrived yet", () => {
  only();
  render(<Thinking />);
  expect(screen.getByRole("status")).toHaveTextContent("Thinking");
});

it("borrows the language the model has been thinking in, so the word does not flip", () => {
  only();
  thoughts = [{ type: "reasoning", text: "Сначала посмотрю на историю треда." } as Part];
  render(<Thinking />);
  expect(screen.getByRole("status")).toHaveTextContent("Думаю");
});

it("stays quiet when a step group or a card of its own already says it", () => {
  only({ type: "tool-call", toolName: "Bash", status: complete });   // the group header carries the gap
  const { unmount } = render(<Thinking />);
  expect(screen.queryByRole("status")).toBeNull();
  unmount();

  only({ type: "tool-call", toolName: ASK_TOOL, status: running });  // waiting for the human, not thinking
  render(<Thinking />);
  expect(screen.queryByRole("status")).toBeNull();
});

it("speaks again after a delivered file, which ends its group", () => {
  only({ type: "tool-call", toolName: FILE_TOOL, status: complete });
  render(<Thinking />);
  expect(screen.getByRole("status")).toHaveTextContent("Thinking");
});
