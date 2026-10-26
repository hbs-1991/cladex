/** Adding a plugin to the catalog: the manifest a person would otherwise have to
 * write by hand, as a form.
 *
 * It files a draft — it does not install anything. The queue above the grid is where
 * it is read and approved, and only then does the entry exist; installing it is a
 * separate act with its own screen showing what the clone actually contains. Two
 * steps for what looks like one job, because the thing being added is code that
 * loads inside the agent, and the fields below are only a claim about it.
 *
 * The link at the top is a convenience, not a source of truth: it fills the fields
 * underneath, which stay visible and editable, and the server validates the result.
 */

import { useState } from "react";
import { toast } from "sonner";
import type { ComponentProps } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/i18n";
import { draftError } from "@/lib/pluginMenu";
import { nameFrom, parseSource } from "@/lib/pluginSource";
import { useStore } from "@/store";

const NAME_RE = /^[a-z0-9][a-z0-9_-]*$/;
/** A full sha or a release tag — the same rule the server enforces, applied here so
 *  the mistake is caught while the field is still under the cursor. */
const PIN_RE = /^(?:[0-9a-f]{40}|v?\d+(?:\.\d+)*[A-Za-z0-9.\-+_]*)$/;
type Field = "link" | "repo" | "ref" | "subdir" | "name" | "label" | "description";

const EMPTY = { link: "", repo: "", ref: "", subdir: "", name: "", label: "", description: "" };

export function AddPluginDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const t = useT();
  const createPluginDraft = useStore((s) => s.createPluginDraft);
  const [f, setF] = useState({ ...EMPTY });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (k: Field, v: string) => setF((p) => ({ ...p, [k]: v }));

  /** A pasted link fills what it knows and leaves the rest alone: a ref it could not
   *  read as a pin (a branch) must not wipe one the owner already typed. */
  const pasted = (link: string) => {
    const from = parseSource(link);
    setF((p) => (from ? {
      ...p, link, repo: from.repo, subdir: from.subdir,
      ref: from.ref || p.ref,
      name: from.name || p.name,
      label: p.label || from.name.replace(/[-_]+/g, " "),
    } : { ...p, link }));
  };

  const nameOk = NAME_RE.test(f.name);
  const refOk = PIN_RE.test(f.ref);
  const repoOk = f.repo.startsWith("https://");
  const ready = nameOk && refOk && repoOk && f.description.trim() !== "";

  const close = (o: boolean) => { onOpenChange(o); if (!o) { setF({ ...EMPTY }); setError(null); } };

  const submit = async () => {
    setBusy(true); setError(null);
    try {
      const d = await createPluginDraft({
        name: f.name, repo: f.repo, ref: f.ref, subdir: f.subdir,
        label: f.label.trim(), description: f.description.trim(),
      });
      toast(t("plugins.queuedToast", { label: d.label }));
      close(false);
    } catch (e) { setError(draftError(t, e)); }
    finally { setBusy(false); }
  };

  const field = (id: Field, label: string, hint?: string, extra: Partial<ComponentProps<typeof Input>> = {}) => (
    <div className="grid gap-1.5">
      <Label htmlFor={`add-plugin-${id}`}>{label}</Label>
      <Input id={`add-plugin-${id}`} value={f[id]} onChange={(e) => set(id, e.target.value)} {...extra} />
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{t("plugins.addTitle")}</DialogTitle>
          <DialogDescription>{t("plugins.addLead")}</DialogDescription>
        </DialogHeader>
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
          {/* The fields scroll, the decision does not: six labelled inputs plus their
              hints do not fit a laptop screen, and a submit button that drifts off the
              bottom edge is how a form gets abandoned. */}
          <div className="-mr-1 grid max-h-[52vh] gap-4 overflow-y-auto pr-1">
          <div className="grid gap-1.5">
            <Label htmlFor="add-plugin-link">{t("plugins.linkLabel")}</Label>
            <Input id="add-plugin-link" value={f.link} onChange={(e) => pasted(e.target.value)}
              placeholder="https://github.com/owner/repo/tree/<sha>/plugin" />
            <p className="text-xs text-muted-foreground">{t("plugins.linkHint")}</p>
          </div>

          {field("repo", t("plugins.repository"), undefined,
            { placeholder: "https://github.com/owner/repo", "aria-invalid": f.repo !== "" && !repoOk })}
          {field("ref", t("plugins.refLabel"), t("plugins.refHint"),
            { placeholder: "v1.2.0", "aria-invalid": f.ref !== "" && !refOk })}
          {field("subdir", t("plugins.subdirLabel"), t("plugins.subdirHint"), { placeholder: "plugins/radar" })}
          {field("name", t("plugins.nameLabel"), nameOk || f.name === "" ? undefined : t("plugins.nameHint"),
            { placeholder: "content-radar", "aria-invalid": f.name !== "" && !nameOk,
              onChange: (e) => set("name", nameFrom(e.target.value) || e.target.value.toLowerCase()) })}
          {field("label", t("plugins.labelLabel"), undefined, { placeholder: "Content Radar" })}

          <div className="grid gap-1.5">
            <Label htmlFor="add-plugin-description">{t("plugins.descriptionLabel")}</Label>
            <Textarea id="add-plugin-description" rows={3} value={f.description}
              onChange={(e) => set("description", e.target.value)} placeholder={t("plugins.descriptionPlaceholder")} />
            <p className="text-xs text-muted-foreground">{t("plugins.descriptionHint")}</p>
          </div>

          </div>

          <p className="rounded-lg bg-muted p-3 text-xs text-muted-foreground">{t("plugins.addNote")}</p>
          {error && <p role="alert" className="text-sm text-error-foreground">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => close(false)}>{t("common.cancel")}</Button>
            <Button type="submit" disabled={!ready || busy}>{t("plugins.addSubmit")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
