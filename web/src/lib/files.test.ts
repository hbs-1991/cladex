import { expect, it } from "vitest";
import { extLabel, fileKindLabel, isMarkdown, kindOf, uniqueFiles } from "./files";

const f = (name: string, media_type: string, file_id = name) => ({ file_id, name, media_type, size: 1, url: `/api/files/${file_id}` });

it("names a file's kind from its type and extension", () => {
  expect(kindOf(f("report.md", "text/markdown"))).toBe("document");
  expect(kindOf(f("a.pdf", "application/pdf"))).toBe("document");
  expect(kindOf(f("calc.py", "text/x-python"))).toBe("code");
  expect(kindOf(f("prices.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))).toBe("spreadsheet");
  expect(kindOf(f("cat.png", "image/png"))).toBe("image");
  expect(kindOf(f("bundle.zip", "application/zip"))).toBe("archive");
  expect(kindOf(f("blob", "application/octet-stream"))).toBe("file");
});

it("labels a file as its kind and extension", () => {
  expect(fileKindLabel(f("report.md", "text/markdown"))).toBe("Document · MD");
  expect(fileKindLabel(f("Dockerfile", "text/plain"))).toBe("Code");
  expect(extLabel(f("archive.tar.gz", "application/gzip"))).toBe("GZ");
});

it("treats a .md sent as plain text as markdown", () => {
  expect(isMarkdown(f("notes.md", "text/plain"))).toBe(true);
  expect(isMarkdown(f("notes.txt", "text/plain"))).toBe(false);
});

it("keeps one entry per file id, in delivery order", () => {
  const list = uniqueFiles([f("a.md", "text/markdown", "1"), f("b.md", "text/markdown", "2"), f("a.md", "text/markdown", "1")]);
  expect(list.map((x) => x.file_id)).toEqual(["1", "2"]);
});
