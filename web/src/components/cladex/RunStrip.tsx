/** A row of coloured squares, newest first, summarising a routine's recent runs. */

import type { RunWire } from "@/api/types";
import { useT } from "@/i18n";
import { fmtDateTime } from "@/lib/dates";

const COLOR: Record<string, string> = { ok: "bg-success-foreground", error: "bg-error-foreground", timeout: "bg-warning-foreground", missed: "bg-border" };
const STATUSES = ["ok", "error", "timeout", "missed"] as const;
const known = (s: string): s is (typeof STATUSES)[number] => (STATUSES as readonly string[]).includes(s);

export function RunStrip({ runs }: { runs: RunWire[] }) {
  const t = useT();
  /** A status the UI knows, in the interface language; anything else verbatim. */
  const status = (s: string) => (known(s) ? t(`routines.runStatus.${s}`) : s);
  const counts = runs.reduce<Record<string, number>>((a, r) => ({ ...a, [r.status]: (a[r.status] ?? 0) + 1 }), {});
  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <span>{t("routines.lastRuns")}</span>
      <span className="flex gap-1">{[...runs].reverse().map((r, i) => <i key={i} title={`${fmtDateTime(r.ts)} · ${status(r.status)}`} className={`inline-block size-2.5 rounded-sm ${COLOR[r.status] ?? "bg-border"}`} />)}</span>
      <span>{Object.entries(counts).map(([k, n]) => `${n} ${status(k)}`).join(" · ")}</span>
    </div>
  );
}
