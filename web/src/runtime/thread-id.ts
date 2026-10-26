/** The open thread's id, handed from the runtime to what sits inside it. */

import { createContext, useContext } from "react";

/**
 * Provided by `CladeXRuntime`. assistant-ui's own state knows the thread it is
 * rendering, but not the lane key our API is addressed by, and the model pill
 * lives deep inside the composer — so the id travels the same way the context
 * figure does rather than through half a dozen props.
 */
export const ThreadIdContext = createContext<string | null>(null);

export function useThreadId(): string | null {
  return useContext(ThreadIdContext);
}
