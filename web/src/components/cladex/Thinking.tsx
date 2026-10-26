/**
 * The header of a step group that does not exist yet.
 *
 * A turn starts, and for a second or ten there is nothing to show: no thought
 * has streamed, no tool has been called. The same gap opens again between a
 * finished tool call and whatever the model decides next. This line fills both
 * with what the group header will say once the first part lands — the same
 * spinner, the same shimmering word, the same tempo — so the transition into a
 * real step group is a change of text, not a change of shape.
 *
 * It renders nothing when a step group is already the tail of the message: the
 * group's own header carries the state there (StepGroup.tsx), and two marks for
 * one running turn is exactly what DESIGN.md §5 forbids.
 */

import { LoaderIcon } from "lucide-react";
import { useAuiState } from "@assistant-ui/react";
import { t } from "@/i18n";
import { useModelLang } from "@/toolkit/lang";
import { ASK_TOOL, FILE_TOOL } from "@/runtime/fold";

/** Tool calls that render as cards of their own, outside any step group. */
const STANDALONE = new Set<string>([ASK_TOOL, FILE_TOOL]);

export function Thinking() {
  const lang = useModelLang();
  const mine = useAuiState((s) => {
    const last = s.message.parts[s.message.parts.length - 1];
    if (!last) return true;                                                   // nothing yet — the wait is ours to name
    if (last.type === "reasoning") return false;                              // still streaming a thought
    if (last.type === "tool-call" && !STANDALONE.has(last.toolName)) return false;  // a step group heads the wait
    return last.status?.type !== "running";                                   // a card of its own: it shows its own state
  });
  if (!mine) return null;
  return (
    <span data-slot="thinking" role="status"
      className="flex items-center gap-1.5 py-1 text-xs text-muted-foreground">
      <LoaderIcon className="size-3 shrink-0 animate-spin [animation-duration:var(--cl-motion-loop)]" />
      <span className="shimmer motion-reduce:animate-none">{t("steps.thinking", {}, lang)}</span>
    </span>
  );
}
