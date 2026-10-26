/**
 * The language the model is thinking in, as a hook.
 *
 * Step labels follow the model, not the interface (see `labels.ts`), and until a
 * thought has arrived there is nothing to read it off — so the line that stands
 * in for the step group before the first part used the interface language and
 * flipped, a second later, to the language of the thoughts that landed. The
 * model's language is a property of the conversation, though, not of the moment:
 * when this message has no reasoning of its own yet, the newest reasoning in the
 * thread answers for it.
 */

import { useAuiState } from "@assistant-ui/react";
import { useLocale } from "@/i18n";
import { detectLang, type Lang } from "@/toolkit/labels";

type Textish = { readonly type: string; readonly text?: string };

function reasoning(parts: readonly Textish[] | string | undefined): string {
  if (!parts || typeof parts === "string") return "";
  return parts.filter((p) => p.type === "reasoning").map((p) => p.text ?? "").join(" ");
}

/** `own` is this message's own reasoning, when the caller already has it at hand. */
export function useModelLang(own?: string): Lang {
  const locale = useLocale();
  return useAuiState((s) => {
    if (own) return detectLang(own, locale);
    const here = reasoning(s.message.parts);
    if (here) return detectLang(here, locale);
    const messages = s.thread.messages;
    for (let i = messages.length - 1; i >= 0; i--) {
      const text = reasoning(messages[i]?.content);
      if (text) return detectLang(text, locale);
    }
    return locale;
  });
}
