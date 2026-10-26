/** The bar a phone gets instead of the sidebar (DESIGN.md §4, "Mobile header"):
 * the menu that opens the sidebar drawer, the mark in a 44px target, and a new
 * chat on the right. Hidden from `md` up, where the sidebar is docked. */

import { Link, useNavigate } from "react-router";
import { Menu, SquarePen } from "lucide-react";
import { useT } from "@/i18n";
import { useStore } from "@/store";
import { Mark } from "./Logo";

const TARGET = "flex size-11 shrink-0 items-center justify-center rounded-md text-sidebar-foreground hover:bg-accent hover:text-accent-foreground";

export function MobileBar() {
  const t = useT();
  const navigate = useNavigate();
  const create = useStore((s) => s.create);
  const setSidebarOpen = useStore((s) => s.setSidebarOpen);
  return (
    <header className="flex h-12 shrink-0 items-center gap-1 border-b border-sidebar-border bg-sidebar px-1 md:hidden">
      <button type="button" aria-label={t("sidebar.menu")} className={TARGET} onClick={() => setSidebarOpen(true)}>
        <Menu className="size-5" strokeWidth={1.75} />
      </button>
      <Link to="/" aria-label="CladeX" className="flex h-11 items-center rounded-md px-1.5 hover:bg-accent">
        <Mark width={32} />
      </Link>
      <button type="button" aria-label={t("sidebar.newChat")} className={`${TARGET} ml-auto`}
        onClick={async () => navigate(`/t/${encodeURIComponent(await create())}`)}>
        <SquarePen className="size-5" strokeWidth={1.75} />
      </button>
    </header>
  );
}
