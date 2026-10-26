/**
 * Interface strings in two languages. `en` is the dictionary of record: `Key`
 * is typed from it and `ru` must have the same shape, so a language can never
 * fall behind silently. Components use `useT()`; plain functions take the
 * locale as an argument and default to the store's value.
 */

import { useStore } from "@/store";
import { en } from "./en";
import { ru } from "./ru";

export type Locale = "en" | "ru";
export const LOCALES: readonly Locale[] = ["en", "ru"];
export const DEFAULT_LOCALE: Locale = "en";

type PluralLeaf = { one: string; few: string; many: string };
type Leaf = string | PluralLeaf;
/** The same tree with every leaf widened to a plain string / plural object. */
export type Shape<T> = { [K in keyof T]: T[K] extends string ? string : T[K] extends PluralLeaf ? PluralLeaf : Shape<T[K]> };
type Paths<T, P extends string = ""> = {
  [K in keyof T & string]: T[K] extends Leaf ? `${P}${K}` : Paths<T[K], `${P}${K}.`>;
}[keyof T & string];
export type Key = Paths<typeof en>;
type Params = Record<string, string | number>;

const DICTS: Record<Locale, Shape<typeof en>> = { en, ru };

export function plural(n: number, locale: Locale): keyof PluralLeaf {
  const a = Math.abs(Math.trunc(n));
  if (locale === "en") return a === 1 ? "one" : "many";
  const m10 = a % 10, m100 = a % 100;
  if (m10 === 1 && m100 !== 11) return "one";
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "few";
  return "many";
}

function lookup(locale: Locale, key: string): Leaf | undefined {
  let node: unknown = DICTS[locale];
  for (const part of key.split(".")) {
    if (!node || typeof node !== "object") return undefined;
    node = (node as Record<string, unknown>)[part];
  }
  return node as Leaf | undefined;
}

function fill(text: string, params?: Params): string {
  if (!params) return text;
  return text.replace(/\{(\w+)\}/g, (m, name: string) => (name in params ? String(params[name]) : m));
}

export function currentLocale(): Locale { return useStore.getState().locale; }

export function t(key: Key, params?: Params, locale: Locale = currentLocale()): string {
  const leaf = lookup(locale, key) ?? lookup(DEFAULT_LOCALE, key);
  if (leaf === undefined) return key;
  if (typeof leaf === "string") return fill(leaf, params);
  const n = typeof params?.n === "number" ? params.n : Number(params?.n ?? 0);
  return fill(leaf[plural(n, locale)], params);
}

/** Re-renders with the locale: components call this, not `t` directly. */
export function useT() {
  const locale = useStore((s) => s.locale);
  return (key: Key, params?: Params) => t(key, params, locale);
}

export function useLocale(): Locale { return useStore((s) => s.locale); }

export { applyLocale } from "@/store/ui";
