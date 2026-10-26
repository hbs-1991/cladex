/**
 * One tool call, as a quiet line: icon, what the agent did, the target, how
 * long it took. Click to unfold the arguments and the result. Used for the
 * detail view inside a StepGroup run and as the toolkit fallback renderer.
 */

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { Working } from "@/components/cladex/Working";
import { t, useLocale } from "@/i18n";
import { labelFor, type Lang } from "@/toolkit/labels";
import { cn } from "@/lib/utils";

/** The read-only slice of a tool-call part: enough for a row, so a StepGroup can
 * hand it a plain part state without the toolkit's addResult/resume callbacks. */
export type ToolRowProps = Pick<ToolCallMessagePartProps, "toolName" | "args" | "result" | "status" | "isError" | "artifact">
  & { lang?: Lang };

export function ToolRow({ toolName, args, result, status, isError, artifact, lang: langProp }: ToolRowProps) {
  const uiLocale = useLocale();          // subscribe: a standalone row must re-render on a language switch
  const lang = langProp ?? uiLocale;
  const [open, setOpen] = useState(false);
  const done = status.type !== "running";
  const { icon: Icon, text, target } = labelFor(toolName, args as Record<string, unknown>, done, lang);
  const ms = (artifact as { duration_ms?: number } | undefined)?.duration_ms;
  return (
    <div className="text-xs">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="flex w-full items-center gap-2 py-0.5 text-left text-muted-foreground transition-colors hover:text-foreground">
        <Icon className="size-3.5 shrink-0" strokeWidth={1.75} />
        <span className={cn("shrink-0 whitespace-nowrap", isError && "text-error-foreground")}>{text}</span>
        {target && <span className="truncate font-mono text-[0.7rem]">{target}</span>}
        <span className="ml-auto flex items-center gap-2 tabular-nums">
          {status.type === "running" ? <Working /> : null}
          {ms !== undefined && done ? t("steps.seconds", { n: (ms / 1000).toFixed(1) }, lang) : null}
          <ChevronRight className={cn("size-3 transition-transform", open && "rotate-90")} />
        </span>
      </button>
      {open && (
        <div className="my-1 ml-6 space-y-2">
          <pre className="max-h-64 overflow-auto rounded-md bg-muted p-3 font-mono text-code">{JSON.stringify(args, null, 2)}</pre>
          {result !== undefined && (
            <pre className={cn("max-h-64 overflow-auto rounded-md bg-muted p-3 font-mono text-code whitespace-pre-wrap",
              isError && "text-error-foreground")}>{typeof result === "string" ? result : JSON.stringify(result, null, 2)}</pre>
          )}
        </div>
      )}
    </div>
  );
}
