/** One skill in the library list, in the routines-row idiom: a tile, the name
 * with the badges it earns, the description the agent itself reads, and a muted
 * line of use. A skill whose frontmatter will not parse still gets a row — it is
 * exactly the one the owner has to go and fix. */

import { BookOpen, Pin } from "lucide-react";
import { Link } from "react-router";
import type { SkillWire } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { useLocale, useT, type Key } from "@/i18n";
import { fmtDateTime } from "@/lib/dates";
import { cn } from "@/lib/utils";

/** Where the text came from. The source tree is the authority, not the sidecar. */
const SOURCE: Record<SkillWire["source"], Key> = {
  bundled: "skills.bundled", agent: "skills.fromAgent", user: "skills.yours",
};

export function SkillRow({ s }: { s: SkillWire }) {
  const t = useT();
  const locale = useLocale();
  const used = s.use_count > 0 ? t("skills.usedTimes", { n: s.use_count }) : t("skills.neverUsed");
  return (
    <li>
      <Link to={`/customize/skills/${encodeURIComponent(s.name)}`} className="-mx-3 flex items-start gap-4 rounded-lg px-3 py-3 hover:bg-accent">
        <span className={cn("mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted", s.state === "archived" && "opacity-50")}>
          <BookOpen className="size-4" strokeWidth={1.75} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="truncate font-medium">{s.title || s.name}</span>
            <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">{t(SOURCE[s.source])}</Badge>
            {s.pinned && <Badge variant="outline" className="px-1.5 py-0 text-[10px]"><Pin className="size-2.5" /> {t("skills.pinned")}</Badge>}
            {s.state === "stale" && <Badge variant="outline" className="px-1.5 py-0 text-[10px] text-muted-foreground">{t("skills.stale")}</Badge>}
            {s.state === "archived" && <Badge variant="outline" className="px-1.5 py-0 text-[10px] text-muted-foreground">{t("skills.archived")}</Badge>}
            {s.broken && <Badge variant="outline" className="px-1.5 py-0 text-[10px] text-error-foreground">{t("skills.broken")}</Badge>}
          </span>
          <span className="mt-0.5 line-clamp-2 block text-sm text-muted-foreground">{s.description || t("skills.noDescription")}</span>
          <span className="mt-1.5 block text-xs text-muted-foreground">
            {used}
            {s.last_used && ` · ${t("skills.lastUsed", { when: fmtDateTime(s.last_used, locale) })}`}
          </span>
        </span>
      </Link>
    </li>
  );
}
