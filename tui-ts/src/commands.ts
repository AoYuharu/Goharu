export interface CommandDef {
  name: string;
  description: string;
  usage: string;
}

/** Mirrors TUI/widgets/command_suggestions.py COMMANDS. */
export const COMMANDS: CommandDef[] = [
  { name: "/clear", description: "Clear chat history and context", usage: "/clear" },
  { name: "/compact", description: "Summarize conversation via LLM", usage: "/compact" },
  { name: "/upgrade", description: "Check GitHub for updates and upgrade Goharu (audited, confirm-gated)", usage: "/upgrade" },
  { name: "/config", description: "View/set configuration parameters", usage: "/config list" },
  { name: "/copy", description: "Copy last assistant message to clipboard", usage: "/copy" },
  { name: "/export", description: "Export chat history to file", usage: "/export" },
  { name: "/help", description: "Show help information", usage: "/help" },
  { name: "/prompt", description: "Browse and edit prompt files in-app", usage: "/prompt" },
  { name: "/prompt-cache", description: "Browse & reuse recent long prompts", usage: "/prompt-cache" },
  { name: "/tools", description: "List all tools (builtin + MCP) with descriptions", usage: "/tools" },
  { name: "/exit", description: "Exit application", usage: "/exit" },
  { name: "/quit", description: "Exit application", usage: "/quit" },
];

export function filterCommands(input: string): CommandDef[] {
  if (!input.startsWith("/")) return [];
  const lower = input.toLowerCase();
  return COMMANDS.filter((c) => c.name.startsWith(lower));
}

export const HELP_LINES = [
  "Available commands:",
  "  /clear - Clear chat history",
  "  /compact - Summarize conversation via LLM",
  "  /upgrade - Check GitHub and upgrade Goharu (audited)",
  "  /config - Open interactive configuration editor",
  "  /copy - Copy last assistant message",
  "  /export - Export chat history to file",
  "  /help - Show this help",
  "  /prompt - Browse & edit prompt files",
  "  /prompt-cache - Browse & reuse recent long prompts",
  "  /tools - List all tools (builtin + MCP)",
  "  /exit or /quit - Exit application",
  "",
  "Keyboard shortcuts:",
  "  Ctrl+C - Exit",
  "  Ctrl+L - Clear chat",
  "  F2 - Open selectable text viewer",
  "  Tab - Auto-complete command",
];
