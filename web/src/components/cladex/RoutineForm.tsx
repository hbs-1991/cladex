/** Create/edit form for a routine: name, instructions, a frequency picked from
 * presets (or typed as cron / interval / date), the model it runs on and the
 * timeout. */

import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useLocale, useT } from "@/i18n";
import { effortKey, findModel, keepEffort } from "@/lib/models";
import { PRESETS, presetLabel } from "@/lib/schedule";
import { useStore } from "@/store";

export type RoutineInput = {
  name: string; schedule: string; prompt: string; timeout_seconds: number | null;
  model: string | null; effort: string | null;
};
const CUSTOM = "custom";
// Sentinels, not empty strings: a Radix select item may not have an empty value,
// and no catalog id is ever spelled like these.
const DEPLOY = "deploy";  // "unset" — the cron model, then the deploy default
const AUTO = "auto";      // "no effort flag"

export function RoutineForm({ initial, onSubmit, onCancel }: { initial?: Partial<RoutineInput>; onSubmit: (v: RoutineInput) => Promise<void>; onCancel?: () => void }) {
  const t = useT();
  const locale = useLocale();
  const models = useStore((s) => s.models);
  const loadModels = useStore((s) => s.loadModels);
  useEffect(() => { void loadModels(); }, [loadModels]);
  const first = initial?.schedule ?? PRESETS[0]!.value;
  const [v, setV] = useState<RoutineInput>({ name: initial?.name ?? "", schedule: first, prompt: initial?.prompt ?? "", timeout_seconds: initial?.timeout_seconds ?? 180,
    model: initial?.model ?? null, effort: initial?.effort ?? null });
  const [freq, setFreq] = useState(PRESETS.some((p) => p.value === first) ? first : CUSTOM);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setError(null);
    try { await onSubmit(v); } catch (err) { setError(err instanceof Error ? err.message : t("common.error")); } finally { setBusy(false); }
  };
  const pick = (value: string) => {
    setFreq(value);
    if (value !== CUSTOM) setV((s) => ({ ...s, schedule: value }));
    else if (PRESETS.some((p) => p.value === v.schedule)) setV((s) => ({ ...s, schedule: "" }));
  };
  const chosen = findModel(models, v.model);
  const effortName = (effort: string) => { const k = effortKey(effort); return k ? t(k) : effort; };
  return (
    <form onSubmit={submit} className="space-y-5">
      <div className="space-y-1.5">
        <Label htmlFor="name">{t("routines.form.name")} <span aria-hidden className="text-error-foreground">*</span></Label>
        <Input id="name" value={v.name} onChange={(e) => setV({ ...v, name: e.target.value })} required maxLength={80} placeholder={t("routines.form.namePlaceholder")} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="prompt">{t("routines.form.instructions")} <span aria-hidden className="text-error-foreground">*</span></Label>
        <Textarea id="prompt" rows={5} value={v.prompt} onChange={(e) => setV({ ...v, prompt: e.target.value })} required
          placeholder={t("routines.form.instructionsPlaceholder")} />
      </div>
      <div className="grid grid-cols-[6rem_minmax(0,1fr)] items-center gap-x-3 sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-x-4 gap-y-3">
        <Label htmlFor="frequency">{t("routines.form.frequency")}</Label>
        <Select value={freq} onValueChange={pick}>
          <SelectTrigger id="frequency" className="w-full sm:w-56"><SelectValue /></SelectTrigger>
          <SelectContent>
            {PRESETS.map((p) => <SelectItem key={p.value} value={p.value}>{presetLabel(p, locale)}</SelectItem>)}
            <SelectItem value={CUSTOM}>{t("routines.form.custom")}</SelectItem>
          </SelectContent>
        </Select>
        {freq === CUSTOM && (
          <>
            <Label htmlFor="schedule">{t("routines.form.schedule")}</Label>
            <div className="space-y-1">
              <Input id="schedule" value={v.schedule} onChange={(e) => setV({ ...v, schedule: e.target.value })} required className="font-mono" />
              <p className="text-xs text-muted-foreground">{t("routines.form.hint")}</p>
            </div>
          </>
        )}
        <Label htmlFor="timeout_seconds">{t("routines.form.timeout")}</Label>
        <Input id="timeout_seconds" type="number" min={30} max={1800} className="w-full sm:w-56" value={v.timeout_seconds ?? ""}
          onChange={(e) => setV({ ...v, timeout_seconds: e.target.value ? Number(e.target.value) : null })} />

        {/* Unset is the honest default: a routine then follows `cron_model`, and a
            model that later leaves the offered list falls back the same way. An
            effort the chosen model does not take is dropped rather than sent. */}
        <Label htmlFor="model">{t("models.model")}</Label>
        <Select value={v.model ?? DEPLOY} onValueChange={(id) => setV((s) => ({ ...s, model: id === DEPLOY ? null : id,
          effort: keepEffort(findModel(models, id), s.effort) }))}>
          <SelectTrigger id="model" className="w-full sm:w-56"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={DEPLOY}>{t("models.deployDefault")}</SelectItem>
            {models.map((m) => <SelectItem key={m.id} value={m.id}>{m.label}</SelectItem>)}
          </SelectContent>
        </Select>

        <Label htmlFor="effort">{t("models.effort")}</Label>
        <Select value={v.effort ?? AUTO} onValueChange={(e) => setV({ ...v, effort: e === AUTO ? null : e })}>
          <SelectTrigger id="effort" className="w-full sm:w-56"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={AUTO}>{chosen?.default_effort ? t("models.autoWith", { effort: effortName(chosen.default_effort) }) : t("models.auto")}</SelectItem>
            {(chosen?.efforts ?? []).map((e) => <SelectItem key={e} value={e}>{effortName(e)}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>
      {error && <p role="alert" className="text-sm text-error-foreground">{error}</p>}
      <div className="flex justify-end gap-2 pt-1">
        {onCancel && <Button type="button" variant="secondary" onClick={onCancel}>{t("common.cancel")}</Button>}
        <Button type="submit" disabled={busy || !v.name.trim() || !v.prompt.trim() || !v.schedule.trim()}>{t("common.save")}</Button>
      </div>
    </form>
  );
}
