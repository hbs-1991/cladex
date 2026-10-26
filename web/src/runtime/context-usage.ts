/** The thread's context-window figure, handed from the fold to the composer. */

import { createContext, useContext } from "react";
import type { ContextUsage } from "./fold";

/**
 * Provided by `CladeXRuntime` from `folded.context`. `null` means unknown — no
 * turn on this SDK session has reported usage yet — and the meter that reads it
 * renders nothing rather than a zero.
 */
export const ContextUsageContext = createContext<ContextUsage | null>(null);

export function useContextUsage(): ContextUsage | null {
  return useContext(ContextUsageContext);
}
