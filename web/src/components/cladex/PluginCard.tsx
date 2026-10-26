/** One plugin as a card, the connector card's twin: a tile, the label and a
 * one-line description that open the plugin page, and on the right one square
 * control that says the state — a plus to install, a green check when installed,
 * a warning when the clone is gone — and opens the menu once there is more than
 * one thing to do. */

import { BadgeCheck, Check, Loader2, Plus, Power, Puzzle, RefreshCw, TriangleAlert, type LucideIcon } from "lucide-react";
import { Link } from "react-router";
import type { PluginWire } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { useT } from "@/i18n";
import { firstSentence } from "@/lib/connectorMenu";
import { installsDirectly, needsReinstall, pluginMenu, pluginStatus, type PluginAction } from "@/lib/pluginMenu";
import { cn } from "@/lib/utils";

export type { PluginAction };

const SQUARE = "flex size-9 shrink-0 items-center justify-center rounded-lg border transition-colors";
const LOOK: Record<PluginWire["state"], { icon: LucideIcon; className: string }> = {
  available: { icon: Plus, className: "border-border bg-card text-foreground hover:bg-accent" },
  installed: { icon: Check, className: "border-success-foreground/30 bg-success text-success-foreground" },
  disabled: { icon: Power, className: "border-border bg-muted text-muted-foreground" },
  error: { icon: RefreshCw, className: "border-error-foreground/30 bg-error text-error-foreground" },
};
/** A row whose files are gone is not an error the server recorded — it is one the
 * page just noticed — so it gets its own look rather than borrowing `error`. */
const MISSING = { icon: TriangleAlert, className: "border-warning-foreground/30 bg-warning text-warning-foreground" };

const look = (p: PluginWire) => (needsReinstall(p) ? MISSING : LOOK[p.state]);

/** The plugin's tile. Plugins have no brands: one symbol for all of them, on the
 * same white square a connector's mark sits on. */
export function PluginTile({ size = "md" }: { size?: "md" | "lg" }) {
  const lg = size === "lg";
  return (
    <span aria-hidden className={cn("flex shrink-0 items-center justify-center border border-border bg-white text-neutral-600", lg ? "size-20 rounded-xl" : "size-11 rounded-lg")}>
      <Puzzle style={{ width: lg ? 40 : 22, height: lg ? 40 : 22 }} strokeWidth={1.75} />
    </span>
  );
}

/** The square control: installs outright, or opens the menu. `owner` gates the
 * clone, not the card — install and remove fetch and delete code every tree
 * loads, so they stay the owner's, while enable and disable write the caller's
 * own row and are offered to everybody. Static when nothing is left to offer. */
export function StateControl({ p, owner, busy, onAction }: { p: PluginWire; owner: boolean; busy?: boolean; onAction: (a: PluginAction, p: PluginWire) => void }) {
  const t = useT();
  const l = look(p);
  const Icon = busy ? Loader2 : l.icon;
  const mark = <Icon className={cn("size-4", busy && "animate-spin")} strokeWidth={2} />;
  const items = pluginMenu(p, t).filter((it) => owner || (it.action !== "install" && it.action !== "remove"));
  if (items.length === 0) return <span aria-hidden className={cn(SQUARE, l.className)}>{mark}</span>;
  if (owner && installsDirectly(p)) {
    return (
      <button type="button" aria-label={t("plugins.install")} disabled={busy} onClick={() => onAction("install", p)} className={cn(SQUARE, l.className)}>
        {mark}
      </button>
    );
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger aria-label={t("common.actions")} className={cn(SQUARE, l.className, "data-[state=open]:ring-2 data-[state=open]:ring-ring/40")}>
        {mark}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {items.map((it) => <DropdownMenuItem key={it.label} onSelect={() => onAction(it.action, p)}>{it.label}</DropdownMenuItem>)}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function PluginCard({ p, owner, busy, onAction }: { p: PluginWire; owner: boolean; busy?: boolean; onAction: (a: PluginAction, p: PluginWire) => void }) {
  const t = useT();
  const status = pluginStatus(p, t);
  const missing = needsReinstall(p);
  return (
    <li className={cn("flex items-center gap-3 rounded-xl border border-border bg-card p-3 transition-colors hover:border-foreground/20", p.state === "disabled" && "opacity-70")}>
      <Link to={`/customize/plugins/${encodeURIComponent(p.name)}`} className="flex min-w-0 flex-1 items-center gap-3">
        <PluginTile />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate font-medium">{p.label}</span>
            {p.source === "catalog"
              ? <BadgeCheck role="img" aria-label={t("plugins.fromCatalog")} className="size-3.5 shrink-0 text-muted-foreground" strokeWidth={1.75} />
              : <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">{t("plugins.custom")}</Badge>}
          </span>
          <span className="line-clamp-2 block text-sm text-muted-foreground">{firstSentence(p.description)}</span>
          {status && <span className={cn("mt-0.5 block text-xs", missing ? "text-warning-foreground" : p.state === "installed" ? "text-success-foreground" : p.state === "disabled" ? "text-muted-foreground" : "text-error-foreground")}>{status}</span>}
          {missing && <span className="block text-xs text-muted-foreground">{t("plugins.missingHint")}</span>}
        </span>
      </Link>
      <StateControl p={p} owner={owner} busy={busy} onAction={onAction} />
    </li>
  );
}
