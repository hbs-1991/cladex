/** "Pending review" — the queue of catalog entries the agent drafted, above the
 * grid of the tab that owns their kind (`mcp` on Connectors, `plugin` on
 * Plugins).
 *
 * It renders nothing in the normal case, so neither tab changes shape until
 * something is actually waiting. Approving is owner-only, so a plain user is
 * shown nothing at all rather than a queue with no buttons. */

import { useEffect, useState } from "react";
import { toast } from "sonner";
import type { DraftKind, DraftWire } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { firstSentence } from "@/lib/connectorMenu";
import { draftError } from "@/lib/pluginMenu";
import { useStore } from "@/store";
import { ConfirmDialog } from "./AskDialog";
import { DraftDialog } from "./DraftDialog";

/** `<stem>.yaml` — a rejected draft is named by its file, an approval by name. */
const stem = (file: string) => file.replace(/\.[^.]+$/, "");

export function PendingDrafts({ kind }: { kind: DraftKind }) {
  const t = useT();
  const s = useStore();
  const owner = s.me?.role === "owner";
  const [reviewing, setReviewing] = useState<DraftWire | null>(null);
  const [discarding, setDiscarding] = useState<string | null>(null);
  useEffect(() => {
    // The section is auxiliary: a drafts route that fails leaves it hidden
    // rather than taking the tab it sits on down with it.
    if (owner) void s.loadDrafts().catch(() => {});
  }, [owner]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!owner) return null;
  const mine = s.drafts.filter((d) => d.kind === kind);
  // A draft that no longer parses has no kind to file it under, so it is listed
  // on both tabs — the alternative is an operator waiting forever for an
  // approval that cannot happen.
  const rejected = s.rejectedDrafts;
  if (mine.length === 0 && rejected.length === 0) return null;

  const discard = (name: string) => {
    void (async () => {
      try { await s.discardDraft(name); toast(t("drafts.discardedToast", { label: name })); }
      catch (e) { toast.error(draftError(t, e)); }
    })();
  };

  return (
    <section className="mb-8">
      <h2 className="mb-1 text-sm font-medium">{t("drafts.pendingReview")}</h2>
      <p className="mb-3 text-sm text-muted-foreground">{t("drafts.pendingLead")}</p>
      <ul className="grid gap-3">
        {mine.map((d) => (
          <li key={d.name} className="flex items-center gap-3 rounded-xl border border-dashed border-border bg-card p-3">
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-1.5">
                <span className="truncate font-medium">{d.label}</span>
                <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">{t(d.kind === "plugin" ? "drafts.kindPlugin" : "drafts.kindMcp")}</Badge>
              </span>
              <span className="line-clamp-2 block text-sm text-muted-foreground">{firstSentence(d.description)}</span>
            </span>
            <Button size="sm" onClick={() => setReviewing(d)}>{t("drafts.review")}</Button>
          </li>
        ))}
      </ul>

      {rejected.length > 0 && (
        <div className="mt-4 rounded-xl border border-border p-3">
          <p className="text-sm font-medium">{t("drafts.rejected")}</p>
          <p className="mt-0.5 text-sm text-muted-foreground">{t("drafts.rejectedLead")}</p>
          <ul className="mt-2 grid gap-1">
            {rejected.map((r) => (
              <li key={r.file} className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="min-w-0 flex-1 truncate">{t("drafts.rejectedReason", { file: r.file, reason: r.reason })}</span>
                <Button variant="ghost" size="sm" onClick={() => setDiscarding(stem(r.file))}>{t("drafts.discard")}</Button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {reviewing && <DraftDialog draft={reviewing} open onOpenChange={(o) => { if (!o) setReviewing(null); }} />}
      {discarding && (
        <ConfirmDialog open onOpenChange={(o) => { if (!o) setDiscarding(null); }}
          title={t("drafts.discard")} description={t("drafts.discardConfirm", { name: discarding })}
          confirmLabel={t("drafts.discard")} destructive onConfirm={() => discard(discarding)} />
      )}
    </section>
  );
}
