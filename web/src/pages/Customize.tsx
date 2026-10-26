/** `/customize` — what the agent may use: a tab strip over Connectors, Plugins
 * and Skills. Tabs are URL segments so a tab is linkable and the OAuth return
 * can land on it. */

import { NavLink, Outlet } from "react-router";
import { useT, type Key } from "@/i18n";
import { cn } from "@/lib/utils";

const TABS: { to: string; label: Key }[] = [
  { to: "connectors", label: "customize.connectors" },
  { to: "plugins", label: "customize.plugins" },
  { to: "skills", label: "customize.skills" },
];

export function Customize() {
  const t = useT();
  return (
    <div className="mx-auto w-full max-w-5xl overflow-y-auto px-4 py-6 sm:px-8 sm:py-8">
      <h1 className="text-2xl font-semibold tracking-tight">{t("customize.title")}</h1>
      <p className="mt-1 text-sm text-muted-foreground">{t("customize.subtitle")}</p>
      <nav className="mt-6 flex flex-wrap gap-1">
        {TABS.map((tab) => (
          <NavLink key={tab.to} to={tab.to}
            className={({ isActive }) => cn("rounded-md px-3 py-1.5 text-sm transition-colors", isActive ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:text-foreground")}>
            {t(tab.label)}
          </NavLink>
        ))}
      </nav>
      <div className="pt-6"><Outlet /></div>
    </div>
  );
}
