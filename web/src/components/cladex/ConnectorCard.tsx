/** One connector as a card: the brand tile, the label and a one-line description
 * that open the connector page, and on the right one square control that says
 * the state — a plus to connect, a green check when connected, a refresh mark
 * when a sign-in is due — and opens the menu once there is more than one thing
 * to do. */

import { BadgeCheck, Check, Plus, Power, RefreshCw, type LucideIcon } from "lucide-react";
import { Link } from "react-router";
import type { ConnectorWire } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { useT } from "@/i18n";
import { connectorMenu, connectorStatus, connectsDirectly, firstSentence, type ConnectorAction } from "@/lib/connectorMenu";
import { cn } from "@/lib/utils";
import { BrandTile } from "./brandIcons";

export type { ConnectorAction };

const SQUARE = "flex size-9 shrink-0 items-center justify-center rounded-lg border transition-colors";
const LOOK: Record<ConnectorWire["state"], { icon: LucideIcon; className: string }> = {
  available: { icon: Plus, className: "border-border bg-card text-foreground hover:bg-accent" },
  connected: { icon: Check, className: "border-success-foreground/30 bg-success text-success-foreground" },
  needs_reauth: { icon: RefreshCw, className: "border-warning-foreground/30 bg-warning text-warning-foreground" },
  error: { icon: RefreshCw, className: "border-error-foreground/30 bg-error text-error-foreground" },
  disabled: { icon: Power, className: "border-border bg-muted text-muted-foreground" },
};

/** The square control: connects outright, or opens the menu. Static for a plain user. */
export function StateControl({ c, owner, onAction }: { c: ConnectorWire; owner: boolean; onAction: (a: ConnectorAction, c: ConnectorWire) => void }) {
  const t = useT();
  const look = LOOK[c.state];
  const Icon = look.icon;
  if (!owner) return <span aria-hidden className={cn(SQUARE, look.className)}><Icon className="size-4" strokeWidth={2} /></span>;
  if (connectsDirectly(c)) {
    return (
      <button type="button" aria-label={t("connectors.connect")} onClick={() => onAction("connect", c)} className={cn(SQUARE, look.className)}>
        <Icon className="size-4" strokeWidth={2} />
      </button>
    );
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger aria-label={t("common.actions")} className={cn(SQUARE, look.className, "data-[state=open]:ring-2 data-[state=open]:ring-ring/40")}>
        <Icon className="size-4" strokeWidth={2} />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {connectorMenu(c, t).map((it) => <DropdownMenuItem key={it.label} onSelect={() => onAction(it.action, c)}>{it.label}</DropdownMenuItem>)}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function ConnectorCard({ c, owner, onAction }: { c: ConnectorWire; owner: boolean; onAction: (a: ConnectorAction, c: ConnectorWire) => void }) {
  const t = useT();
  const status = connectorStatus(c, t);
  return (
    <li className={cn("flex items-center gap-3 rounded-xl border border-border bg-card p-3 transition-colors hover:border-foreground/20", c.state === "disabled" && "opacity-70")}>
      <Link to={`/customize/connectors/${encodeURIComponent(c.name)}`} className="flex min-w-0 flex-1 items-center gap-3">
        <BrandTile name={c.name} label={c.label} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate font-medium">{c.label}</span>
            {c.source === "catalog"
              ? <BadgeCheck role="img" aria-label={t("connectors.fromCatalog")} className="size-3.5 shrink-0 text-muted-foreground" strokeWidth={1.75} />
              : <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">{t("connectors.custom")}</Badge>}
          </span>
          <span className="line-clamp-2 block text-sm text-muted-foreground">{firstSentence(c.description)}</span>
          {status && <span className={cn("mt-0.5 block text-xs", c.state === "connected" ? "text-success-foreground" : c.state === "disabled" ? "text-muted-foreground" : "text-error-foreground")}>{status}</span>}
        </span>
      </Link>
      <StateControl c={c} owner={owner} onAction={onAction} />
    </li>
  );
}
