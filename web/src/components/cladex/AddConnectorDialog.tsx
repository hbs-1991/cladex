/** A remote MCP server by URL: name, label, URL, how it authenticates, and the
 * client credentials for a provider that will not register clients itself. */

import { useState, type ChangeEvent, type ComponentProps } from "react";
import { ChevronDown } from "lucide-react";
import { api } from "@/api/client";
import type { ConnectorCreate, ConnectorWire } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useT } from "@/i18n";
import { errorText } from "@/lib/connectorErrors";

const NAME_RE = /^[a-z0-9][a-z0-9_-]*$/;
type Auth = ConnectorCreate["auth"];
type Field = "name" | "label" | "url" | "header" | "token" | "client_id" | "client_secret";

export function AddConnectorDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: (c: ConnectorWire) => void }) {
  const t = useT();
  const [f, setF] = useState({ name: "", label: "", url: "", auth: "oauth" as Auth, header: "Authorization", token: "", client_id: "", client_secret: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (k: Field) => (e: ChangeEvent<HTMLInputElement>) => setF((p) => ({ ...p, [k]: e.target.value }));
  const nameOk = NAME_RE.test(f.name);
  const ready = nameOk && f.url.startsWith("https://") && (f.auth !== "header" || f.token.trim() !== "");
  const submit = async () => {
    setBusy(true); setError(null);
    const body: ConnectorCreate = { name: f.name, label: f.label || f.name, url: f.url, auth: f.auth };
    if (f.auth === "header") { body.header = f.header || "Authorization"; body.token = f.token; }
    if (f.client_id) { body.client_id = f.client_id; body.client_secret = f.client_secret; }
    try { onCreated(await api.connectors.create(body)); onOpenChange(false); }
    catch (e) { setError(errorText(t, e)); }
    finally { setBusy(false); }
  };
  const field = (id: Field, label: string, extra: Partial<ComponentProps<typeof Input>> = {}) => (
    <div className="grid gap-1.5"><Label htmlFor={`add-${id}`}>{label}</Label><Input id={`add-${id}`} value={f[id]} onChange={set(id)} {...extra} /></div>
  );
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader><DialogTitle>{t("connectors.addTitle")}</DialogTitle><DialogDescription>{t("connectors.addLead")}</DialogDescription></DialogHeader>
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
          {field("name", t("connectors.name"), { placeholder: "my-api", "aria-invalid": f.name !== "" && !nameOk })}
          {f.name !== "" && !nameOk && <p className="-mt-3 text-xs text-error-foreground">{t("connectors.nameHint")}</p>}
          {field("label", t("connectors.label"))}
          {field("url", t("connectors.url"), { placeholder: "https://…/mcp", type: "url" })}
          <div className="grid gap-1.5">
            <Label htmlFor="add-auth">{t("connectors.auth")}</Label>
            <Select value={f.auth} onValueChange={(v) => setF({ ...f, auth: v as Auth })}>
              <SelectTrigger id="add-auth" aria-label={t("connectors.auth")}><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="oauth">{t("connectors.authOauth")}</SelectItem>
                <SelectItem value="header">{t("connectors.authHeader")}</SelectItem>
                <SelectItem value="none">{t("connectors.authNone")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {f.auth === "header" && field("header", t("connectors.headerName"))}
          {f.auth === "header" && field("token", t("connectors.token"), { type: "password", autoComplete: "off" })}
          {f.auth === "oauth" && (
            <Collapsible>
              <CollapsibleTrigger className="flex items-center gap-1 text-sm text-muted-foreground">{t("connectors.advanced")} <ChevronDown className="size-3.5" /></CollapsibleTrigger>
              <CollapsibleContent className="mt-3 grid gap-4">
                {field("client_id", t("connectors.clientId"))}
                {field("client_secret", t("connectors.clientSecret"), { type: "password", autoComplete: "off" })}
              </CollapsibleContent>
            </Collapsible>
          )}
          {error && <p role="alert" className="text-sm text-error-foreground">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
            <Button type="submit" disabled={!ready || busy}>{t("connectors.add")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
