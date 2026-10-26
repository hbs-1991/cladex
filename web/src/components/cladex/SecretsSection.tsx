/** Settings → Secrets: the environment variables the agent's extensions launch with.
 *
 * One list, two kinds of row. A stored secret carries the day it was last written;
 * a name a connector declares and nobody has filled in yet is here too, as "not
 * set" — so the list is what the deployment needs, not only what it has. A value
 * is never read back: the server answers with names, and this panel only ever
 * sends one.
 */

import { useEffect, useState } from "react";
import { KeyRound, Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import type { SecretWire } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useT, type Key } from "@/i18n";
import { dayLabel } from "@/lib/dates";
import { useStore } from "@/store";
import { ConfirmDialog } from "./AskDialog";

/** The domain's rule, the same one the server applies — checked here so the button
 *  can say no before a round trip, never instead of the server's check. */
const NAME_RE = /^[A-Z][A-Z0-9_]*$/;

type T = (k: Key, p?: Record<string, string | number>) => string;

/** The two codes this panel can provoke; anything else is the server's own words. */
function errorText(t: T, e: unknown): string {
  if (e instanceof ApiError && (e.code === "bad_request" || e.code === "forbidden")) {
    return t(`secrets.errors.${e.code}`);
  }
  return e instanceof Error ? e.message : t("common.error");
}

function Row({ s, onRemove }: { s: SecretWire; onRemove: (name: string) => void }) {
  const t = useT();
  const [confirming, setConfirming] = useState(false);
  const note = [
    s.used_by.length ? t("secrets.usedBy", { names: s.used_by.join(", ") }) : "",
    s.set && s.updated_at ? t("secrets.updated", { date: dayLabel(s.updated_at) }) : t("secrets.notSet"),
  ].filter(Boolean).join(" · ");
  return (
    <li className="flex items-center gap-3 border-b border-border py-2.5 last:border-b-0">
      <KeyRound aria-hidden strokeWidth={1.75}
        className={`size-4 shrink-0 ${s.set ? "text-muted-foreground" : "text-muted-foreground/50"}`} />
      <div className="min-w-0 flex-1">
        <p className="truncate font-mono text-sm">{s.name}</p>
        <p className="truncate text-xs text-muted-foreground">{note}</p>
      </div>
      {/* Only a stored secret can be forgotten: a declared-but-unset name is the
          catalog's, and deleting it would delete nothing. */}
      {s.set && (
        <Button variant="ghost" size="icon" aria-label={t("common.delete")} onClick={() => setConfirming(true)}>
          <Trash2 className="size-4" strokeWidth={1.75} />
        </Button>
      )}
      <ConfirmDialog open={confirming} onOpenChange={setConfirming} destructive
        title={t("secrets.removeConfirm", { name: s.name })} confirmLabel={t("common.delete")}
        onConfirm={() => onRemove(s.name)} />
    </li>
  );
}

function AddForm() {
  const t = useT();
  const put = useStore((s) => s.putSecret);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = NAME_RE.test(name) && value !== "";
  const submit = async () => {
    setBusy(true);
    try {
      await put(name, value);
      toast.success(t("secrets.saved", { name }));
      setName(""); setValue(""); setOpen(false);
    } catch (e) { toast.error(errorText(t, e)); }
    finally { setBusy(false); }
  };
  if (!open) {
    return (
      <div>
        <Button variant="secondary" size="sm" onClick={() => setOpen(true)}>
          <Plus className="size-4" strokeWidth={1.75} />
          {t("secrets.add")}
        </Button>
      </div>
    );
  }
  return (
    <form className="grid gap-3 rounded-lg border border-border p-3"
      onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <div className="grid gap-1.5">
        <Label htmlFor="secret-name">{t("secrets.name")}</Label>
        <Input id="secret-name" autoFocus autoComplete="off" spellCheck={false} className="font-mono"
          placeholder="OPENAI_API_KEY" value={name} onChange={(e) => setName(e.target.value)} />
        <p className="text-xs text-muted-foreground">{t("secrets.nameHint")}</p>
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="secret-value">{t("secrets.value")}</Label>
        <Input id="secret-value" type="password" autoComplete="off" value={value}
          onChange={(e) => setValue(e.target.value)} />
      </div>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={() => setOpen(false)}>{t("common.cancel")}</Button>
        <Button type="submit" disabled={!valid || busy}>{t("common.save")}</Button>
      </div>
    </form>
  );
}

export function SecretsSection() {
  const t = useT();
  const secrets = useStore((s) => s.secrets);
  const loaded = useStore((s) => s.secretsLoaded);
  const load = useStore((s) => s.loadSecrets);
  const forget = useStore((s) => s.forgetSecret);
  const [error, setError] = useState<string | null>(null);

  // The dialog mounts this panel when it opens, so every opening is a fresh read —
  // a secret set from Telegram in the meantime is on screen without a reload.
  useEffect(() => { load().catch((e: unknown) => setError(errorText(t, e))); },
    []);                                     // eslint-disable-line react-hooks/exhaustive-deps

  const remove = async (name: string) => {
    try { await forget(name); toast.success(t("secrets.removed", { name })); }
    catch (e) { toast.error(errorText(t, e)); }
  };

  return (
    <div className="grid gap-4">
      <p className="text-sm text-muted-foreground">{t("secrets.lead")}</p>
      <AddForm />
      {error && <p role="alert" className="text-sm text-error-foreground">{error}</p>}
      {!loaded && !error
        ? <Loader2 aria-label={t("common.loading")} className="size-4 animate-spin text-muted-foreground" />
        : secrets.length === 0 && !error
          ? <p className="text-sm text-muted-foreground">{t("secrets.empty")}</p>
          : <ul>{secrets.map((s) => <Row key={s.name} s={s} onRemove={(n) => void remove(n)} />)}</ul>}
    </div>
  );
}
