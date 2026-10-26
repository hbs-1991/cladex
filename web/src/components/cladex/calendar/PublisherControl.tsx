/** Which routine publishes the plan, and which one drafts it — two buttons in
 * the calendar toolbar, owner only, one component with a `kind`.
 *
 * Both are on-demand (`manual`) routines the content tick wakes: the publisher
 * when an approved item falls due, the drafter a day before an approved theme's
 * time. The server stores each by id and refuses anything that is not the
 * owner's manual routine (400 `bad_publisher` / `bad_drafter`), and refuses one
 * routine in both roles. So the picker offers manual routines only, minus the
 * one holding the other role, and the shortcut creates one from its template —
 * the same routine the Tasks tab would make — and points the plan at it in one
 * go.
 *
 * A routine that is paused or gone is the quiet way a plan stops going out, so
 * the button says so in the toolbar rather than inside the dialog. */

import { useEffect, useState } from "react";
import { Link } from "react-router";
import { AlertTriangle, PenLine, Send, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useStore } from "@/store";
import { DRAFTER_TEMPLATE, PUBLISHER_TEMPLATE, templateKey, type RoutineTemplate } from "../routineTemplates";

export type RoutineRole = "publisher" | "drafter";

/** What differs between the two roles; everything else is the same control. */
const ROLES: Record<RoutineRole, { icon: LucideIcon; template: RoutineTemplate; other: RoutineRole }> = {
  publisher: { icon: Send, template: PUBLISHER_TEMPLATE, other: "drafter" },
  drafter: { icon: PenLine, template: DRAFTER_TEMPLATE, other: "publisher" },
};

export function PublisherControl({ kind = "publisher" }: { kind?: RoutineRole }) {
  const t = useT();
  const current = useStore((s) => s[kind]);
  const [open, setOpen] = useState(false);
  const Icon = ROLES[kind].icon;
  const healthy = current?.job_id != null && current.state === "active";
  const label = current?.job_id != null && current.name ? current.name : t(`calendar.${kind}.none`);
  return (
    <>
      <Button variant="secondary" size="sm" onClick={() => setOpen(true)} className="min-w-0 max-w-full"
        aria-label={`${t(`calendar.${kind}.label`)}: ${label}`}>
        {healthy ? <Icon className="size-4" /> : <AlertTriangle className="size-4 text-warning-foreground" />}
        <span className="hidden text-muted-foreground lg:inline">{t(`calendar.${kind}.label`)}:</span>
        <span className={cn("min-w-0 truncate", !healthy && "text-warning-foreground")}>{label}</span>
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        {open && <RoleForm kind={kind} close={() => setOpen(false)} />}
      </Dialog>
    </>
  );
}

function RoleForm({ kind, close }: { kind: RoutineRole; close: () => void }) {
  const t = useT();
  const s = useStore();
  const { template, other } = ROLES[kind];
  const p = s[kind];
  const taken = s[other]?.job_id;
  const assign = kind === "publisher" ? s.setPublisher : s.setDrafter;
  const manual = s.routines.filter((r) => r.kind === "manual" && r.id !== taken);
  const [pick, setPick] = useState<string>(p?.job_id != null ? String(p.job_id) : "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { void s.loadRoutines(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (fn: () => Promise<string>) => {
    setBusy(true); setError(null);
    try { toast(t(`calendar.${kind}.saved`, { name: await fn() })); close(); }
    catch (e) { setError(e instanceof Error && e.message ? e.message : t("common.error")); }
    finally { setBusy(false); }
  };
  const use = () => run(async () => (await assign(Number(pick))).name ?? "");
  const create = () => run(async () => {
    const r = await s.createRoutine({
      name: t(templateKey(template.key, "name")), prompt: t(templateKey(template.key, "prompt")),
      schedule: template.schedule, timeout_seconds: template.timeout_seconds ?? null,
    });
    return (await assign(r.id)).name ?? r.name;
  });

  const state = p?.job_id == null ? null
    : p.state === "paused" ? t(`calendar.${kind}.paused`)
    : p.state === "active" ? null : t(`calendar.${kind}.missing`);
  const pickId = `${kind}-pick`;
  return (
    <DialogContent className="sm:max-w-md">
      <DialogHeader>
        <DialogTitle>{t(`calendar.${kind}.title`)}</DialogTitle>
        <DialogDescription>{t(`calendar.${kind}.lead`)}</DialogDescription>
      </DialogHeader>
      {p?.job_id != null && (
        <p className="text-sm">
          {t(`calendar.${kind}.current`)}{" "}
          <Link to={`/calendar/tasks/${p.job_id}`} onClick={close} className="text-signal underline underline-offset-[3px]">{p.name ?? `#${p.job_id}`}</Link>
          {state && <span className="text-warning-foreground"> — {state}</span>}
        </p>
      )}
      <div className="grid gap-1.5">
        <Label htmlFor={pickId}>{t(`calendar.${kind}.pick`)}</Label>
        {manual.length > 0 ? (
          <Select value={pick} onValueChange={setPick}>
            <SelectTrigger id={pickId} className="w-full"><SelectValue placeholder={t(`calendar.${kind}.pickPlaceholder`)} /></SelectTrigger>
            <SelectContent>{manual.map((r) => <SelectItem key={r.id} value={String(r.id)}>{r.name}</SelectItem>)}</SelectContent>
          </Select>
        ) : <p className="text-sm text-muted-foreground">{t(`calendar.${kind}.noManual`)}</p>}
      </div>
      {error && <p role="alert" className="break-words text-sm text-error-foreground">{error}</p>}
      <DialogFooter className="flex-wrap gap-2">
        <Button type="button" variant="secondary" disabled={busy} onClick={() => void create()}
          title={t(`calendar.${kind}.createHint`, { name: t(templateKey(template.key, "name")) })}>
          {t(`calendar.${kind}.create`)}
        </Button>
        {manual.length > 0 && (
          <Button type="button" disabled={busy || !pick || Number(pick) === p?.job_id} onClick={() => void use()}>{t(`calendar.${kind}.use`)}</Button>
        )}
      </DialogFooter>
    </DialogContent>
  );
}
