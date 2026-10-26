/** `/calendar` — the content plan and the routines on one timeline, with the
 * routines list one tab over. Tabs are URL segments, like Customize's, so each
 * is linkable: `/calendar` is the calendar and `/calendar/tasks` the list the
 * sidebar's "Scheduled" used to open.
 *
 * A single routine (`/calendar/tasks/:id`) is its header over its delivery
 * thread and needs the whole height of the page, so it gets the outlet alone —
 * no title, no tab strip, no scroll container around a thread that scrolls
 * itself. */

import { Navigate, NavLink, Outlet, useMatch, useParams } from "react-router";
import { useT, type Key } from "@/i18n";
import { cn } from "@/lib/utils";

const TABS: { to: string; label: Key; end?: boolean }[] = [
  { to: "/calendar", label: "calendar.tabCalendar", end: true },
  { to: "/calendar/tasks", label: "calendar.tabTasks" },
];

export function Calendar() {
  const t = useT();
  const onRoutine = useMatch("/calendar/tasks/:id");
  if (onRoutine) return <Outlet />;
  return (
    <div className="mx-auto w-full max-w-6xl overflow-y-auto px-4 py-6 sm:px-8 sm:py-8">
      <h1 className="text-2xl font-semibold tracking-tight">{t("calendar.title")}</h1>
      <p className="mt-1 text-sm text-muted-foreground">{t("calendar.subtitle")}</p>
      <nav className="mt-6 flex flex-wrap gap-1">
        {TABS.map((tab) => (
          <NavLink key={tab.to} to={tab.to} end={tab.end}
            className={({ isActive }) => cn("rounded-md px-3 py-1.5 text-sm transition-colors", isActive ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:text-foreground")}>
            {t(tab.label)}
          </NavLink>
        ))}
      </nav>
      <div className="pt-6"><Outlet /></div>
    </div>
  );
}

/** `/routines/:id`, from before the calendar: the same routine under its new URL. */
export function RoutineRedirect() {
  const { id } = useParams();
  return <Navigate to={`/calendar/tasks/${id ?? ""}`} replace />;
}
