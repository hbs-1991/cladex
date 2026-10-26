/**
 * The agent's chain of thought, Codex-style: a quiet "Worked 11 min 1 s"
 * header over a plain flow of the model's own reasoning (verbatim, in whatever
 * language it thinks in) and one muted line per run of tool calls. No box, no
 * border around it — the thread text before and after stays the focus.
 */

import { useMemo, useState, type ReactNode } from "react";
import { ChevronDownIcon, LoaderIcon, type LucideIcon } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useAuiState, type PartState } from "@assistant-ui/react";
import { ImageGeneration } from "@/components/assistant-ui/elements/image-generation";
import type { ThreadGroupPart } from "@/components/assistant-ui/elements/thread.aui";
import { ToolRow } from "@/components/cladex/ToolRow";
import { Working } from "@/components/cladex/Working";
import { currentLocale, t, type Locale } from "@/i18n";
import { useStore } from "@/store";
import { fmtDuration } from "@/lib/dates";
import { imagePrompt, labelFor, type Lang } from "@/toolkit/labels";
import { useModelLang } from "@/toolkit/lang";
import { cn } from "@/lib/utils";

export type ToolStep = Extract<PartState, { type: "tool-call" }>;
export type ReasoningStep = Extract<PartState, { type: "reasoning" }>;
type Step = ToolStep | ReasoningStep;

/** Consecutive tool calls collapse into one line; reasoning stays a paragraph. */
type Item = { kind: "reasoning"; part: ReasoningStep } | { kind: "run"; calls: ToolStep[] };

const lower = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

/** "Ran command, searched the web" — one label for a run of calls, deduplicated in order. */
// eslint-disable-next-line react-refresh/only-export-components -- pure and unit-tested next to the component that uses it
export function runLabel(calls: ToolStep[], lang: Lang = currentLocale()): { text: string; running: boolean } {
  const running = calls.some((c) => c.status.type === "running");
  const texts: string[] = [];
  for (const c of calls) {
    const done = c.status.type !== "running";
    const t = labelFor(c.toolName, c.args as Record<string, unknown>, done, lang).text;
    if (!texts.includes(t)) texts.push(t);
  }
  return { text: texts.map((t, i) => (i === 0 ? t : lower(t))).join(", "), running };
}

/**
 * The header's mark and words: what the agent is doing right now, or how long it
 * worked.
 *
 * A call in flight lends the header its own icon, so the mark says *which* work
 * is running and not merely that something is. `null` asks the caller for the
 * spinner instead, and that is now a state of its own rather than decoration:
 * turning means the model is between calls and has not picked a tool yet.
 */
// eslint-disable-next-line react-refresh/only-export-components -- pure and unit-tested next to the component that uses it
export function groupHead(parts: Step[], running: boolean, durationMs?: number, lang: Lang = currentLocale()): { icon: LucideIcon | null; text: string } {
  const locale: Locale = lang;
  if (running) {
    // A call in flight names the wait — and it outranks a thought, which is
    // "running" whenever it is the last part of a running message. A page opened
    // mid-turn replays the not-yet-flushed reasoning after the pending call, and
    // the header used to read "Thinking" through the whole fetch because of it.
    // Between a finished call and the next one nothing is in flight and the model
    // is indeed thinking; naming the call that already returned would be a lie.
    const call = parts.findLast((p) => p.type === "tool-call" && p.status.type === "running");
    if (call?.type === "tool-call") {
      const { icon, text } = labelFor(call.toolName, call.args as Record<string, unknown>, false, lang);
      return { icon, text };
    }
    return { icon: null, text: t("steps.thinking", {}, locale) };
  }
  if (durationMs !== undefined) return { icon: null, text: t("steps.workedFor", { duration: fmtDuration(durationMs, locale) }, locale) };
  const n = parts.filter((p) => p.type === "tool-call").length;
  return { icon: null, text: n ? t("steps.workedSteps", { n }, locale) : t("steps.thought", {}, locale) };
}

// eslint-disable-next-line react-refresh/only-export-components -- pure and unit-tested next to the component that uses it
export function toItems(parts: Step[]): Item[] {
  const items: Item[] = [];
  for (const p of parts) {
    if (p.type === "reasoning") { items.push({ kind: "reasoning", part: p }); continue; }
    const last = items[items.length - 1];
    if (last?.kind === "run") last.calls.push(p);
    else items.push({ kind: "run", calls: [p] });
  }
  return items;
}

const THOUGHT = [
  "text-sm leading-relaxed text-foreground break-words",
  "[&>*:first-child]:mt-0 [&>*:last-child]:mb-0 [&_p]:my-2",
  "[&_code]:rounded-sm [&_code]:bg-muted [&_code]:px-1 [&_code]:py-px [&_code]:font-mono [&_code]:text-[0.85em]",
  "[&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5",
].join(" ");

function Thought({ part }: { part: ReasoningStep }) {
  return (
    <div data-slot="step-thought" className={THOUGHT}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{part.text}</ReactMarkdown>
    </div>
  );
}

function Run({ calls, lang }: { calls: ToolStep[]; lang: Lang }) {
  const [open, setOpen] = useState(false);
  const { text, running } = runLabel(calls, lang);
  // A generation runs for tens of seconds, so the frame is held for it while it
  // draws. It goes away when the call ends: the picture itself arrives as a file,
  // and this element never shows one.
  const drawing = calls.find((c) => c.status.type === "running"
    && imagePrompt(c.toolName, c.args as Record<string, unknown>) !== null);
  const last = calls[calls.length - 1]!;
  const { icon: Icon } = labelFor(last.toolName, last.args as Record<string, unknown>, !running, lang);
  const failed = calls.some((c) => c.isError);
  return (
    <div data-slot="step-run">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className={cn("flex items-center gap-2 py-0.5 text-left text-xs text-muted-foreground transition-colors hover:text-foreground",
          failed && "text-error-foreground")}>
        <Icon className="size-3.5 shrink-0" strokeWidth={1.75} />
        {/* The header above already shimmers this same label; here the dot alone
            says which run of the group is the live one. */}
        <span className="whitespace-nowrap">{text}</span>
        {running && <Working />}
      </button>
      {drawing && (
        <ImageGeneration className="mt-2" generating
          prompt={imagePrompt(drawing.toolName, drawing.args as Record<string, unknown>) ?? ""}
          label={t("steps.drawing", {}, lang)} />
      )}
      {open && (
        <div className="mt-1 ml-1 flex flex-col gap-0.5 border-l pl-3">
          {calls.map((c) => <ToolRow key={c.toolCallId} {...c} lang={lang} />)}
        </div>
      )}
    </div>
  );
}

export function StepGroup({ group }: { group: ThreadGroupPart; children?: ReactNode }) {
  // Select stable references and narrow outside the selector: `useAuiState`
  // compares snapshots with `Object.is`, so a fresh array from the selector
  // would re-render on every store tick.
  const messageParts = useAuiState((s) => s.message.parts);
  const turnRunning = useAuiState((s) => s.message.status?.type === "running");
  // A group's own status goes complete the moment its last call returns, which
  // is precisely when the model starts thinking about the next one. Held that
  // way, the group would collapse and re-open on every gap of the turn and the
  // header would read "Worked 3 steps" while work was still going on. The group
  // that ends the message keeps running as long as the turn does; once anything
  // follows it — the answer, a file card — it is settled for good.
  const owns = group.indices[group.indices.length - 1] === messageParts.length - 1;
  const running = group.status.type === "running" || (turnRunning && owns);
  const durationMs = useAuiState((s) => (s.message.metadata?.custom as { duration_ms?: number } | undefined)?.duration_ms);
  const parts = useMemo(
    () => group.indices.map((i) => messageParts[i]).filter((p): p is Step => p?.type === "tool-call" || p?.type === "reasoning"),
    [messageParts, group.indices],
  );
  const items = useMemo(() => toItems(parts), [parts]);
  // Tool labels follow the language the model thinks in: every reasoning part of
  // this message votes, so a group of tools with no thought of its own still
  // matches the thoughts around it.
  const own = useMemo(
    () => messageParts.filter((p) => p.type === "reasoning").map((p) => p.text).join(" "),
    [messageParts],
  );
  const lang = useModelLang(own);
  // Open while this group is the live one, closed once it is history — unless the
  // reader has said otherwise for this message. The choice is the message's, not
  // this group's: a turn opens a new group after every text block it writes, and
  // closing the thoughts of a working agent has to mean all of them, or the next
  // group pops open a second later and the thoughts cannot be closed at all. It
  // lapses when the turn ends, so the group still collapses on its own.
  const messageId = useAuiState((s) => s.message.id);
  const choice = useStore((s) => s.steps[messageId]);
  const setSteps = useStore((s) => s.setSteps);
  const open = choice && choice.running === running ? choice.open : running;
  const toggle = () => setSteps(messageId, { open: !open, running });
  // The mark is the running tool's own icon where there is one; the spinner is
  // what "no tool picked yet" looks like. Only one of the two is ever on screen,
  // so the head still carries a single mark at a single tempo (DESIGN.md §5).
  const { icon: HeadIcon, text } = groupHead(parts, running, durationMs, lang);
  return (
    <div data-slot="step-group" className="my-2">
      <button type="button" onClick={toggle} aria-expanded={open}
        className="flex items-center gap-1.5 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground">
        {running && (HeadIcon
          ? <HeadIcon className="size-3 shrink-0" strokeWidth={1.75} />
          : <LoaderIcon className="size-3 shrink-0 animate-spin [animation-duration:var(--cl-motion-loop)]" />)}
        <span className={cn(running && "shimmer motion-reduce:animate-none")}>{text}</span>
        <ChevronDownIcon className={cn("size-3 shrink-0 transition-transform", !open && "-rotate-90")} />
      </button>
      {open && (
        <div data-slot="step-group-content" className="mt-1 flex flex-col gap-3 border-t pt-3" aria-busy={running}>
          {items.map((it, i) => it.kind === "reasoning"
            ? <Thought key={`r${i}`} part={it.part} />
            : <Run key={it.calls[0]!.toolCallId} calls={it.calls} lang={lang} />)}
        </div>
      )}
    </div>
  );
}
