import { defineToolkit } from "@assistant-ui/react";
import { AskCard } from "@/components/cladex/AskCard";
import { FileCard } from "@/components/cladex/FileCard";
import { ToolRow } from "@/components/cladex/ToolRow";
import { ASK_TOOL, FILE_TOOL } from "@/runtime/fold";

const BUILTIN = ["Read", "Glob", "Grep", "Write", "Edit", "Bash", "WebSearch", "WebFetch", "Agent", "Task",
  "TodoWrite", "Skill", "mcp__hermes__session_search", "mcp__hermes__cron_manage", "mcp__hermes__cost_report",
  "mcp__hermes__skill_admin", "mcp__hermes__ext_manage", "mcp__hermes__send_document", "mcp__hermes__ask_user"] as const;

/** The backend owns every schema; a human tool still has to declare one. */
const ASK_ARGS = {
  type: "object" as const,
  properties: {
    question: { type: "string" as const },
    options: { type: "array" as const, items: { type: "string" as const } },
  },
  required: ["question", "options"],
};

export const toolkit = defineToolkit({
  ...Object.fromEntries(BUILTIN.map((name) => [name, { type: "backend" as const, render: ToolRow }])),
  [ASK_TOOL]: { type: "human" as const, parameters: ASK_ARGS, render: AskCard },  // standalone by rule
  [FILE_TOOL]: { type: "backend" as const, display: "standalone" as const, render: FileCard },
});
