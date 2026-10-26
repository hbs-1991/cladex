/** Header of a routine page: schedule, prompt, run controls, run strip and the edit dialog. */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { Pause, Pencil, Play, Trash2 } from "lucide-react";
import { toast } from "sonner";
import type { RoutineWire } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useT } from "@/i18n";
import { fmtDateTime } from "@/lib/dates";
import { describeSchedule } from "@/lib/schedule";
import { useStore } from "@/store";
import { ConfirmDialog } from "./AskDialog";
import { RoutineForm } from "./RoutineForm";
import { RunStrip } from "./RunStrip";

export function RoutineHeader({ r }: { r: RoutineWire }) {
  const s = useStore();
  const t = useT();
  const navigate = useNavigate();
  const [edit, setEdit] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  useEffect(() => { void s.loadRuns(r.id); }, [r.id]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <header className="space-y-3 border-b px-4 py-4 md:px-6">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h1 className="text-xl font-semibold md:text-2xl">{r.name}</h1>
        <span className={`rounded px-2 py-0.5 text-xs ${r.state === "active" ? "bg-success text-success-foreground" : "bg-muted text-muted-foreground"}`}>
          {r.state === "active" ? t("routines.active") : r.state === "paused" ? t("routines.paused") : t("routines.finished")}
        </span>
      </div>
      <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
        <span>⏰ {describeSchedule(r.schedule)}</span>
        {r.next_run_at && <span>{t("routines.nextRunAt", { when: fmtDateTime(r.next_run_at) })}</span>}
        <span>{t("routines.timeout", { n: r.timeout_seconds ?? 180 })}</span>
      </div>
      <p className="rounded-md border bg-card px-3 py-2 text-sm">{r.prompt}</p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={async () => { await s.runRoutine(r.id); toast(t("routines.queued")); }}><Play className="size-4" /> {t("routines.runNow")}</Button>
        {r.state === "active"
          ? <Button size="sm" variant="secondary" onClick={() => void s.pauseRoutine(r.id)}><Pause className="size-4" /> {t("routines.pause")}</Button>
          : <Button size="sm" variant="secondary" onClick={() => void s.resumeRoutine(r.id)}><Play className="size-4" /> {t("routines.resume")}</Button>}
        <Button size="sm" variant="secondary" onClick={() => setEdit(true)}><Pencil className="size-4" /> {t("common.edit")}</Button>
        <Button size="sm" variant="ghost" className="ml-auto text-error-foreground"
          onClick={() => setConfirmDelete(true)}>
          <Trash2 className="size-4" /> {t("common.delete")}
        </Button>
      </div>
      <RunStrip runs={s.runs[r.id] ?? []} />
      <ConfirmDialog open={confirmDelete} onOpenChange={setConfirmDelete} title={t("common.delete")}
        description={t("routines.deleteConfirm", { name: r.name })} confirmLabel={t("common.delete")} destructive
        onConfirm={async () => { await s.removeRoutine(r.id); navigate("/calendar/tasks"); }} />
      <Dialog open={edit} onOpenChange={setEdit}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t("routines.editRoutine")}</DialogTitle></DialogHeader>
          <RoutineForm initial={r} onCancel={() => setEdit(false)} onSubmit={async (v) => { await s.updateRoutine(r.id, v); setEdit(false); }} />
        </DialogContent>
      </Dialog>
    </header>
  );
}
