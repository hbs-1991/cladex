/** The Plugins tab: the drafts queue, then a two-column grid of cards — the
 * reviewed catalog under "Top plugins", approved drafts under "Yours".
 *
 * "Add plugin" files a manifest; it does not add a plugin. A connector is a URL,
 * so it can be created outright; a plugin is code that loads inside the agent, so
 * the way in stays draft → human approval → install, and both gates are on this
 * page: the approval in the section above the grid, and what the clone actually
 * contains on the install dialog. */

import { useEffect, useState } from "react";
import type { PluginWire } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { useStore } from "@/store";
import { AddPluginDialog } from "./AddPluginDialog";
import { PendingDrafts } from "./PendingDrafts";
import { PluginCard } from "./PluginCard";
import { usePluginActions } from "./usePluginActions";

export function PluginsTab() {
  const t = useT();
  const s = useStore();
  const owner = s.me?.role === "owner";
  const { onAction, busy, confirmDialog } = usePluginActions();
  const [adding, setAdding] = useState(false);
  useEffect(() => { void s.loadPlugins(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const catalog = s.plugins.filter((p) => p.source === "catalog");
  const custom = s.plugins.filter((p) => p.source === "custom");
  const group = (title: string, items: PluginWire[]) => items.length > 0 && (
    <section className="mb-8">
      <h2 className="mb-3 text-sm font-medium">{title}</h2>
      <ul className="grid gap-3 sm:grid-cols-2">
        {/* `owner` on a card is the clone's gate, not the card's: install and
            delete fetch and remove code every tree loads. Enable and disable
            write the caller's own row, so the card offers them to everybody. */}
        {items.map((p) => <PluginCard key={p.name} p={p} owner={!!owner} busy={busy === p.name} onAction={onAction} />)}
      </ul>
    </section>
  );

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <p className="min-w-0 flex-1 basis-64 text-sm text-muted-foreground">{t("plugins.subtitle")}</p>
        {owner && <Button onClick={() => setAdding(true)}>{t("plugins.addPlugin")}</Button>}
      </div>
      <PendingDrafts kind="plugin" />
      {group(t("plugins.top"), catalog)}
      {group(t("plugins.yours"), custom)}
      {s.pluginsLoaded && s.plugins.length === 0 && <p className="py-6 text-sm text-muted-foreground">{t("plugins.empty")}</p>}
      {confirmDialog}
      {owner && <AddPluginDialog open={adding} onOpenChange={setAdding} />}
    </div>
  );
}
