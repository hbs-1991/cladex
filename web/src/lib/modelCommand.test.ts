import { expect, it } from "vitest";
import { isModelCommand } from "./modelCommand";

it("matches exactly what the server's guard matches", () => {
  for (const text of ["/model", "/effort", "  /model", "/model opus", "/model@hermes_bot", "/effort high", "/model\n"]) {
    expect(isModelCommand(text), text).toBe(true);
  }
  // `/models` is an ordinary command, and a mention of one mid-sentence is prose.
  for (const text of ["/models", "/modelling", "/efforts", "tell me about /model", "", "model"]) {
    expect(isModelCommand(text), text).toBe(false);
  }
});
