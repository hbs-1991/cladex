/** Reading a drafted catalog entry before it becomes real.
 *
 * Opening the dialog calls `review`, which snapshots the draft on the server and
 * returns its executable surface together with a token bound to those exact
 * bytes. The surface is rendered in a `<pre>`, never through a markdown
 * renderer and never transformed: it is the text a human is being asked to
 * vouch for, and a renderer would quietly swallow a `#`, a `*` or a backslash
 * that changes what the entry runs. Approve posts the token back; a draft
 * rewritten in between invalidates it and the dialog reads it again. */

import { useEffect, useState } from "react";
import { TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import type { DraftReview, DraftWire } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useT } from "@/i18n";
import { draftError } from "@/lib/pluginMenu";
import { useStore } from "@/store";

type Props = { draft: DraftWire; open: boolean; onOpenChange: (open: boolean) => void };

export function DraftDialog({ draft, open, onOpenChange }: Props) {
  // The body is mounted with the dialog, so reopening reads the draft again
  // rather than offering a token for bytes that may have changed since.
  return <Dialog open={open} onOpenChange={onOpenChange}>{open && <DraftBody draft={draft} onOpenChange={onOpenChange} />}</Dialog>;
}

function DraftBody({ draft, onOpenChange }: Omit<Props, "open">) {
  const t = useT();
  const s = useStore();
  const [review, setReview] = useState<DraftReview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The read is a promise chain rather than an awaited call so that nothing sets
  // state synchronously inside the effect (`FilePreview` does the same).
  const load = () => s.reviewDraft(draft.name).then(setReview).catch((e: unknown) => setError(draftError(t, e)));
  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const approve = async () => {
    if (!review) return;
    setBusy(true);
    setError(null);
    try {
      await s.approveDraft(draft.name, review.token);
      toast(t("drafts.approvedToast", { label: draft.label }));
      onOpenChange(false);
    } catch (e) {
      setError(draftError(t, e));
      // The token no longer matches the file: drop the surface that is no longer
      // true and read it again, so Approve is never offered against bytes nobody
      // has seen.
      if (e instanceof ApiError && e.code === "stale_draft") { setReview(null); await load(); }
    } finally { setBusy(false); }
  };

  const discard = async () => {
    try { await s.discardDraft(draft.name); toast(t("drafts.discardedToast", { label: draft.label })); onOpenChange(false); }
    catch (e) { setError(draftError(t, e)); }
  };

  return (
    <DialogContent className="sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle>{t("drafts.reviewTitle", { label: draft.label })}</DialogTitle>
        <DialogDescription>{t("drafts.surfaceNote")}</DialogDescription>
      </DialogHeader>

      {review?.local_command && (
        <div className="flex gap-2 rounded-lg border border-warning-foreground/30 bg-warning p-3 text-sm text-warning-foreground">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} />
          <span>
            <span className="block font-medium">{t("drafts.localCommand")}</span>
            {t("drafts.localCommandWarning")}
          </span>
        </div>
      )}
      {error && <p role="alert" className="text-sm text-error-foreground">{error}</p>}

      <h3 className="text-sm font-medium">{t("drafts.whatWillRun")}</h3>
      {review
        ? <pre className="max-h-[45vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted p-3 font-mono text-xs leading-relaxed">{review.surface}</pre>
        : !error && <p className="text-sm text-muted-foreground">{t("drafts.reviewing")}</p>}

      <DialogFooter className="sm:justify-between">
        <Button variant="ghost" className="text-error-foreground" onClick={() => void discard()}>{t("drafts.discard")}</Button>
        <span className="flex flex-col-reverse gap-2 sm:flex-row">
          <Button variant="secondary" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
          <Button disabled={!review || busy} onClick={() => void approve()}>{busy ? t("drafts.approving") : t("drafts.approve")}</Button>
        </span>
      </DialogFooter>
    </DialogContent>
  );
}
