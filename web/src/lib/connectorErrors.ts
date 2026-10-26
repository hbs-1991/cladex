/** Turning a connector error into words. It lives here rather than in the tab so
 * the add dialog can use it without importing its own parent. */

import { ApiError } from "@/api/client";
import type { Key } from "@/i18n";

const KNOWN = ["bad_manifest", "name_taken", "secrets_missing", "oauth_required", "discovery_failed", "registration_unsupported",
  "state_mismatch", "exchange_failed", "probe_failed", "needs_reauth", "forbidden", "access_denied"] as const;
type Known = (typeof KNOWN)[number];

export const isKnownError = (c: string): c is Known => (KNOWN as readonly string[]).includes(c);

/** A server error as the person should read it: a code the UI knows is translated,
 * anything else is the server's own message. */
export function errorText(t: (k: Key, p?: Record<string, string | number>) => string, e: unknown): string {
  if (e instanceof ApiError && isKnownError(e.code)) return t(`connectors.errors.${e.code}`, { message: e.message });
  return e instanceof Error ? e.message : t("common.error");
}
