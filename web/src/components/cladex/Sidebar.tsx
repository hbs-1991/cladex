/** Left rail, ChatGPT-style: the app name with a search button, a short list of
 * actions, then a flat "Recent" list of threads and the user menu at the
 * bottom. Search lives in the ⌘K palette. */

import { useMemo } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { Brain, CalendarDays, Search, SlidersHorizontal, SquarePen, type LucideIcon } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useStore } from "@/store";
import { Skeleton } from "@/components/ui/skeleton";
import { Logo } from "./Logo";
import { ThreadRow } from "./ThreadRow";
import { UserMenu } from "./UserMenu";

const ITEM = "flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left text-sm text-sidebar-foreground hover:bg-accent hover:text-accent-foreground";
/** The current page keeps the selection surface, hover included. */
const SELECTED = "bg-sidebar-accent font-medium text-sidebar-accent-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground";

function NavIcon({ icon: Icon }: { icon: LucideIcon }) {
  return <Icon className="size-4 shrink-0" strokeWidth={1.75} />;
}

/** Stand-ins for the recent list while the first page is on the wire.
 *
 * Only before the first page lands: `load()` also runs on every remount of the
 * layout, and a sidebar that already has rows must not throw them away to blink
 * at the reader. The widths vary so the block reads as titles rather than as a
 * progress bar, and the delay keeps a fast answer from flashing it at all. */
function RecentSkeleton() {
  const t = useT();
  const widths = ["w-4/5", "w-3/5", "w-11/12", "w-2/3", "w-3/4", "w-1/2"];
  return (
    <div role="status" aria-label={t("sidebar.loadingThreads")}
      className="animate-in fade-in fill-mode-both flex flex-col gap-1 [animation-delay:150ms] [animation-duration:200ms]">
      {widths.map((w, i) => (
        <div key={i} className="flex h-8 items-center px-2.5">
          <Skeleton className={cn("h-3.5 motion-reduce:animate-none", w)} />
        </div>
      ))}
    </div>
  );
}

export function Sidebar() {
  const t = useT();
  const list = useStore((s) => s.list);
  const loaded = useStore((s) => s.loaded);
  const create = useStore((s) => s.create);
  const loadMore = useStore((s) => s.loadMore);
  const next = useStore((s) => s.next);
  const setPaletteOpen = useStore((s) => s.setPaletteOpen);
  const navigate = useNavigate();
  const { pathname } = useLocation();
  // `/routines*` are only redirects now, but a bookmark lands there for a frame first.
  const onCalendar = pathname.startsWith("/calendar") || pathname.startsWith("/routines");
  const recent = useMemo(() => [...list].sort((a, b) => b.last_event_at.localeCompare(a.last_event_at)), [list]);

  return (
    <aside className="flex h-full w-full flex-col bg-sidebar text-sidebar-foreground md:w-sidebar">
      <div className="flex items-center gap-1 px-3 pb-1 pt-3">
        <Link to="/" aria-label="CladeX" className="rounded-md px-1.5 py-1 hover:bg-accent">
          <Logo width={144} />
        </Link>
        <button type="button" aria-label={t("sidebar.searchChats")} onClick={() => setPaletteOpen(true)}
          className="ml-auto rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-accent-foreground">
          <Search className="size-4" strokeWidth={1.75} />
        </button>
      </div>

      <nav className="px-3 py-2">
        <button type="button" className={ITEM} onClick={async () => navigate(`/t/${encodeURIComponent(await create())}`)}>
          <NavIcon icon={SquarePen} /> {t("sidebar.newChat")}
        </button>
        <Link to="/calendar" className={cn(ITEM, onCalendar && SELECTED)}>
          <NavIcon icon={CalendarDays} /> {t("sidebar.calendar")}
        </Link>
        <Link to="/memory" className={cn(ITEM, pathname.startsWith("/memory") && SELECTED)}>
          <NavIcon icon={Brain} /> {t("memory.title")}
        </Link>
        <Link to="/customize" className={cn(ITEM, pathname.startsWith("/customize") && SELECTED)}>
          <NavIcon icon={SlidersHorizontal} /> {t("customize.title")}
        </Link>
      </nav>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-2">
        {(recent.length > 0 || !loaded) && <div className="px-2.5 pb-1 pt-3 text-xs text-muted-foreground">{t("sidebar.recent")}</div>}
        {!loaded && recent.length === 0 ? <RecentSkeleton /> : recent.map((th) => <ThreadRow key={th.id} t={th} />)}
        {next && (
          <button type="button" onClick={() => void loadMore()} className={cn(ITEM, "mt-1 text-muted-foreground")}>{t("sidebar.showMore")}</button>
        )}
      </div>

      <UserMenu />
    </aside>
  );
}
