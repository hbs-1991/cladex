import { describe, expect, it } from "vitest";
import { nameFrom, parseSource } from "./pluginSource";

describe("parseSource", () => {
  it("reads repo, pin and subdirectory out of a tree URL", () => {
    expect(parseSource("https://github.com/anthropics/knowledge-work-plugins/tree/11ac5eeaca27c764913a06f1e61a7cb712790b1a/marketing")).toEqual({
      repo: "https://github.com/anthropics/knowledge-work-plugins",
      ref: "11ac5eeaca27c764913a06f1e61a7cb712790b1a",
      subdir: "marketing", name: "marketing",
    });
  });

  it("keeps a nested subdirectory and names the plugin after its last segment", () => {
    expect(parseSource("https://github.com/o/r/tree/v1.2.3/plugins/plugin-dev")).toEqual({
      repo: "https://github.com/o/r", ref: "v1.2.3",
      subdir: "plugins/plugin-dev", name: "plugin-dev",
    });
  });

  it("takes a bare repository, with or without .git and a trailing slash", () => {
    for (const raw of ["https://github.com/o/radar", "https://github.com/o/radar.git", "https://github.com/o/radar/"]) {
      expect(parseSource(raw)).toEqual({ repo: "https://github.com/o/radar", ref: "", subdir: "", name: "radar" });
    }
  });

  it("yields no ref for a branch, because a branch moves after the review", () => {
    expect(parseSource("https://github.com/o/r/tree/main/marketing")?.ref).toBe("");
    expect(parseSource("https://github.com/o/r/tree/feature/x")?.ref).toBe("");
  });

  it("refuses what cannot become a manifest", () => {
    for (const raw of ["", "not a url", "http://github.com/o/r", "git@github.com:o/r.git", "https://github.com/o"]) {
      expect(parseSource(raw)).toBeNull();
    }
  });

  it("ignores a page that says nothing about which code to pin", () => {
    expect(parseSource("https://github.com/o/r/issues/12")).toEqual({
      repo: "https://github.com/o/r", ref: "", subdir: "", name: "r",
    });
  });
});

describe("nameFrom", () => {
  it("makes a catalog name, or nothing at all", () => {
    expect(nameFrom("Content Radar")).toBe("content-radar");
    expect(nameFrom("claude-plugin.git")).toBe("claude-plugin");
    expect(nameFrom("___")).toBe("");
    expect(nameFrom("")).toBe("");
  });
});
