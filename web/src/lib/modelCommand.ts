/**
 * The one guard for `/model` and `/effort`, spelled exactly as the server's
 * `core.model_catalog.is_model_command()`.
 *
 * The CLI runs a prompt that starts with either of them as its own LOCAL command
 * in non-interactive mode — it answers "Set model to x" and the turn is gone — so
 * such a draft never becomes a message. The composer opens the model picker
 * instead; `POST /api/threads/{id}/messages` answers `400 model_command` for
 * anything that gets past the client.
 *
 * `/models` does not match: the word has to end at a space, an `@` (a command
 * addressed to a bot in a group) or the end of the text.
 */
const MODEL_COMMAND = /^\s*\/(model|effort)(\s|@|$)/;

export function isModelCommand(text: string): boolean {
  return MODEL_COMMAND.test(text);
}
