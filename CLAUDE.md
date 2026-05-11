# claude-mode — repo context

## What this project is

claude-mode is a slash command system that bridges Claude Chat and Claude Code. Chat is where strategic and architectural thinking happens; Code is where execution happens. Without a bridge, every switch between the two loses context — User ends up re-explaining decisions, constraints, and reasoning.

claude-mode solves this with two structured handoff documents and two slash commands:

- `CHAT_TO_CODE.md` — Chat's decision, reasoning, next action, constraints, and watch-for items, written when a conversation concludes
- `CODE_TO_CHAT.md` — Code's current state, completed steps, decision point, and ruled-out options, written when Code hits a strategic decision point
- `/project:mode-cc` — Code reads `CHAT_TO_CODE.md` and waits for User approval before acting
- `/project:mode-c` — Code writes `CODE_TO_CHAT.md` and pauses the session

Install method is `npx claude-mode init`, which drops the commands and templates into a target project and adds the generated handoff files to `.gitignore`.

## Three-phase roadmap

1. **Phase 1 — Manual routing (this repo).** User shuttles handoff files between Chat and Code by hand. The document formats are the deliverable — everything downstream depends on them being structured and machine-readable.
2. **Phase 2 — Cowork-assisted notification.** Cowork surfaces handoff events so User doesn't have to poll for them.
3. **Phase 3 — Full MCP automation.** An MCP server routes handoff documents between Chat and Code without User involvement.

Phase 1 is the foundation. Every choice in this repo is made with Phase 3 in mind: documents are structured, sections are predictable, formatting is consistent.

## Strategic decision point — definition

A strategic decision point is any question where the answer changes:

- *what* is being built,
- *how* it is structured, or
- *whether* it meets a required standard.

Coding questions ("how do I implement this loop?") are not strategic decision points. Architectural, directional, and standards questions are.

## Working style

- Use **"User"** in all generated files, command prompts, templates, and documentation. Never use a personal name.
- One question at a time. If multiple decisions are open, surface them sequentially.
- Generated handoff files (`CHAT_TO_CODE.md`, `CODE_TO_CHAT.md`) are never committed. Templates under `templates/` are committed.
- All handoff documents must be machine-readable. Phase 3 will parse them — keep section headings, ordering, and structure consistent with the templates.
- Do not publish to npm until the install flow has been verified locally end-to-end.
