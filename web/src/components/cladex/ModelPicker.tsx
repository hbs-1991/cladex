/**
 * The model pill on the composer rail: which model this chat runs on, and the
 * menu that changes it.
 *
 * The pair is LANE state — it lives on the thread row, survives `/new` and is the
 * same row Telegram's `/model` edits — so the menu re-reads `GET /api/threads/{id}`
 * every time it opens rather than trusting what this tab last saw. What it shows
 * is the EFFECTIVE pair: the lane's explicit choice when it has one, otherwise
 * what the server resolved from the live session, the personal default and the
 * deploy default.
 *
 * Prices are on every row because a switch is a bill: prompt caches are
 * model-scoped, so the first reply on a new model re-reads the whole conversation
 * cold. That is the one-line note under the lists — the figure the reader is
 * actually agreeing to, shown before they close the menu, and kept until the next
 * turn reports a new context size.
 *
 * On a phone the pill truncates rather than wraps: the composer rail is the one
 * place in the app that must not grow a second line.
 */

import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { useT } from "@/i18n";
import { coldReread, cost, effortKey, findModel, keepEffort, modelErrorText, modelLabel, money } from "@/lib/models";
import { useContextUsage } from "@/runtime/context-usage";
import { useThreadId } from "@/runtime/thread-id";
import { useStore } from "@/store";
import { effectivePair } from "@/store/models";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

/** "no effort flag", which the server stores as null and the UI calls Auto. */
const AUTO = "auto";

export function ModelPicker() {
  const t = useT();
  const threadId = useThreadId();
  const usage = useContextUsage();
  const models = useStore((s) => s.models);
  const deployDefault = useStore((s) => s.deployDefault);
  const loadModels = useStore((s) => s.loadModels);
  const loadThreadModel = useStore((s) => s.loadThreadModel);
  const setThreadModel = useStore((s) => s.setThreadModel);
  const tm = useStore((s) => (threadId ? s.threadModel[threadId] : undefined));
  const open = useStore((s) => s.modelPickerOpen);
  const setOpen = useStore((s) => s.setModelPickerOpen);
  const [error, setError] = useState<string | null>(null);
  // The switch this note describes, together with the context size it was
  // estimated from: the next `turn.end` moves that size, and the estimate is
  // then about a re-read that has already happened.
  const [note, setNote] = useState<{ model: string; used: number } | null>(null);

  useEffect(() => { void loadModels(); }, [loadModels]);
  useEffect(() => { if (threadId) void loadThreadModel(threadId); }, [threadId, loadThreadModel]);
  // Opened from the pill or from a `/model` draft in the composer — either way
  // the lane may have moved in Telegram or another tab since this tab looked.
  useEffect(() => { if (open && threadId) void loadThreadModel(threadId); }, [open, threadId, loadThreadModel]);

  const effortName = (effort: string) => { const k = effortKey(effort); return k ? t(k) : effort; };
  const pairName = (effort: string | null) => (effort ? effortName(effort) : t("models.auto"));

  // Before the thread's own pair is known — a fresh tab, a lane with no row yet —
  // the deploy default is what a turn would run on, so that is what the pill says.
  const pair = tm ? effectivePair(tm)
    : deployDefault ? { model: deployDefault.model, effort: deployDefault.effort } : null;
  if (!threadId || !pair) return null;

  const current = findModel(models, pair.model);
  const used = usage?.used ?? 0;
  const anyTooLarge = models.some((m) => m.window > 0 && used > m.window);
  const noted = note && used === note.used ? findModel(models, note.model) : undefined;
  const reread = noted ? coldReread(noted, used) : null;

  const apply = async (model: string | null, effort: string | null) => {
    setError(null);
    try {
      await setThreadModel(threadId, { model, effort });
      // Clearing the pair costs nothing to explain: what the lane falls back to
      // is only known after the server answers, and it may well be what it was.
      setNote(model && usage ? { model, used: usage.used } : null);
    } catch (e) { setError(modelErrorText(t, e)); }
  };

  return (
    <DropdownMenu open={open} onOpenChange={(v) => { setOpen(v); if (v) setError(null); }}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          data-slot="cladex_model-pill"
          aria-label={t("models.pill")}
          className="flex h-7 max-w-[7.5rem] items-center gap-1 rounded-full px-2 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground data-[state=open]:bg-accent data-[state=open]:text-foreground sm:max-w-[13rem]"
        >
          <span className="truncate">{modelLabel(models, pair.model)} · {pairName(pair.effort)}</span>
          <ChevronDown aria-hidden className="size-3.5 shrink-0" strokeWidth={1.75} />
        </button>
      </DropdownMenuTrigger>
      {/* Anchored to the rail and narrower than the narrowest phone, so the menu
          never pushes the page sideways at 390px or inside the Mini App. */}
      <DropdownMenuContent align="start" side="top" className="w-[min(20rem,calc(100vw-2rem))] shadow-floating">
        <DropdownMenuLabel className="flex items-baseline justify-between gap-2 font-normal text-muted-foreground">
          {t("models.model")}
          <span className="text-[11px]">{t("models.priceHint")}</span>
        </DropdownMenuLabel>
        <DropdownMenuRadioGroup value={pair.model} onValueChange={(id) => void apply(id, keepEffort(findModel(models, id), pair.effort))}>
          {models.map((m) => {
            // A window of 0 is "the pinned CLI has no entry for this model", not
            // "it holds nothing" — an unknown window never disables a row.
            const tooLarge = m.window > 0 && used > m.window;
            return (
              <DropdownMenuRadioItem
                key={m.id} value={m.id} disabled={tooLarge}
                title={tooLarge ? t("models.tooLarge", { label: m.label }) : undefined}
                onSelect={(e) => e.preventDefault()}
              >
                <span className="truncate">{m.label}</span>
                <span className="ml-auto pl-3 text-xs text-muted-foreground tabular-nums">
                  {t("models.price", { in: money(m.price.in), out: money(m.price.out) })}
                </span>
              </DropdownMenuRadioItem>
            );
          })}
        </DropdownMenuRadioGroup>
        {anyTooLarge && <p className="px-2 py-1 text-[11px] text-muted-foreground">{t("models.tooLargeHint")}</p>}

        <DropdownMenuSeparator />
        <DropdownMenuLabel className="font-normal text-muted-foreground">{t("models.effort")}</DropdownMenuLabel>
        {/* An effort-only change is sent as the effective model plus the new
            effort, so choosing one also pins the model — which is what the
            server would record anyway. */}
        <DropdownMenuRadioGroup value={pair.effort ?? AUTO} onValueChange={(v) => void apply(pair.model, v === AUTO ? null : v)}>
          <DropdownMenuRadioItem value={AUTO} onSelect={(e) => e.preventDefault()}>
            {current?.default_effort ? t("models.autoWith", { effort: effortName(current.default_effort) }) : t("models.auto")}
          </DropdownMenuRadioItem>
          {(current?.efforts ?? []).map((e) => (
            <DropdownMenuRadioItem key={e} value={e} onSelect={(ev) => ev.preventDefault()}>{effortName(e)}</DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>

        <DropdownMenuSeparator />
        <DropdownMenuItem disabled={!tm?.model} onSelect={(e) => { e.preventDefault(); void apply(null, null); }}>
          {t("models.useDefault")}
        </DropdownMenuItem>

        {reread && noted && (
          <p className="px-2 pt-1.5 text-xs text-muted-foreground">
            {t("models.switchNote", { tokens: reread.tokens, label: noted.label, cost: cost(reread.cost) })}
          </p>
        )}
        {error && <p role="alert" className="px-2 pt-1.5 text-xs text-error-foreground">{error}</p>}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
