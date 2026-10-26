import { expect, it } from "vitest";
import { BookOpen, FileText, Terminal, Wrench } from "lucide-react";
import { groupHead, runLabel, toItems } from "./StepGroup";

const tool = (toolName: string, status: "running" | "complete", args: Record<string, unknown> = {}) =>
  ({ type: "tool-call", toolName, args, toolCallId: `${toolName}-${Math.random()}`, status: { type: status } });
const thought = (text: string) => ({ type: "reasoning", text, status: { type: "complete" } });

it("labels a running group with the current step and a finished one with the time it took", () => {
  expect(groupHead([tool("WebSearch", "running", { query: "x" })] as never, true, undefined, "en").text).toBe("Searching the web");
  expect(groupHead([thought("hm")] as never, true, undefined, "en").text).toBe("Thinking");
  const done = [tool("Read", "complete"), tool("Bash", "complete")];
  expect(groupHead(done as never, false, 661_000, "en").text).toBe("Worked 11 min 1 s");
  expect(groupHead(done as never, false, undefined, "en").text).toBe("Worked 2 steps");        // no timing yet: count
  expect(groupHead([done[0]!] as never, false, undefined, "en").text).toBe("Worked 1 step");
  expect(groupHead([thought("hm")] as never, false, undefined, "en").text).toBe("Thought");
  expect(groupHead(done as never, false, 661_000, "ru").text).toBe("Работал 11 мин 1 с");
  expect(groupHead(new Array(5).fill(done[0]) as never, false, undefined, "ru").text).toBe("Работал 5 шагов");
  expect(groupHead([thought("hm")] as never, true, undefined, "ru").text).toBe("Думаю");
  // The gap between a finished call and the next one: the model is thinking, and
  // the header must not go on naming the call that already returned.
  expect(groupHead(done as never, true, undefined, "en").text).toBe("Thinking");
});

it("lends the header the running tool's own icon, and the spinner only between calls", () => {
  // The mark says which work is running, not merely that something is.
  expect(groupHead([tool("Read", "running", { file_path: "a.md" })] as never, true, undefined, "en").icon).toBe(FileText);
  expect(groupHead([tool("Bash", "running", { command: "ls" })] as never, true, undefined, "en").icon).toBe(Terminal);
  expect(groupHead([tool("Skill", "running")] as never, true, undefined, "en").icon).toBe(BookOpen);
  // An unknown tool still gets a mark of its own rather than falling back to the spinner.
  expect(groupHead([tool("ToolSearch", "running")] as never, true, undefined, "en")).toEqual({ icon: Wrench, text: "Calling ToolSearch" });
  // No call in flight — the model is between steps, and `null` asks for the spinner.
  expect(groupHead([thought("hm")] as never, true, undefined, "en").icon).toBeNull();
  expect(groupHead([tool("Read", "complete")] as never, true, undefined, "en").icon).toBeNull();
  // A settled header carries no mark at all.
  expect(groupHead([tool("Read", "complete")] as never, false, 1_000, "en").icon).toBeNull();
});

it("collapses a run of tool calls into one deduplicated line, Codex-style", () => {
  const { text, running } = runLabel([tool("Bash", "complete"), tool("Bash", "complete"), tool("WebSearch", "complete")] as never, "ru");
  expect(text).toBe("Выполнил команду, искал в интернете");
  expect(running).toBe(false);
  expect(runLabel([tool("Read", "complete"), tool("Grep", "running")] as never, "ru")).toEqual({ text: "Прочитал файл, ищу в файлах", running: true });
  expect(runLabel([tool("Bash", "complete"), tool("WebSearch", "complete")] as never, "en").text).toBe("Ran command, searched the web");
  expect(groupHead([tool("WebSearch", "running")] as never, true, undefined, "en").text).toBe("Searching the web");
});

it("keeps reasoning as paragraphs between the runs, in the order the agent produced them", () => {
  const items = toItems([thought("plan"), tool("Read", "complete"), tool("Bash", "complete"), thought("ok"), tool("Edit", "complete")] as never);
  expect(items.map((i) => (i.kind === "run" ? `run:${i.calls.length}` : "reasoning"))).toEqual(["reasoning", "run:2", "reasoning", "run:1"]);
});
