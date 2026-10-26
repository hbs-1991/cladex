/** One password field per declared secret; Save stores them and connects. */

import { useState } from "react";
import type { ConnectorWire } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useT } from "@/i18n";
import { errorText } from "@/lib/connectorErrors";

export function SecretsDialog({ c, open, onOpenChange, onSave }: {
  c: ConnectorWire; open: boolean; onOpenChange: (o: boolean) => void; onSave: (values: Record<string, string>) => Promise<void>;
}) {
  const t = useT();
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const filled = c.secrets.every((s) => s.set || values[s.name]?.trim());
  const submit = async () => {
    setBusy(true); setError(null);
    try { await onSave(Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim()))); onOpenChange(false); }
    catch (e) { setError(errorText(t, e)); }
    finally { setBusy(false); }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("connectors.secretsTitle", { label: c.label })}</DialogTitle>
          <DialogDescription>{t("connectors.secretsLead")}</DialogDescription>
        </DialogHeader>
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
          {c.secrets.map((s) => (
            <div key={s.name} className="grid gap-1.5">
              <Label htmlFor={`secret-${s.name}`}>{s.prompt}</Label>
              <Input id={`secret-${s.name}`} type="password" autoComplete="off" value={values[s.name] ?? ""} placeholder={s.set ? "••••••••" : ""}
                onChange={(e) => setValues({ ...values, [s.name]: e.target.value })} />
            </div>
          ))}
          {error && <p role="alert" className="text-sm text-error-foreground">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
            <Button type="submit" disabled={!filled || busy}>{t("connectors.saveAndConnect")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
