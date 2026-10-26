/** Reading the model catalog: names, efforts, prices and what a switch costs. */

import { ApiError } from "@/api/client";
import type { ModelWire } from "@/api/types";
import type { Key } from "@/i18n";

/** The effort levels the catalog knows, named. An id the catalog grows later is
 *  shown as it comes off the wire rather than as a missing key. */
const EFFORT_KEYS: Record<string, Key> = {
  low: "models.effortLow", medium: "models.effortMedium", high: "models.effortHigh",
  xhigh: "models.effortXhigh", max: "models.effortMax",
};
export const effortKey = (effort: string): Key | null => EFFORT_KEYS[effort] ?? null;

/** The label for a model id, falling back to the id itself: a lane may still be
 *  on a model that has since left `models:`, and naming it is more use than
 *  showing a blank. */
export function modelLabel(models: readonly ModelWire[], id: string): string {
  return models.find((m) => m.id === id)?.label ?? id;
}

export const findModel = (models: readonly ModelWire[], id: string | null): ModelWire | undefined =>
  id ? models.find((m) => m.id === id) : undefined;

/** An effort survives a model switch only if the new model takes it; otherwise
 *  the pair goes to "Auto", which is what the server would make of it anyway
 *  (`400 bad_effort` for anything else). */
export function keepEffort(model: ModelWire | undefined, effort: string | null): string | null {
  return effort && model?.efforts.includes(effort) ? effort : null;
}

/** A price as money: whole dollars stay whole, cents keep two digits. */
export const money = (n: number): string => (Number.isInteger(n) ? String(n) : n.toFixed(2));

/**
 * What the next reply costs in a cold cache after a switch.
 *
 * Prompt caches are model-scoped, so the first turn on a new model re-reads the
 * whole conversation at the cache-WRITE price — once. `used` is the last call's
 * prompt as `turn.end` reported it, which is the closest thing the browser has to
 * the size of what will be re-read.
 */
export function coldReread(model: ModelWire, used: number): { tokens: number; cost: number } {
  return { tokens: Math.round(used / 1000), cost: (used * model.price.in * model.cache_write) / 1e6 };
}

/** A cost that never rounds away to "$0.00": small figures keep three digits. */
export const cost = (n: number): string => (n >= 0.01 ? n.toFixed(2) : n.toFixed(3));

/** Every model write is validated server-side; these are the codes it answers
 *  with. Anything else is shown as the server worded it. */
const CODES = ["bad_model", "bad_effort", "model_command"] as const;
const known = (c: string): c is (typeof CODES)[number] => (CODES as readonly string[]).includes(c);

type T = (key: Key, params?: Record<string, string | number>) => string;

export function modelErrorText(t: T, e: unknown): string {
  if (e instanceof ApiError && known(e.code)) return t(`models.errors.${e.code}`);
  return e instanceof Error ? e.message : t("common.error");
}
