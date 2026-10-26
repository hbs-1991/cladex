/** The actions on a plugin, shared by the Plugins tab and the plugin page:
 * install (also the reinstall and the follow-the-pin update), enable, disable,
 * remove — each toasting its outcome.
 *
 * Install opens `PluginInstallDialog` rather than doing anything: the server's
 * first phase only fetches, and what it fetched has to be read before it is
 * registered. The dialog owns both phases; everything else here is one call. */

import { useState } from "react";
import { toast } from "sonner";
import type { PluginWire } from "@/api/types";
import { useT } from "@/i18n";
import { pluginError, type PluginAction } from "@/lib/pluginMenu";
import { useStore } from "@/store";
import { ConfirmDialog } from "./AskDialog";
import { PluginInstallDialog } from "./PluginInstallDialog";

export function usePluginActions() {
  const t = useT();
  const s = useStore();
  const [removing, setRemoving] = useState<PluginWire | null>(null);
  const [installing, setInstalling] = useState<PluginWire | null>(null);
  // The one plugin whose install dialog is open: the card behind it shows the
  // spinner and refuses a second click, which is all `busy` ever meant.
  const busy = installing?.name ?? null;

  const run = async (fn: () => Promise<void>) => { try { await fn(); } catch (e) { toast.error(pluginError(t, e)); } };

  const onAction = (a: PluginAction, p: PluginWire) => {
    if (a === "install") setInstalling(p);
    else if (a === "enable") void run(async () => { await s.enablePlugin(p.name); toast(t("plugins.enabledToast", { label: p.label })); });
    else if (a === "disable") void run(async () => { await s.disablePlugin(p.name); toast(t("plugins.disabledToast", { label: p.label })); });
    else if (a === "remove") setRemoving(p);
  };

  const confirmDialog = removing && (
    <ConfirmDialog open onOpenChange={(o) => { if (!o) setRemoving(null); }}
      title={t("plugins.remove")}
      description={t("plugins.removeConfirm", { label: removing.label })}
      confirmLabel={t("plugins.remove")} destructive
      onConfirm={() => { const p = removing; void run(async () => { await s.removePlugin(p.name); toast(t("plugins.removedToast", { label: p.label })); }); }} />
  );

  const installDialog = installing && (
    <PluginInstallDialog plugin={installing} open
      onOpenChange={(o) => { if (!o) setInstalling(null); }} />
  );

  return { onAction, busy, confirmDialog: <>{confirmDialog}{installDialog}</> };
}
