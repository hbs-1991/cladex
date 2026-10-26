/** `/customize/plugins/:name` — one plugin in full: the whole description, what
 * it adds to the agent once it is loaded, the exact revision it was fetched
 * from, and when it landed. Actions are the same as on the card. */

import { useEffect, type ReactNode } from "react";
import { ArrowLeft, BadgeCheck, Check, Info, Link2, MoreVertical, TriangleAlert } from "lucide-react";
import { Link, useParams } from "react-router";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { PluginTile } from "@/components/cladex/PluginCard";
import { usePluginActions } from "@/components/cladex/usePluginActions";
import { useLocale, useT } from "@/i18n";
import { firstSentence } from "@/lib/connectorMenu";
import { installsDirectly, needsReinstall, pluginMenu, pluginStatus, treeUrl } from "@/lib/pluginMenu";
import { fmtDateTime } from "@/lib/dates";
import { cn } from "@/lib/utils";
import { useStore } from "@/store";

const LIST = "/customize/plugins";

/** "anthropics/knowledge-work-plugins" — the repository as a person names it. */
const repoLabel = (repo: string) => repo.replace(/^https?:\/\/[^/]+\//, "").replace(/\.git$/, "").replace(/\/+$/, "");

export function PluginPage() {
  const t = useT();
  const locale = useLocale();
  const s = useStore();
  const { name = "" } = useParams();
  const owner = s.me?.role === "owner";
  const { onAction, busy, confirmDialog } = usePluginActions();
  useEffect(() => { if (!s.pluginsLoaded) void s.loadPlugins(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const p = s.plugins.find((x) => x.name === name);

  const copyLink = async () => {
    try { await navigator.clipboard.writeText(window.location.href); toast(t("common.copied")); }
    catch { toast.error(t("common.copyFailed")); }
  };

  if (!p) {
    return (
      <div>
        <Link to={LIST} className="inline-flex items-center gap-1 rounded-md bg-muted px-2.5 py-1.5 text-sm"><ArrowLeft className="size-4" /> {t("customize.plugins")}</Link>
        <p className="mt-8 text-sm text-muted-foreground">{s.pluginsLoaded ? t("plugins.notFound") : t("common.loading")}</p>
      </div>
    );
  }
  const status = pluginStatus(p, t);
  const missing = needsReinstall(p);
  const scopes = p.scopes.map((sc) => (sc === "cron" ? t("plugins.scopeCron") : t("plugins.scopeTurn"))).join(", ");
  const c = p.components;
  const adds = c && (c.commands.length > 0 || c.skills.length > 0 || c.agents.length > 0 || c.hooks || c.mcp);
  // Install and Remove fetch and delete a clone every tree loads, so they stay
  // the owner's. Enable and disable write the caller's own row, so everybody
  // gets them — the same split the plugins router enforces server-side.
  const menu = pluginMenu(p, t).filter((it) => owner || (it.action !== "install" && it.action !== "remove"));

  const chips = (title: string, names: string[]) => names.length > 0 && (
    <div className="mt-4">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{title}</p>
      <ul className="mt-2 flex flex-wrap gap-2">
        {names.map((n) => <li key={n}><code className="inline-block rounded-md bg-muted px-2.5 py-1 text-xs">{n}</code></li>)}
      </ul>
    </div>
  );

  return (
    <div>
      <div className="mb-8 flex items-center justify-between">
        <Link to={LIST} className="inline-flex items-center gap-1 rounded-md bg-muted px-2.5 py-1.5 text-sm hover:bg-accent"><ArrowLeft className="size-4" /> {t("customize.plugins")}</Link>
        <Button variant="ghost" size="sm" onClick={() => void copyLink()}><Link2 className="size-4" /> {t("plugins.copyLink")}</Button>
      </div>

      <header className="flex flex-wrap items-start gap-4 sm:gap-5">
        <PluginTile size="lg" />
        <div className="min-w-0 flex-1">
          <h2 className="flex items-center gap-2 text-2xl font-medium">
            {p.label}
            {p.source === "catalog"
              ? <BadgeCheck role="img" aria-label={t("plugins.fromCatalog")} className="size-5 text-muted-foreground" strokeWidth={1.5} />
              : <Badge variant="secondary">{t("plugins.custom")}</Badge>}
          </h2>
          <p className="mt-1 text-muted-foreground">{firstSentence(p.description)}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2 max-sm:w-full">
          {owner && installsDirectly(p) ? (
            <Button disabled={busy === p.name} onClick={() => onAction("install", p)}>{busy === p.name ? t("plugins.installing") : t("plugins.install")}</Button>
          ) : p.state === "installed" && !missing ? (
            <span className="inline-flex items-center gap-1.5 rounded-lg bg-muted px-3 py-1.5 text-sm"><Check className="size-4" /> {t("plugins.installed")}</span>
          ) : status ? (
            <span className={cn("inline-flex items-center rounded-lg bg-muted px-3 py-1.5 text-sm", missing ? "text-warning-foreground" : p.state === "disabled" ? "text-muted-foreground" : "text-error-foreground")}>{status}</span>
          ) : null}
          {!installsDirectly(p) && menu.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" aria-label={t("common.actions")}><MoreVertical className="size-4" /></Button></DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {menu.map((it) => <DropdownMenuItem key={it.label} onSelect={() => onAction(it.action, p)}>{it.label}</DropdownMenuItem>)}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      </header>

      {missing && (
        <div className="mt-8 flex gap-2 rounded-xl border border-warning-foreground/30 bg-warning p-4 text-sm text-warning-foreground">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} />
          <span>{t("plugins.missingHint")}</span>
        </div>
      )}

      <p className="mt-10 max-w-3xl text-sm leading-relaxed">{p.description}</p>

      <h3 className="mt-10 text-lg font-medium">{t("plugins.whatItAdds")}</h3>
      {/* Components are read live from the clone, so there is nothing to show
          until the plugin is on disk — and nothing that can be stale once it is. */}
      {!c ? <p className="mt-3 text-sm text-muted-foreground">{t("plugins.componentsUnknown")}</p>
        : !adds ? <p className="mt-3 text-sm text-muted-foreground">{t("plugins.noComponents")}</p>
        : (
          <>
            {chips(t("plugins.commands"), c.commands)}
            {chips(t("plugins.skills"), c.skills)}
            {chips(t("plugins.agents"), c.agents)}
            {/* The two that run rather than read: worth a sentence each. */}
            {c.hooks && <p className="mt-4 text-sm">{t("plugins.hooksLine")}</p>}
            {c.mcp && <p className="mt-2 text-sm">{t("plugins.mcpLine")}</p>}
          </>
        )}

      <div className="mt-8 flex gap-2 rounded-xl border border-border p-4 text-sm text-muted-foreground">
        <Info className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} />
        <span>{t("plugins.trustNote")}</span>
      </div>

      <dl className="mt-10 grid gap-x-8 gap-y-6 border-t border-border pt-8 text-sm sm:grid-cols-2">
        <Field term={t("plugins.repository")}>
          {/* An orphan row carries no manifest, so no repo: a link would be a
              broken '<origin>/tree/'. Say nothing rather than something false. */}
          {p.repo
            ? <a href={treeUrl(p)} target="_blank" rel="noreferrer" title={t("plugins.openRepo")} className="break-all underline-offset-2 hover:underline">{repoLabel(p.repo)} ↗</a>
            : "—"}
        </Field>
        <Field term={t("plugins.pinnedRef")}><span className="break-all font-mono text-xs">{p.ref}</span></Field>
        <Field term={t("plugins.subdirectory")}><span className="break-all font-mono text-xs">{p.subdir || "—"}</span></Field>
        <Field term={t("plugins.scopesLabel")}>{scopes}</Field>
        <Field term={t("plugins.madeBy")}>{c?.author || "—"}</Field>
        <Field term={t("plugins.version")}>{p.version || "—"}</Field>
        <Field term={t("plugins.added")}>{p.installed_at ? fmtDateTime(p.installed_at, locale) : "—"}</Field>
      </dl>
      {confirmDialog}
    </div>
  );
}

function Field({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">{term}</dt>
      <dd className="mt-1">{children}</dd>
    </div>
  );
}
