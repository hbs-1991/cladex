import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";

type Args = { question: string; options: string[] };
type Result = { answer: string | null; source: "user" | "timeout" | "cancelled" } | undefined;

export function AskCard({ args, result, addResult }: { args: Args; result?: Result; addResult: (r: { answer: string }) => void }) {
  const t = useT();
  const [picked, setPicked] = useState<string | null>(null);
  const settled = result !== undefined;
  return (
    <div className="my-2 rounded-lg border bg-card p-4">
      <p className="mb-3 text-base">{args.question}</p>
      {settled ? (
        <p className="text-sm text-muted-foreground">
          {result.source === "user" ? t("ask.chosen", { answer: result.answer ?? "" }) :
           result.source === "timeout" ? t("ask.timedOut") : t("ask.cancelled")}
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {args.options.map((o) => (
            <Button key={o} variant={picked === o ? "default" : "secondary"} size="sm" disabled={picked !== null}
              className="h-auto min-h-8 max-w-full py-1.5 text-left whitespace-normal"   // a long option wraps instead of leaving the card
              onClick={() => { setPicked(o); addResult({ answer: o }); }}>
              {o}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}
