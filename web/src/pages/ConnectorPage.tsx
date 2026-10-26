/** `/customize/connectors/:name` — one connector in full: the mark, the state,
 * the whole description, the tools it offers, where it lives and when it was
 * added. Actions are the same as on the card. */

import { useEffect } from "react";
import { ArrowLeft, BadgeCheck, Check, Info, Link2, MoreVertical } from "lucide-react";
import { Link, useParams } from "react-router";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { BrandTile } from "@/components/cladex/brandIcons";
import { useConnectorActions } from "@/components/cladex/useConnectorActions";
import { useLocale, useT } from "@/i18n";
import { connectorMenu, connectorStatus, connectsDirectly, firstSentence } from "@/lib/connectorMenu";
import { fmtDateTime } from "@/lib/dates";
import { cn } from "@/lib/utils";
import { useStore } from "@/store";

const LIST = "/customize/connectors";

export function ConnectorPage() {
  const t = useT();
  const locale = useLocale();
  const s = useStore();
  const { name = "" } = useParams();
  // No owner gate here: every control on this page acts on the caller's own
  // connector row, so the API answers each caller for themselves.
  const { onAction, secretsDialog } = useConnectorActions();
  useEffect(() => { if (!s.connectorsLoaded) void s.loadConnectors(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const c = s.connectors.find((x) => x.name === name);

  const copyLink = async () => {
    try { await navigator.clipboard.writeText(window.location.href); toast(t("common.copied")); }
    catch { toast.error(t("common.copyFailed")); }
  };

  if (!c) {
    return (
      <div>
        <Link to={LIST} className="inline-flex items-center gap-1 rounded-md bg-muted px-2.5 py-1.5 text-sm"><ArrowLeft className="size-4" /> {t("customize.connectors")}</Link>
        <p className="mt-8 text-sm text-muted-foreground">{s.connectorsLoaded ? t("connectors.notFound") : t("common.loading")}</p>
      </div>
    );
  }
  const status = connectorStatus(c, t);
  const host = c.transport !== "stdio" ? new URL(c.endpoint).host : null;
  const scopes = c.scopes.map((sc) => (sc === "cron" ? t("connectors.scopeCron") : t("connectors.scopeTurn"))).join(", ");

  return (
    <div>
      <div className="mb-8 flex items-center justify-between">
        <Link to={LIST} className="inline-flex items-center gap-1 rounded-md bg-muted px-2.5 py-1.5 text-sm hover:bg-accent"><ArrowLeft className="size-4" /> {t("customize.connectors")}</Link>
        <Button variant="ghost" size="sm" onClick={() => void copyLink()}><Link2 className="size-4" /> {t("connectors.copyLink")}</Button>
      </div>

      <header className="flex flex-wrap items-start gap-4 sm:gap-5">
        <BrandTile name={c.name} label={c.label} size="lg" />
        <div className="min-w-0 flex-1">
          <h2 className="flex items-center gap-2 text-2xl font-medium">
            {c.label}
            {c.source === "catalog"
              ? <BadgeCheck role="img" aria-label={t("connectors.fromCatalog")} className="size-5 text-muted-foreground" strokeWidth={1.5} />
              : <Badge variant="secondary">{t("connectors.custom")}</Badge>}
          </h2>
          <p className="mt-1 text-muted-foreground">{firstSentence(c.description)}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2 max-sm:w-full">
          {c.state === "connected" ? (
            <span className="inline-flex items-center gap-1.5 rounded-lg bg-muted px-3 py-1.5 text-sm"><Check className="size-4" /> {t("connectors.connected")}</span>
          ) : connectsDirectly(c) ? (
            <Button onClick={() => onAction("connect", c)}>{t("connectors.connect")}</Button>
          ) : status ? (
            <span className={cn("inline-flex items-center rounded-lg bg-muted px-3 py-1.5 text-sm", c.state === "disabled" ? "text-muted-foreground" : "text-error-foreground")}>{status}</span>
          ) : null}
          {!connectsDirectly(c) && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" aria-label={t("common.actions")}><MoreVertical className="size-4" /></Button></DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {connectorMenu(c, t).map((it) => <DropdownMenuItem key={it.label} onSelect={() => onAction(it.action, c)}>{it.label}</DropdownMenuItem>)}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      </header>

      <p className="mt-10 max-w-3xl text-sm leading-relaxed">{c.description}</p>

      <h3 className="mt-10 text-lg font-medium">{t("connectors.tools")}</h3>
      {c.tool_names.length > 0 ? (
        <ul className="mt-3 grid gap-2 sm:grid-cols-3">
          {c.tool_names.map((n) => <li key={n}><code className="inline-block rounded-md bg-muted px-2.5 py-1 text-xs">{n}</code></li>)}
        </ul>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground">{status && c.tools !== null ? t("connectors.toolsCount", { n: c.tools }) : t("connectors.noToolsYet")}</p>
      )}

      <div className="mt-8 flex gap-2 rounded-xl border border-border p-4 text-sm text-muted-foreground">
        <Info className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} />
        <span>{t("connectors.trustNote")}</span>
      </div>

      <dl className="mt-10 grid gap-x-8 gap-y-6 border-t border-border pt-8 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-xs uppercase tracking-wide text-muted-foreground">{t("connectors.madeBy")}</dt>
          <dd className="mt-1">{host ? <a href={`https://${host}`} target="_blank" rel="noreferrer" className="underline-offset-2 hover:underline">{host} ↗</a> : c.endpoint.split(" ")[0]}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-muted-foreground">{t("connectors.endpoint")}</dt>
          <dd className="mt-1 break-all font-mono text-xs">{c.endpoint}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-muted-foreground">{t("connectors.scopesLabel")}</dt>
          <dd className="mt-1">{scopes}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-muted-foreground">{t("connectors.added")}</dt>
          <dd className="mt-1">{c.installed_at ? fmtDateTime(c.installed_at, locale) : "—"}</dd>
        </div>
      </dl>
      {secretsDialog}
    </div>
  );
}
