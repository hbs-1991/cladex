/** The actions on a connector, shared by the Connectors tab and the connector
 * page: connect (OAuth redirect, secrets dialog or a plain probe), enable,
 * disable, test, disconnect — each toasting its outcome. */

import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/api/client";
import type { ConnectorWire } from "@/api/types";
import { useT } from "@/i18n";
import { errorText } from "@/lib/connectorErrors";
import type { ConnectorAction } from "@/lib/connectorMenu";
import { goTo } from "@/lib/navigate";
import { useStore } from "@/store";
import { ConfirmDialog } from "./AskDialog";
import { SecretsDialog } from "./SecretsDialog";

export function useConnectorActions() {
  const t = useT();
  const s = useStore();
  const [secretsFor, setSecretsFor] = useState<ConnectorWire | null>(null);
  const [disconnecting, setDisconnecting] = useState<ConnectorWire | null>(null);

  const run = async (fn: () => Promise<void>) => { try { await fn(); } catch (e) { toast.error(errorText(t, e)); } };
  const connect = (c: ConnectorWire) => run(async () => {
    if (c.auth === "oauth") { goTo((await api.connectors.oauthStart(c.name)).url); return; }
    if (c.secrets.some((x) => !x.set)) { setSecretsFor(c); return; }
    await s.connectConnector(c.name);
  });
  const onAction = (a: ConnectorAction, c: ConnectorWire) => {
    if (a === "connect") void connect(c);
    else if (a === "enable") void run(() => s.enableConnector(c.name));
    else if (a === "disable") void run(() => s.disableConnector(c.name));
    else if (a === "test") void run(async () => {
      const r = await api.connectors.test(c.name);
      if (r.ok) toast(t("connectors.testOk", { label: c.label, n: r.tools.length })); else toast.error(t("connectors.testFailed", { label: c.label, error: r.error }));
    });
    else if (a === "disconnect") setDisconnecting(c);
  };

  const secretsDialog = (
    <>
      {secretsFor && (
        <SecretsDialog c={secretsFor} open onOpenChange={(o) => { if (!o) setSecretsFor(null); }}
          onSave={async (values) => { await api.connectors.secrets(secretsFor.name, values); await s.connectConnector(secretsFor.name); }} />
      )}
      {disconnecting && (
        <ConfirmDialog open onOpenChange={(o) => { if (!o) setDisconnecting(null); }}
          title={t(disconnecting.state === "available" ? "connectors.remove" : "connectors.disconnect")}
          description={t("connectors.disconnectConfirm", { label: disconnecting.label })}
          confirmLabel={t(disconnecting.state === "available" ? "connectors.remove" : "connectors.disconnect")} destructive
          onConfirm={() => { const c = disconnecting; void run(async () => { await s.removeConnector(c.name); toast(t("connectors.disconnected", { label: c.label })); }); }} />
      )}
    </>
  );
  return { onAction, connect, secretsDialog };
}
