/** "Approve this week's themes" — the owner's weekly pass over the plan.
 *
 * It acts on ONE week: the visible one in the week view, the current one in the
 * month and the agenda (`themeWeek`). The confirmation names the week and counts
 * the ideas it would reach, read fresh for that week rather than taken from the
 * grid — the month on screen may not hold all of the current week. The count is
 * the ideas with a time still ahead; the server decides, and the toast says how
 * many it actually approved.
 *
 * A theme approval schedules nothing: the drafter writes each post a day before
 * its time, and the post itself still waits for "Approve". */

import { useEffect, useState } from "react";
import { CheckCheck } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useLocale, useT } from "@/i18n";
import { rangeTitle, themeWeek, themesIn, type CalendarView } from "@/lib/calendar";
import { useStore } from "@/store";

export function ApproveThemesButton({ view, anchor }: { view: CalendarView; anchor: Date }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="secondary" size="sm" onClick={() => setOpen(true)} aria-label={t("calendar.themes.button")} title={t("calendar.themes.button")}>
        <CheckCheck className="size-4" />
        <span className="hidden sm:inline">{t("calendar.themes.button")}</span>
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        {open && <ApproveThemesForm view={view} anchor={anchor} close={() => setOpen(false)} />}
      </Dialog>
    </>
  );
}

function ApproveThemesForm({ view, anchor, close }: { view: CalendarView; anchor: Date; close: () => void }) {
  const t = useT();
  const locale = useLocale();
  const approveThemes = useStore((s) => s.approveThemes);
  // Fixed when the dialog opens: the count, the words and the request all mean the same week.
  const [{ start, end, now }] = useState(() => { const now = new Date(); return { ...themeWeek(view, anchor, now), now }; });
  const [count, setCount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    // No count is no verdict: the confirm stays enabled and the server answers.
    api.content.list(start.toISOString(), end.toISOString())
      .then((plan) => { if (live) setCount(themesIn(plan.items, start, end, now).length); })
      .catch(() => undefined);
    return () => { live = false; };
  }, [start, end, now]);

  const confirm = async () => {
    setBusy(true); setError(null);
    try {
      const out = await approveThemes(start.toISOString(), end.toISOString());
      toast(t("calendar.themes.done", { n: out.approved }));
      close();
    } catch (e) { setError(e instanceof Error && e.message ? e.message : t("common.error")); }
    finally { setBusy(false); }
  };

  return (
    <DialogContent className="sm:max-w-md">
      <DialogHeader>
        <DialogTitle>{t("calendar.themes.title")}</DialogTitle>
        <DialogDescription>{t("calendar.themes.lead", { range: rangeTitle("week", start, locale) })}</DialogDescription>
      </DialogHeader>
      {count !== null && (
        <p className="text-sm" aria-live="polite">{count > 0 ? t("calendar.themes.count", { n: count }) : t("calendar.themes.none")}</p>
      )}
      {error && <p role="alert" className="break-words text-sm text-error-foreground">{error}</p>}
      <DialogFooter>
        <Button type="button" variant="secondary" onClick={close} disabled={busy}>{t("common.cancel")}</Button>
        <Button type="button" disabled={busy || count === 0} onClick={() => void confirm()}>{t("calendar.themes.confirm")}</Button>
      </DialogFooter>
    </DialogContent>
  );
}
