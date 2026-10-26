import { expect, it } from "vitest";
import { detectLang, imagePrompt, labelFor } from "./labels";

it("names the step in Russian when the model thinks in Russian, with a target where it helps", () => {
  expect(labelFor("Read", { file_path: "/data/agent_home/wiki/index.md" }, true, "ru")).toMatchObject({ text: "Прочитал файл", target: "wiki/index.md" });
  expect(labelFor("Read", { file_path: "a.md" }, false, "ru").text).toBe("Читаю файл");
  expect(labelFor("Bash", { command: "ls -la" }, true, "ru")).toMatchObject({ text: "Выполнил команду", target: "ls -la" });
  expect(labelFor("WebSearch", { query: "x" }, false, "ru")).toMatchObject({ text: "Ищу в интернете", target: "x" });
  expect(labelFor("mcp__hermes__session_search", { query: "y" }, true, "ru").text).toBe("Искал в памяти");
  expect(labelFor("mcp__fatsecret__foods", {}, true, "ru")).toMatchObject({ text: "Вызвал fatsecret · foods" });
  expect(labelFor("Skill", { skill: "deploy" }, false, "ru")).toMatchObject({ text: "Использую навык", target: "deploy" });
  expect(labelFor("Whatever", {}, true, "ru").text).toBe("Вызвал Whatever");
});

it("speaks English when the model thinks in English", () => {
  expect(labelFor("Read", { file_path: "a.md" }, true, "en")).toMatchObject({ text: "Read file", target: "a.md" });
  expect(labelFor("Bash", { command: "ls" }, false, "en").text).toBe("Running command");
  expect(labelFor("WebSearch", {}, true, "en").text).toBe("Searched the web");
  expect(labelFor("mcp__fatsecret__foods", {}, false, "en").text).toBe("Calling fatsecret · foods");
  expect(labelFor("Whatever", {}, true, "en").text).toBe("Called Whatever");
});

it("reads an image generation out of the command that draws it", () => {
  const cmd = 'bash .claude/skills/gpt-image-2/scripts/gen.sh --prompt "a calm lake at dawn" --out /data/agent_home/image.png';
  expect(imagePrompt("Bash", { command: cmd })).toBe("a calm lake at dawn");
  expect(labelFor("Bash", { command: cmd }, false, "en")).toMatchObject({ text: "Generating an image", target: "a calm lake at dawn" });
  expect(labelFor("Bash", { command: cmd }, true, "ru").text).toBe("Нарисовал изображение");
  expect(imagePrompt("Bash", { command: "bash skills/gpt-image-2/scripts/gen.sh --prompt 'a \"quoted\" sign' --out /data/x.png" })).toBe('a "quoted" sign');
  expect(imagePrompt("Bash", { command: 'gpt-image-2/scripts/gen.sh --prompt "say \\"hi\\"" --out /data/x.png' })).toBe('say "hi"');
  expect(imagePrompt("Bash", { command: "bash skills/gpt-image-2/scripts/gen.sh --prompt \"$P\" --out /data/x.png" })).toBe("$P");
  expect(imagePrompt("Bash", { command: "bash skills/gpt-image-2/scripts/gen.sh $ARGS" })).toBe("");   // drawing, prompt out of reach
  expect(imagePrompt("Bash", { command: "ls -la" })).toBeNull();
  expect(imagePrompt("Read", { file_path: "gpt-image-2/scripts/gen.sh" })).toBeNull();
});

it("detects the language of a thought and falls back to the interface language when unsure", () => {
  expect(detectLang("I'll check the repository's design context first.")).toBe("en");
  expect(detectLang("Проверю контекст дизайна в репозитории.")).toBe("ru");
  expect(detectLang("Нужно прочитать файл README.md")).toBe("ru");   // Cyrillic outweighs the Latin file name
  expect(detectLang("", "en")).toBe("en");
  expect(detectLang(undefined, "ru")).toBe("ru");
  expect(detectLang("1234 …", "en")).toBe("en");
  expect(detectLang("")).toBe("en");                                    // the store's default locale
});
