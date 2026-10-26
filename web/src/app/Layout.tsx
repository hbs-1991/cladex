/** Application chrome: sidebar, routed page and the file panel. From `md` the
 * sidebar is docked; below it a phone gets the MobileBar and the same sidebar as
 * a drawer (DESIGN.md §4: sidebar and file preview are overlays below 768px). */

import { useEffect } from "react";
import { Outlet, useLocation } from "react-router";
import { Sidebar } from "@/components/cladex/Sidebar";
import { MobileBar } from "@/components/cladex/MobileBar";
import { FilePanel } from "@/components/cladex/FilePanel";
import { CommandPalette } from "@/components/cladex/CommandPalette";
import { SettingsDialog } from "@/components/cladex/SettingsDialog";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { useStore } from "@/store";
import { applyLocale, applyTheme } from "@/store/ui";

export function Layout() {
  const load = useStore((s) => s.load);
  const theme = useStore((s) => s.theme);
  const locale = useStore((s) => s.locale);
  const sidebarOpen = useStore((s) => s.sidebarOpen);
  const setSidebarOpen = useStore((s) => s.setSidebarOpen);
  const { pathname } = useLocation();
  useEffect(() => { applyTheme(theme); applyLocale(locale); }, [theme, locale]);
  useEffect(() => { void load(); }, [load]);
  // A tap in the drawer navigates; the drawer has then done its job.
  useEffect(() => { setSidebarOpen(false); }, [pathname, setSidebarOpen]);
  return (
    <div className="flex h-dvh flex-col bg-background pb-[env(safe-area-inset-bottom)] text-foreground md:flex-row">
      <MobileBar />
      <div className="hidden h-full md:flex"><Sidebar /></div>
      <Sheet open={sidebarOpen} onOpenChange={setSidebarOpen}>
        <SheetContent side="left" showCloseButton={false}
          className="w-[min(var(--cl-sidebar-drawer),calc(100vw-2rem))] gap-0 p-0 sm:max-w-none">
          <SheetTitle className="sr-only">CladeX</SheetTitle>
          <SheetDescription className="sr-only">Navigation</SheetDescription>
          <Sidebar />
        </SheetContent>
      </Sheet>
      <main className="flex min-h-0 min-w-0 flex-1 flex-col"><Outlet /></main>
      <FilePanel />
      <CommandPalette />
      <SettingsDialog />
    </div>
  );
}
