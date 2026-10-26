/** The Skills tab: the agent's procedural memory as a list — what it has learned,
 * where each skill came from and how often it reaches for it. Filtering and
 * searching happen in the browser: the library is a few dozen rows at most, and
 * a round trip per keystroke would buy nothing. */

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useT, type Key } from "@/i18n";
import { cn } from "@/lib/utils";
import { useStore } from "@/store";
import { NewSkillDialog } from "./NewSkillDialog";
import { SkillRow } from "./SkillRow";

type Filter = "active" | "archived" | "all";
const FILTERS: { id: Filter; label: Key }[] = [
  { id: "active", label: "skills.filterActive" },
  { id: "archived", label: "skills.filterArchived" },
  { id: "all", label: "skills.filterAll" },
];

export function SkillsTab() {
  const t = useT();
  const s = useStore();
  // Since Wave 4b the API serves the caller's OWN library and every logged-in
  // user may edit it; the buttons follow the API, not the role.
  const canEdit = Boolean(s.me);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("active");
  const [creating, setCreating] = useState(false);
  useEffect(() => { void s.loadSkills().catch(() => toast.error(t("skills.loadFailed"))); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const matches = (x: string) => x.toLowerCase().includes(needle);
    return s.skills.filter((x) =>
      (filter === "all" || (filter === "archived") === (x.state === "archived")) &&
      (!needle || matches(x.name) || matches(x.title) || matches(x.description)));
  }, [s.skills, q, filter]);

  // Which of the three "nothing here" sentences is the true one: an empty
  // library, an empty archive, or a search that matched nothing.
  const emptyText: Key = s.skills.length === 0 ? "skills.empty"
    : filter === "archived" && q.trim() === "" ? "skills.emptyArchived"
    : "skills.emptyFiltered";

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <p className="min-w-0 flex-1 basis-64 text-sm text-muted-foreground">{t("skills.subtitle")}</p>
        <Input type="search" value={q} onChange={(e) => setQ(e.target.value)} className="h-9 min-w-0 flex-1 basis-40 sm:w-48 sm:flex-none"
          aria-label={t("skills.search")} placeholder={t("skills.searchPlaceholder")} />
        {canEdit && <Button onClick={() => setCreating(true)}>{t("skills.newSkill")}</Button>}
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div role="group" aria-label={t("skills.filter")} className="inline-flex rounded-lg bg-muted p-0.5">
          {FILTERS.map((f) => (
            <button key={f.id} type="button" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}
              className={cn("rounded-md px-2.5 py-1 text-sm transition-colors",
                filter === f.id ? "bg-background font-medium text-foreground shadow-xs" : "text-muted-foreground hover:text-foreground")}>
              {t(f.label)}
            </button>
          ))}
        </div>
        {s.skillsLoaded && <span className="text-xs text-muted-foreground">{t("skills.count", { n: shown.length })}</span>}
      </div>

      <ul className="divide-y divide-border">
        {shown.map((x) => <SkillRow key={x.name} s={x} />)}
      </ul>
      {s.skillsLoaded && shown.length === 0 && <p className="py-6 text-sm text-muted-foreground">{t(emptyText)}</p>}

      {canEdit && <NewSkillDialog open={creating} onOpenChange={setCreating} />}
    </div>
  );
}
