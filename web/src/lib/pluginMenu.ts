/** What can be done with a plugin in its current state — shared by the card and
 * the plugin page so the two never disagree — plus the two error-code readers
 * for the plugins tab and the drafts queue that sits on it. The connectors idiom
 * (`lib/connectorMenu.ts`) with two differences, both from the wire: a plugin
 * that is not installed has nothing to remove, and `present` can contradict
 * `state` when the clone vanished behind the app's back. */

import { ApiError } from "@/api/client";
import type { PluginWire } from "@/api/types";
import type { Key } from "@/i18n";

export type PluginAction = "install" | "enable" | "disable" | "remove";
type T = (key: Key, params?: Record<string, string | number>) => string;

export const isInstalled = (p: PluginWire) => p.state !== "available";

/** Not installed means exactly one thing to do. Unlike a connector, even a custom
 * (approved-draft) entry has no Remove here: `DELETE /api/plugins/{name}` drops a
 * registry row and the clone, and an uninstalled plugin has neither. */
export const installsDirectly = (p: PluginWire) => !isInstalled(p);

/** An installed row whose clone is gone: the only cure is fetching it again.
 * A row the server already explains (`error` carries `last_error`, e.g. the
 * manifest left the catalog) keeps the server's reason instead — re-cloning is
 * not the cure there, and "reinstall it" would be advice that cannot work. */
export const needsReinstall = (p: PluginWire) => isInstalled(p) && !p.present && p.state !== "error";

export function pluginMenu(p: PluginWire, t: T): { action: PluginAction; label: string }[] {
  const installed = isInstalled(p);
  // An orphan row (`error`: the manifest left the catalog) has no manifest for
  // `manifest_or_404` to find, so install/reinstall answers 404 there. Offering it
  // would be a dead end that also contradicts `needsReinstall` — re-cloning is not
  // the cure. Remove is the one action that works, so it is the only one listed.
  const canInstall = p.state !== "error";
  return [
    ...(!installed && canInstall ? [{ action: "install" as const, label: t("plugins.install") }] : []),
    // Install is idempotent — `clone_plugin` removes and re-fetches at the
    // manifest's ref — so the same call repairs a lost clone and follows a pin
    // that moved. One action, two names, because they read as different jobs.
    ...(installed && canInstall ? [{ action: "install" as const, label: t("plugins.reinstall") }] : []),
    ...(p.state === "disabled" ? [{ action: "enable" as const, label: t("plugins.enable") }] : []),
    ...(p.state === "installed" ? [{ action: "disable" as const, label: t("plugins.disable") }] : []),
    ...(installed ? [{ action: "remove" as const, label: t("plugins.remove") }] : []),
  ];
}

/** The status line under a card, or null when there is nothing to say (not installed). */
export function pluginStatus(p: PluginWire, t: T): string | null {
  if (needsReinstall(p)) return t("plugins.missing");        // the row says installed, the disk disagrees — say the disk
  if (p.state === "installed") return t("plugins.installed");
  if (p.state === "disabled") return t("plugins.disabled");
  if (p.state === "error") return t("plugins.error", { message: p.last_error ?? "" });
  return null;
}

/** `<repo>/tree/<ref>/<subdir>` — the exact code the manifest pins, not the branch tip. */
export function treeUrl(p: PluginWire): string {
  const repo = p.repo.replace(/\.git$/, "").replace(/\/+$/, "");
  const sub = p.subdir.replace(/^\/+|\/+$/g, "");
  return `${repo}/tree/${p.ref}${sub ? `/${sub}` : ""}`;
}

const CODES = ["not_found", "forbidden", "install_failed", "local_server", "stale_install", "not_fetched",
  "no_draft", "stale_draft", "shadowed", "unverified", "bad_manifest", "bad_kind",
  "name_taken", "bad_name", "bad_frontmatter", "too_large", "stale_edit", "pinned", "restore_failed"] as const;
type Code = (typeof CODES)[number];
const known = (c: string): c is Code => (CODES as readonly string[]).includes(c);

/** A server error as the person should read it: a code the UI knows is translated,
 * anything else is the server's own message (git's stderr, usually). */
export function pluginError(t: T, e: unknown): string {
  if (e instanceof ApiError && known(e.code)) return t(`plugins.errors.${e.code}`, { message: e.message });
  return e instanceof Error ? e.message : t("plugins.unknownError");
}

/** The same for the drafts surface, over `drafts.errors.*`. It lives beside its
 * twin rather than in the dialog because a component file may only export
 * components (fast refresh), and the two share the code list either way. */
export function draftError(t: T, e: unknown): string {
  if (e instanceof ApiError && known(e.code)) return t(`drafts.errors.${e.code}`, { message: e.message });
  return e instanceof Error ? e.message : t("common.error");
}
