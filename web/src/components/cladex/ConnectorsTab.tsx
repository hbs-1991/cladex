/** The Connectors tab: a two-column grid of cards — the catalog under "Top
 * connectors", the owner's own entries under "Yours" — plus the OAuth return
 * (`?connected=` / `?error=`) and the custom-connector dialog. */

import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";
import type { ConnectorWire } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { isKnownError } from "@/lib/connectorErrors";
import { useStore } from "@/store";
import { AddConnectorDialog } from "./AddConnectorDialog";
import { ConnectorCard } from "./ConnectorCard";
import { PendingDrafts } from "./PendingDrafts";
import { useConnectorActions } from "./useConnectorActions";

/** The order the list route returns: catalog first, then custom, both by label. */
const byListOrder = (a: ConnectorWire, b: ConnectorWire) =>
  Number(a.source !== "catalog") - Number(b.source !== "catalog") || a.label.localeCompare(b.label);

export function ConnectorsTab() {
  const t = useT();
  const s = useStore();
  const owner = s.me?.role === "owner";
  const [params, setParams] = useSearchParams();
  const [adding, setAdding] = useState(false);
  const { onAction, connect, secretsDialog } = useConnectorActions();
  useEffect(() => { void s.loadConnectors(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const connected = params.get("connected"), error = params.get("error");
    if (!connected && !error) return;
    if (!s.connectorsLoaded) return;                 // the toast names the connector, so wait for the list
    if (connected) toast(t("connectors.connectedToast", { label: s.connectors.find((c) => c.name === connected)?.label ?? connected }));
    if (error) toast.error(isKnownError(error) ? t(`connectors.errors.${error}`, { message: error }) : error);
    setParams({}, { replace: true });
  }, [params, s.connectors, s.connectorsLoaded]); // eslint-disable-line react-hooks/exhaustive-deps

  const catalog = s.connectors.filter((c) => c.source === "catalog");
  const custom = s.connectors.filter((c) => c.source === "custom");
  const group = (title: string, items: ConnectorWire[]) => items.length > 0 && (
    <section className="mb-8">
      <h2 className="mb-3 text-sm font-medium">{title}</h2>
      <ul className="grid gap-3 sm:grid-cols-2">
        {/* Every control on a card — connect, secrets, enable, disable, delete —
            acts on the caller's own row now, so the card is live for everyone.
            New connector, which writes a manifest into the shared catalog, is
            the one control below that is still the owner's. */}
        {items.map((c) => <ConnectorCard key={c.name} c={c} owner onAction={onAction} />)}
      </ul>
    </section>
  );

  return (
    <div>
      <div className="mb-6 flex items-center gap-3">
        <p className="flex-1 text-sm text-muted-foreground">{t("connectors.subtitle")}</p>
        {owner && <Button aria-label={t("connectors.addCustom")} onClick={() => setAdding(true)}>{t("connectors.add")}</Button>}
      </div>
      <PendingDrafts kind="mcp" />
      {group(t("connectors.top"), catalog)}
      {group(t("connectors.yours"), custom)}
      {s.connectorsLoaded && s.connectors.length === 0 && <p className="py-6 text-sm text-muted-foreground">{t("connectors.empty")}</p>}
      {secretsDialog}
      <AddConnectorDialog open={adding} onOpenChange={setAdding}
        onCreated={(c) => { useStore.setState((st) => ({ connectors: [...st.connectors, c].sort(byListOrder) })); if (c.auth === "oauth") void connect(c); }} />
    </div>
  );
}
