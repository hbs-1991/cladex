/// <reference types="node" />
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";
import { en } from "./en";
import { ru } from "./ru";
import { plural, t } from "./index";
import { useStore } from "@/store";

type Tree = Record<string, unknown>;
const leaves = (o: Tree, p = ""): string[] =>
  Object.entries(o).flatMap(([k, v]) =>
    typeof v === "string" || (v && typeof v === "object" && "one" in (v as Tree)) ? [p + k] : leaves(v as Tree, `${p}${k}.`));

it("has the same keys in both languages", () => {
  expect(leaves(ru as unknown as Tree).sort()).toEqual(leaves(en as unknown as Tree).sort());
});

it("interpolates, pluralises and leaves an unknown placeholder visible", () => {
  expect(t("sidebar.user", { id: 7 }, "en")).toBe("User 7");
  expect(t("steps.workedSteps", { n: 1 }, "en")).toBe("Worked 1 step");
  expect(t("steps.workedSteps", { n: 3 }, "en")).toBe("Worked 3 steps");
  expect(t("steps.workedSteps", { n: 1 }, "ru")).toBe("Работал 1 шаг");
  expect(t("steps.workedSteps", { n: 3 }, "ru")).toBe("Работал 3 шага");
  expect(t("steps.workedSteps", { n: 11 }, "ru")).toBe("Работал 11 шагов");
  expect(t("chat.files", {}, "en")).toBe("Files: {n}");
});

it("counts the Russian way", () => {
  expect([1, 2, 5, 11, 21, 22, 25].map((n) => plural(n, "ru"))).toEqual(["one", "few", "many", "many", "one", "few", "many"]);
  expect([1, 2].map((n) => plural(n, "en"))).toEqual(["one", "many"]);
});

it("defaults to English and remembers a switch", () => {
  useStore.setState(useStore.getInitialState());
  expect(useStore.getState().locale).toBe("en");
  expect(t("sidebar.newChat")).toBe("New chat");
  useStore.getState().setLocale("ru");
  expect(t("sidebar.newChat")).toBe("Новый чат");
  expect(localStorage.getItem("cladex.locale")).toBe("ru");
  expect(document.documentElement.lang).toBe("ru");
  useStore.getState().setLocale("en");
});

const SRC = join(__dirname, "..");
const ALLOWED = [join("i18n", "ru.ts"), join("toolkit", "labels.ts"), join("components", "cladex", "UserMenu.tsx")];
function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => { const p = join(dir, f); return statSync(p).isDirectory() ? walk(p) : [p]; });
}

it("keeps every Russian string in the dictionary", () => {
  const offenders = walk(SRC)
    .filter((p) => /\.(ts|tsx)$/.test(p) && !/\.test\.tsx?$/.test(p) && !ALLOWED.some((a) => p.endsWith(a)))
    .filter((p) => /[А-Яа-яЁё]/.test(readFileSync(p, "utf8").replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, "")));   // comments may explain in any language
  expect(offenders.map((p) => p.slice(SRC.length + 1))).toEqual([]);
});
