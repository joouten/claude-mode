# claude-mode

A slash command system that bridges Claude Chat and Claude Code, so context never gets lost when you switch between thinking and execution.

## Install

```bash
npx claude-mode init
```

Run this in any project root. It drops two slash commands into `.claude/commands/`, two handoff templates into the project root, and adds the generated handoff files to `.gitignore`.

## How it works

> Launch Claude Code from the project root so the slash commands and `@file` references resolve correctly.

The loop is simple:

1. **Think in Chat.** Work through the architectural or strategic question with Claude Chat. When the decision is locked, Chat writes `CHAT_TO_CODE.md` to the project root.
2. **Execute in Code.** Open Claude Code, run `/project:mode-cc`. Code reads the handoff, confirms the decision, constraints, and next action, then waits for User to approve before working.
3. **Hit a strategic decision point in Code?** Run `/project:mode-c`. Code writes `CODE_TO_CHAT.md` describing the current state, the question, and what's already been ruled out — then pauses.
4. **Back to Chat with that file** and the loop continues.

A strategic decision point is any question where the answer changes *what* is being built, *how* it is structured, or *whether* it meets a required standard — not a coding question.

## The commands

| Command | Direction | What it does |
| --- | --- | --- |
| `/project:mode-cc` | Chat → Code | Reads `CHAT_TO_CODE.md`, confirms understanding, waits for User approval before acting |
| `/project:mode-c` | Code → Chat | Writes `CODE_TO_CHAT.md` at a strategic decision point, then pauses the session |

## Roadmap

- **Phase 1 — Manual routing (current).** User shuttles the two handoff files between Chat and Code by hand. No automation. This phase is the foundation: structured documents that downstream phases can parse.
- **Phase 2 — Cowork-assisted notification.** Cowork surfaces handoff events so User knows when a document is ready, without needing to check.
- **Phase 3 — Full MCP automation.** An MCP server routes documents between Chat and Code automatically. The document formats from Phase 1 are designed to be machine-readable for exactly this purpose.

## Listing

claude-mode will be listed at [claudefa.st/blog/tools/mcp-extensions/best-addons](https://claudefa.st/blog/tools/mcp-extensions/best-addons) once published.

## License

MIT
