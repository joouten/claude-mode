# claude-mode middleware (Phase 3)

Automation bridge that runs the full Claude Chat ↔ Claude Code loop without User intervention except at the approval step.

## What it does

```
User starts middleware.py against a project
   ↓
middleware launches AgentAPI (persistent Claude Code, port 3284)
middleware starts Flask web UI (port 5000)
middleware starts watchdog file watcher on the project directory
   ↓
Code (running inside AgentAPI) hits a strategic decision point
   → writes CODE_TO_CHAT.md to project root
   ↓
watchdog fires
   → middleware reads CODE_TO_CHAT.md + project CLAUDE.md
   → calls Anthropic Messages API
   → writes CHAT_TO_CODE.md to project root
   → opens http://localhost:5000/decision in User's browser
   ↓
User reviews in browser
   → APPROVE  → middleware POSTs the brief to AgentAPI /message → Code resumes
   → REJECT   → middleware sends feedback to Chat → loop revises until approved
```

## Prerequisites

- **Python 3.11+** (tested on 3.14)
- **AgentAPI binary** on PATH — Go binary from <https://github.com/coder/agentapi/releases>, or `npm i -g agentapi`
- **Claude Code CLI** on PATH — AgentAPI launches `claude` as a subprocess
- **Anthropic API key** in `middleware/.env`:

  ```
  ANTHROPIC_API_KEY=sk-ant-...
  ```

  `.env` is `.gitignore`d at the claude-mode repo root — never committed.

## Install

```bash
cd middleware
pip install -r requirements.txt
```

## Run

```bash
python middleware.py --project C:\path\to\your\project
```

Optional flags:

| Flag | Default | Notes |
| --- | --- | --- |
| `--project` | (required) | Project directory the watchdog monitors |
| `--port` | `5000` | Flask web UI port; falls back to 5001 / 5002 if busy |
| `--agentapi-port` | `3284` | Port AgentAPI is expected to listen on; if busy, middleware exits |

On startup you should see:

```
claude-mode middleware running
Watching:   C:\path\to\your\project
Web UI:     http://127.0.0.1:5000
AgentAPI:   http://127.0.0.1:3284

Press Ctrl+C to stop.
```

Ctrl+C performs a clean shutdown: stops the watchdog, terminates the AgentAPI subprocess, exits.

## Web UI

| Route | Purpose |
| --- | --- |
| `GET /` | Status page. Auto-refreshes every 3 seconds. Shows current state, project path, AgentAPI health, last event, and the 10 most recent activity log entries. |
| `GET /decision` | Approval page. Renders the pending `CHAT_TO_CODE.md` in full with **APPROVE** and **REJECT** buttons. Rejection reveals a feedback textarea before submitting. |
| `POST /approve` | Delivers the approved brief to Claude Code via AgentAPI `POST /message`, then returns to the status page. |
| `POST /reject` | Sends User's feedback back to the Messages API, gets a revised brief, returns to the decision page. |

The decision page opens automatically in the default browser when a new `CODE_TO_CHAT.md` is detected. If the browser fails to open (headless environments, etc.) the URL is logged so it can be opened manually.

## Architecture notes

- **Persistent Code session.** AgentAPI runs `claude` as a long-lived HTTP server so a single Code session can absorb multiple Chat-driven decisions without losing context. The Agent SDK's `query()` was considered and rejected — each call creates a fresh session, breaking continuity across iterations.
- **Event-driven, not polled.** The watchdog `Observer` fires immediately on `CODE_TO_CHAT.md` create/modify events. A `threading.Timer`-based debounce (2-second quiet window — every event cancels the pending timer and arms a fresh one) coalesces the multiple events that File Explorer copy/paste, IDE saves, and editor atomic-replace operations commonly emit. Net effect: exactly one Messages API call per save, regardless of how many filesystem events fire.
- **Resilient to API overload.** Calls to `client.messages.create` are wrapped in `_create_with_retry`, which retries on `APIStatusError(status_code=529)` — Anthropic's `overloaded_error` — with exponential backoff (2s, 4s, 8s, 16s, 32s; 5 retries max). Other errors (auth 4xx, non-529 5xx) raise immediately. After exhausting retries, the watchdog handler logs `Error during handoff: API overloaded after 5 retries — drop CODE_TO_CHAT.md again to retry the loop`, putting User in control of when to re-trigger.
- **State is in-process.** A single `State` dataclass guarded by a `threading.Lock` holds everything: status, ports, activity log, pending decision. No database, no persistence — restart resets everything.
- **`.env` is hand-rolled.** No `python-dotenv` dependency. The parser is ~10 lines and handles the standard `KEY=VALUE`, comments, and quoted values.
- **Shutdown is coordinated.** `signal.SIGINT` / `SIGTERM` set a `threading.Event` that the main loop blocks on. `atexit.register` also wires AgentAPI subprocess termination as a belt-and-braces fallback.

## Limitations (Phase 3 v1)

- **Rejection flow context.** The reject path replays only the prior assistant message — it does not include the original `CODE_TO_CHAT.md` content in the revise turn. Good enough for "tighten this paragraph"; weak for "rethink the whole decision." Conversation history persistence is a future improvement.
- **No template-format validation.** The Messages API response is written to `CHAT_TO_CODE.md` as-is. If Chat returns malformed Markdown, User catches it visually in the decision page.
- **AgentAPI port not auto-fallback.** If 3284 is busy, middleware exits rather than guessing where AgentAPI might be reachable.
- **Single-project.** One `--project` per middleware process. To watch multiple projects, run multiple instances on different `--port` / `--agentapi-port` pairs.

## Troubleshooting

- **`agentapi: command not found`** — install per Prerequisites and verify `where agentapi` (Windows) / `which agentapi` (Unix) returns a path.
- **AgentAPI doesn't reach ready within 30s** — usually means `claude` itself failed to start. Run `agentapi server --type=claude -- claude` manually in a terminal to see the underlying error.
- **`ANTHROPIC_API_KEY not set`** — create `middleware/.env` with the key. Don't `export` it from your shell — middleware specifically loads from the file so the configuration is reproducible.
- **Browser doesn't open on decision** — the URL is logged. Open `http://127.0.0.1:5000/decision` manually.
