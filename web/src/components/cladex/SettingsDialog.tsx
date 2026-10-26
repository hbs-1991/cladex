/** Settings: a dialog with its own sidebar of sections, opened from the user menu.
 *
 * A dialog rather than a page because settings are a detour, not a destination —
 * whatever thread is open stays open behind it. The sidebar is what keeps that
 * true as sections are added: the shape does not change when the second one
 * arrives, only the list does.
 *
 * Owner-only for now, because both sections are: extension secrets and the list of
 * who else may reach the agent. The menu already hides the entry; this guard is
 * what makes that a fact rather than a convention.
 */

import { useState } from "react";
import { KeyRound, Users, type LucideIcon } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useT, type Key } from "@/i18n";
import { cn } from "@/lib/utils";
import { useStore } from "@/store";
import { SecretsSection } from "./SecretsSection";
import { UsersSection } from "./UsersSection";

type Section = { id: string; label: Key; icon: LucideIcon; Panel: () => React.JSX.Element };

const SECTIONS: Section[] = [
  { id: "secrets", label: "settings.secrets", icon: KeyRound, Panel: SecretsSection },
  { id: "users", label: "settings.users", icon: Users, Panel: UsersSection },
];

export function SettingsDialog() {
  const t = useT();
  const me = useStore((s) => s.me);
  const open = useStore((s) => s.settingsOpen);
  const setOpen = useStore((s) => s.setSettingsOpen);
  const [active, setActive] = useState(SECTIONS[0].id);
  if (me?.role !== "owner") return null;
  const section = SECTIONS.find((s) => s.id === active) ?? SECTIONS[0];
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="flex h-[min(85vh,34rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl sm:flex-row">
        <DialogTitle className="sr-only">{t("settings.title")}</DialogTitle>
        <DialogDescription className="sr-only">{t("secrets.lead")}</DialogDescription>
        <nav aria-label={t("settings.title")}
          className="flex shrink-0 gap-1 overflow-x-auto border-b border-border bg-sidebar p-2 sm:w-52 sm:flex-col sm:overflow-y-auto sm:border-r sm:border-b-0 sm:p-3">
          <p className="hidden px-2 pt-1 pb-2 text-xs font-medium text-muted-foreground sm:block">{t("settings.group")}</p>
          {SECTIONS.map((s) => (
            <button key={s.id} type="button" onClick={() => setActive(s.id)}
              aria-current={s.id === active ? "page" : undefined}
              className={cn("flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm whitespace-nowrap transition-colors",
                s.id === active ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground" : "text-sidebar-foreground hover:bg-accent")}>
              <s.icon aria-hidden className="size-4 shrink-0" strokeWidth={1.75} />
              {t(s.label)}
            </button>
          ))}
        </nav>
        <div className="min-w-0 flex-1 overflow-y-auto p-5 sm:p-6">
          <h2 className="mb-4 text-lg font-semibold tracking-tight">{t(section.label)}</h2>
          <section.Panel />
        </div>
      </DialogContent>
    </Dialog>
  );
}
