/** `/calendar/tasks` — the Tasks tab of the calendar (the old `/routines`, which
 * now redirects here), Claude-Desktop style: a header row with search, sort and
 * a "new task" menu; the list (or an empty state); then a zigzag rule and a grid
 * of templates that open the create dialog filled in. The page title is the
 * calendar's, and the tab strip names this tab, so the row carries no heading. */

import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import { AlarmClock, ChevronDown, Clock, MessageSquare, Search, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { RoutineForm, type RoutineInput } from "@/components/cladex/RoutineForm";
import { TEMPLATES, templateKey, type RoutineTemplate } from "@/components/cladex/routineTemplates";
import { currentLocale, t as translate, useLocale, useT, type Key, type Locale } from "@/i18n";
import { fmtDateTime } from "@/lib/dates";
import { describeSchedule } from "@/lib/schedule";
import { cn } from "@/lib/utils";
import { useStore } from "@/store";
import type { RoutineWire } from "@/api/types";

type Sort = "next" | "name";
const SORTS: Record<Sort, { label: Key; cmp: (a: RoutineWire, b: RoutineWire, locale: Locale) => number }> = {
  next: { label: "routines.sortNext", cmp: (a, b) => (a.next_run_at ?? "9").localeCompare(b.next_run_at ?? "9") },
  name: { label: "routines.sortName", cmp: (a, b, locale) => a.name.localeCompare(b.name, locale) },
};

/** What the agent is asked when a task is created in a chat rather than by hand. */
const chatDraft = (locale: Locale = currentLocale()) => translate("routines.chatDraft", {}, locale);

export function Routines() {
  const s = useStore();
  const t = useT();
  const locale = useLocale();
  const navigate = useNavigate();
  const [draft, setDraft] = useState<Partial<RoutineInput> | null>(null);   // the dialog, when open
  const [q, setQ] = useState("");
  const [searching, setSearching] = useState(false);
  const [sort, setSort] = useState<Sort>("next");
  useEffect(() => { void s.loadRoutines(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const owner = s.me?.role === "owner";
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return s.routines
      .filter((r) => !needle || r.name.toLowerCase().includes(needle) || r.prompt.toLowerCase().includes(needle))
      .sort((a, b) => SORTS[sort].cmp(a, b, locale));
  }, [s.routines, q, sort, locale]);
  const fromTemplate = (tpl: RoutineTemplate) => setDraft({ name: t(templateKey(tpl.key, "name")), prompt: t(templateKey(tpl.key, "prompt")), schedule: tpl.schedule,
    ...(tpl.timeout_seconds ? { timeout_seconds: tpl.timeout_seconds } : {}) });
  const templates = TEMPLATES.filter((tpl) => !tpl.ownerOnly || owner);
  const inChat = async () => navigate(`/t/${encodeURIComponent(await s.create())}`, { state: { draft: chatDraft(locale) } });

  return (
    <div className="w-full max-w-thread">
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1 basis-56">
          <h2 className="sr-only">{t("routines.title")}</h2>
          <p className="text-sm text-muted-foreground">{t("routines.subtitle")}</p>
        </div>
        {searching
          ? <Input autoFocus value={q} onChange={(e) => setQ(e.target.value)} onBlur={() => { if (!q) setSearching(false); }}
              placeholder={t("routines.find")} aria-label={t("routines.searchTasks")} className="h-9 min-w-0 flex-1 basis-40 sm:w-48 sm:flex-none" />
          : <Button variant="ghost" size="icon" aria-label={t("routines.searchTasks")} onClick={() => setSearching(true)}><Search className="size-4" /></Button>}
        <Select value={sort} onValueChange={(v) => setSort(v as Sort)}>
          <SelectTrigger aria-label={t("routines.sort")} className="h-9 w-auto gap-1"><span className="text-muted-foreground">{t("routines.sort")}</span> <SelectValue /></SelectTrigger>
          <SelectContent>{(Object.keys(SORTS) as Sort[]).map((k) => <SelectItem key={k} value={k}>{t(SORTS[k].label)}</SelectItem>)}</SelectContent>
        </Select>
        {owner && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button>{t("routines.newTask")} <ChevronDown className="size-4" /></Button></DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => void inChat()}><MessageSquare className="size-4" /> {t("routines.createInChat")}</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setDraft({})}><Settings2 className="size-4" /> {t("routines.setUpManually")}</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      {s.routinesLoaded && s.routines.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-14 text-center">
          <span className="flex size-16 items-center justify-center rounded-full bg-muted">
            <AlarmClock className="size-8 text-foreground" strokeWidth={1.5} />
          </span>
          <p className="text-sm text-muted-foreground">{t("routines.empty")}</p>
        </div>
      ) : (
        <ul className="divide-y divide-border">
          {shown.map((r) => (
            <li key={r.id}>
              <Link to={`/calendar/tasks/${r.id}`} className="-mx-3 flex items-start gap-4 rounded-lg px-3 py-3 hover:bg-accent">
                <span className={cn("mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted", r.state !== "active" && "opacity-50")}>
                  <Clock className="size-4" strokeWidth={1.75} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="truncate font-medium">{r.name}</span>
                    {r.state !== "active" && <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">{r.state === "paused" ? t("routines.paused") : t("routines.finished")}</span>}
                  </span>
                  <span className="mt-0.5 line-clamp-2 text-sm text-muted-foreground">{r.prompt}</span>
                  <span className="mt-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Clock className="size-3.5" /> {describeSchedule(r.schedule, locale)}
                    {r.next_run_at && r.state === "active" && <span>· {t("routines.next", { when: fmtDateTime(r.next_run_at, locale) })}</span>}
                  </span>
                </span>
              </Link>
            </li>
          ))}
          {s.routinesLoaded && shown.length === 0 && <li className="py-6 text-sm text-muted-foreground">{t("common.nothingFound")}</li>}
        </ul>
      )}

      {owner && (
        <>
          <div aria-hidden className="zigzag my-10" />
          <ul className="grid gap-x-10 gap-y-8 sm:grid-cols-2">
            {templates.map((tpl) => (
              <li key={tpl.key}>
                <button type="button" onClick={() => fromTemplate(tpl)} className="group flex w-full items-start gap-4 text-left">
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted transition-colors group-hover:bg-accent">
                    <tpl.icon className="size-4" strokeWidth={1.75} />
                  </span>
                  <span className="min-w-0">
                    <span className="block font-medium">{t(templateKey(tpl.key, "name"))}</span>
                    <span className="mt-0.5 block text-sm text-muted-foreground">{t(templateKey(tpl.key, "description"))}</span>
                    <span className="mt-1.5 flex items-center gap-1.5 text-xs text-muted-foreground"><Clock className="size-3.5" /> {describeSchedule(tpl.schedule, locale)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      <Dialog open={draft !== null} onOpenChange={(o) => { if (!o) setDraft(null); }}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader><DialogTitle>{t("routines.dialogNew")}</DialogTitle></DialogHeader>
          {draft && <RoutineForm initial={draft} onCancel={() => setDraft(null)}
            onSubmit={async (v) => { const r = await s.createRoutine(v); setDraft(null); navigate(`/calendar/tasks/${r.id}`); }} />}
        </DialogContent>
      </Dialog>
    </div>
  );
}
