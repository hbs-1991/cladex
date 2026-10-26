/** What can be done with a connector in its current state — shared by the card
 * and the connector page so the two never disagree. */

import type { ConnectorWire } from "@/api/types";
import type { Key } from "@/i18n";

export type ConnectorAction = "connect" | "enable" | "disable" | "test" | "disconnect";
type T = (key: Key, params?: Record<string, string | number>) => string;

export const isInstalled = (c: ConnectorWire) => c.state !== "available";

/** A catalog entry that is not installed has exactly one thing to do: connect.
 * Everything else (a custom entry can also be removed) needs a menu. */
export const connectsDirectly = (c: ConnectorWire) => !isInstalled(c) && c.source === "catalog";

export function connectorMenu(c: ConnectorWire, t: T): { action: ConnectorAction; label: string }[] {
  const installed = isInstalled(c);
  return [
    ...(!installed ? [{ action: "connect" as const, label: t("connectors.connect") }] : []),
    ...(c.state === "needs_reauth" || c.state === "error" ? [{ action: "connect" as const, label: t("connectors.reconnect") }] : []),
    ...(c.state === "disabled" ? [{ action: "enable" as const, label: t("connectors.enable") }] : []),
    { action: "test", label: t("connectors.test") },
    ...(c.state === "connected" ? [{ action: "disable" as const, label: t("connectors.disable") }] : []),
    ...(installed || c.source === "custom" ? [{ action: "disconnect" as const, label: t(installed ? "connectors.disconnect" : "connectors.remove") }] : []),
  ];
}

/** The status line under a card, or null when there is nothing to say (not installed). */
export function connectorStatus(c: ConnectorWire, t: T): string | null {
  if (c.state === "connected") return c.tools === null ? t("connectors.connected") : `${t("connectors.connected")} · ${t("connectors.toolsCount", { n: c.tools })}`;
  if (c.state === "needs_reauth") return t("connectors.needsReconnect");
  if (c.state === "disabled") return t("connectors.disabled");
  if (c.state === "error") return t("connectors.error", { message: c.last_error ?? "" });
  return null;
}

/** The first sentence of a manifest description: the card's one-liner. */
export function firstSentence(text: string): string {
  const m = /^(.+?[.!?])(\s|$)/.exec(text.trim());
  return m ? m[1]! : text.trim();
}
