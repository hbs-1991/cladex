/** Reading a plugin before it becomes real.
 *
 * Opening the dialog runs phase one: the server fetches the plugin at the
 * manifest's pinned revision and answers with what it found, plus a token bound
 * to those exact bytes. Nothing is registered yet, and `compose.for_scope` reads
 * registry rows — so the tree on disk is inert until Install is pressed.
 *
 * The surface goes into a `<pre>` verbatim, the way `DraftDialog` renders a
 * draft's: it is the text the owner is being asked to vouch for, and a markdown
 * renderer would quietly swallow a character that changes what gets loaded.
 *
 * The servers strip is the reason this dialog exists at all. A plugin's own
 * `.mcp.json` is read by the CLI, not by this catalog, so those servers get no
 * connector row, no probe and no review — the only moment a person can see them
 * is here. The ones that would run a local command never reach this screen: the
 * server refuses them in phase one with `local_server`. */

import { useEffect, useState } from "react";
import { Server, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import type { PluginPreview, PluginWire } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useT } from "@/i18n";
import { pluginError } from "@/lib/pluginMenu";
import { useStore } from "@/store";

type Props = { plugin: PluginWire; open: boolean; onOpenChange: (open: boolean) => void };

export function PluginInstallDialog({ plugin, open, onOpenChange }: Props) {
  // Mounted with the dialog, so reopening fetches again rather than offering a
  // token for a tree that may have been replaced since.
  return <Dialog open={open} onOpenChange={onOpenChange}>{open && <Body plugin={plugin} onOpenChange={onOpenChange} />}</Dialog>;
}

function Body({ plugin, onOpenChange }: Omit<Props, "open">) {
  const t = useT();
  const s = useStore();
  const [preview, setPreview] = useState<PluginPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // A reinstall has already replaced the files by the time this dialog opens, so
  // Cancel must not "clean up" a plugin that is registered and working.
  const fresh = plugin.state === "available";

  const load = () => s.fetchPlugin(plugin.name).then(setPreview).catch((e: unknown) => setError(pluginError(t, e)));
  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const confirm = async () => {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      await s.confirmInstallPlugin(plugin.name, preview.token);
      toast(t("plugins.installedToast", { label: plugin.label }));
      onOpenChange(false);
    } catch (e) {
      setError(pluginError(t, e));
      // The tree no longer matches what was read: drop the surface that is no
      // longer true and fetch again, so Install is never offered against bytes
      // nobody has seen.
      if (e instanceof ApiError && e.code === "stale_install") { setPreview(null); await load(); }
    } finally { setBusy(false); }
  };

  const cancel = () => {
    onOpenChange(false);
    if (fresh && preview) void s.cancelInstallPlugin(plugin.name).catch(() => undefined);
  };

  const servers = preview?.servers ?? [];

  return (
    <DialogContent className="sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle>{t("plugins.fetchTitle", { label: plugin.label })}</DialogTitle>
        <DialogDescription>{fresh ? t("plugins.fetchNote") : t("plugins.reinstallReplaced")}</DialogDescription>
      </DialogHeader>

      {servers.length > 0 && (
        <div className="flex gap-2 rounded-lg border border-warning-foreground/30 bg-warning p-3 text-sm text-warning-foreground">
          <Server className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} />
          <span>
            <span className="block font-medium">{t("plugins.bringsServers", { n: servers.length })}</span>
            {t("plugins.bringsServersNote")}
            <ul className="mt-1.5 space-y-0.5 font-mono text-xs">
              {servers.map((srv) => <li key={srv.name}>{srv.name} · {srv.transport} · {srv.target}</li>)}
            </ul>
          </span>
        </div>
      )}
      {preview?.components?.hooks && (
        <div className="flex gap-2 rounded-lg border border-warning-foreground/30 bg-warning p-3 text-sm text-warning-foreground">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} />
          <span className="font-medium">{t("plugins.hooksLine")}</span>
        </div>
      )}
      {error && <p role="alert" className="text-sm text-error-foreground">{error}</p>}

      <h3 className="text-sm font-medium">{t("plugins.whatItWillAdd")}</h3>
      {preview
        ? <pre className="max-h-[45vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted p-3 font-mono text-xs leading-relaxed">{preview.surface}</pre>
        : !error && <p className="text-sm text-muted-foreground">{t("plugins.fetching")}</p>}

      <DialogFooter>
        <Button variant="secondary" onClick={cancel}>{t("common.cancel")}</Button>
        <Button disabled={!preview || busy} onClick={() => void confirm()}>
          {busy ? t("plugins.installing") : t("plugins.confirmInstall")}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
