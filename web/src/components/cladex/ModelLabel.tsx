/**
 * Which model wrote this answer, under the message.
 *
 * It names the model that ACTUALLY ran — `turn.end`'s `model`, not the one the
 * turn asked for — so a fallback or a silent substitution is visible where it
 * happened rather than only in a log. A turn recorded before this feature carries
 * no such key and gets no label at all, which is the honest thing to show.
 */

import { useAuiState } from "@assistant-ui/react";
import { useT } from "@/i18n";
import { effortKey, modelLabel } from "@/lib/models";
import { useStore } from "@/store";

export function ModelLabel() {
  const t = useT();
  const models = useStore((s) => s.models);
  const model = useAuiState((s) => s.message.metadata.custom?.["model"]);
  const effort = useAuiState((s) => s.message.metadata.custom?.["effort"]);
  if (typeof model !== "string" || !model) return null;
  const key = typeof effort === "string" ? effortKey(effort) : null;
  const level = key ? t(key) : typeof effort === "string" && effort ? effort : t("models.auto");
  return (
    <span data-slot="cladex_answer-model" className="ms-1 truncate text-xs text-muted-foreground">
      {modelLabel(models, model)} · {level}
    </span>
  );
}
