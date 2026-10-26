import { ComposerContext } from "@/components/assistant-ui/elements/composer-context";
import { useT } from "@/i18n";
import { useContextUsage } from "@/runtime/context-usage";

/**
 * How full the model's context window is, on the composer rail beside the send
 * button: the ring and the share, the tokens behind them on hover or focus.
 * Nothing until a turn on this SDK session has reported usage — a fresh thread
 * shows no meter, never a zero.
 */
export function ContextMeter() {
  const t = useT();
  const usage = useContextUsage();
  if (!usage) return null;
  return (
    <ComposerContext
      className="[&_button]:h-7"
      usage={{ system: usage.cacheRead, tools: usage.cacheCreation, messages: usage.input, total: usage.window }}
      labels={{
        title: t("context.title"), usage: t("context.usage"), system: t("context.cached"),
        tools: t("context.cacheWrite"), messages: t("context.fresh"), total: t("context.total"),
      }}
    />
  );
}
