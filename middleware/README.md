# claude-mode middleware (Phase 3, Agent SDK)

Automation bridge that runs the full Claude Chat ↔ Claude Code loop without User intervention except at the approval step.

## What it does

```
User starts middleware.py against a project
   ↓
middleware starts Flask web UI (port 5000)
middleware starts watchdog file watcher on the project directory
   ↓
Code (in any session) hits a strategic decision point
   → writes CODE_TO_CHAT.md to project root
   ↓
watchdog fires
   → middleware reads CODE_TO_CHAT.md + project CLAUDE.md
   → calls Anthropic Messages API
   → writes CHAT_TO_CODE.md to project root
   → opens http://localhost:5000/decision in User's browser
   ↓
User reviews in browser
   → APPROVE  → middleware spawns a fresh Claude Code session via the
                Claude Agent SDK with CHAT_TO_CODE.md as the prompt
   → REJECT   → middleware sends feedback to Chat → loop revises until approved
   ↓
Code executes the brief; if it hits another decision point and writes
CODE_TO_CHAT.md, the watchdog catches it and the next loop iteration
starts automatically.
```

## Prerequisites

- **Python 3.11+** (tested on 3.14)
- **Claude Code CLI** on PATH — the Agent SDK shells out to `claude`
- **Anthropic API key** in `middleware/.env`:

  ```
  ANTHROPIC_API_KEY=sk-ant-...
  ```

  `.env` is `.gitignore`d at the claude-mode repo root — never committed.

No AgentAPI binary required. (Earlier versions of this middleware used AgentAPI as a PTY-mediated transport and had persistent stability problems; v3 uses the Claude Agent SDK directly. See **History** at the bottom.)

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

On startup you should see:

```
claude-mode middleware running (Agent SDK)
Watching:   C:\path\to\your\project
Web UI:     http://127.0.0.1:5000

Press Ctrl+C to stop.
```

Startup is near-instant — there is no subprocess to launch and no port to wait on. Ctrl+C performs a clean shutdown: cancels any pending debounce timer, stops the watchdog, exits.

## Web UI

| Route | Purpose |
| --- | --- |
| `GET /` | Status page. Auto-refreshes every 3 seconds. Shows current state, project path, last event, and the 10 most recent activity log entries. |
| `GET /decision` | Approval page. Renders the pending `CHAT_TO_CODE.md` in full with **APPROVE** and **REJECT** buttons. Rejection reveals a feedback textarea before submitting. |
| `POST /approve` | Hands the approved brief to Claude Code via the Agent SDK in a fire-and-forget daemon thread, then immediately returns to the status page. The status page transitions through `delivering → executing → watching` (or `error`) on auto-refresh. |
| `POST /reject` | Sends User's feedback back to the Messages API, gets a revised brief, returns to the decision page. |

Middleware does not open the browser for you. Keep the status page (`http://localhost:5000/`) open — it auto-refreshes every 3 seconds, so the `awaiting_approval` status (and the inline "Review now →" card) appears within seconds of a new brief being ready.

### Status values

| Status | Meaning | Color |
| --- | --- | --- |
| `watching` | Idle. Waiting for `CODE_TO_CHAT.md` to appear or change. | green |
| `processing` | Calling the Anthropic Messages API to generate a brief. | blue |
| `awaiting_approval` | A brief is ready for User review at `/decision`. | yellow |
| `delivering` | User approved; spawning the SDK delivery thread. | blue |
| `executing` | Code is running the approved brief via the Agent SDK. | blue |
| `error` | Something went wrong; see the activity log for details. | red |

## Architecture notes

- **Fresh Code session per delivery.** Each approval starts a new `query()` against the Claude Agent SDK with `cwd` set to the project path and `setting_sources=["project"]` so the project's `CLAUDE.md` loads automatically. No persistent server, no port management, no PTY. CHAT_TO_CODE.md is the unit of context — it carries everything Code needs.
- **Fire-and-forget delivery.** `deliver_to_code()` spawns a daemon thread that runs `asyncio.run()` on the SDK's async iterator. The `/approve` route returns immediately so the browser doesn't hang on long-running execution. State updates inside the thread show up in the status page on the next 3-second refresh.
- **`permission_mode="bypassPermissions"`.** Code runs the approved brief without per-tool permission prompts. The User-approval gate in this architecture is the `/decision` web UI, not Code's tool prompts — the brief is the unit of consent. **Run middleware only against projects you trust.**
- **Watchdog continues during execution.** If Code writes a new `CODE_TO_CHAT.md` while running, the watchdog catches it and the next loop iteration starts — no special coordination needed.
- **Event-driven, not polled.** The watchdog `Observer` fires immediately on `CODE_TO_CHAT.md` create/modify events. A `threading.Timer`-based debounce (2-second quiet window — every event cancels the pending timer and arms a fresh one) coalesces the multiple events that File Explorer copy/paste, IDE saves, and editor atomic-replace operations commonly emit. Net effect: exactly one Messages API call per save, regardless of how many filesystem events fire.
- **Resilient to API overload.** Calls to `client.messages.create` (the Chat side) are wrapped in `_create_with_retry`, which retries on `APIStatusError(status_code=529)` — Anthropic's `overloaded_error` — with exponential backoff (2s, 4s, 8s, 16s, 32s; 5 retries max). Other errors raise immediately. After exhausting retries, the handler logs a clear instruction: `drop CODE_TO_CHAT.md again to retry the loop`.
- **State is in-process.** A single `State` dataclass guarded by a `threading.Lock` holds status, port, activity log, and pending decision. No database, no persistence — restart resets everything.
- **`.env` is hand-rolled.** No `python-dotenv` dependency. The parser handles `KEY=VALUE`, comments, and quoted values, and treats an existing empty-string env var as unset so `.env` wins.
- **Shutdown is coordinated.** `signal.SIGINT` / `SIGTERM` / `SIGBREAK` set a `threading.Event` that the main loop blocks on. The `finally` block cancels any pending debounce timer and stops the watchdog observer. Any in-flight SDK delivery thread is a daemon and is killed on process exit (acceptable for v1 — User can re-approve from `/decision` if a delivery was interrupted).

## Limitations (v3 v1)

- **No streaming output in the UI.** The status page shows `executing (Code is running…)` while a delivery is in flight; individual messages from the SDK are not surfaced. v2 could stream them via Server-Sent Events.
- **Fresh session per delivery.** No conversation continuity between deliveries. CHAT_TO_CODE.md is meant to carry everything Code needs — if your workflow needs persistent state across iterations, encode it in `CLAUDE.md`.
- **Rejection flow context.** The reject path replays only the prior assistant message — it does not include the original `CODE_TO_CHAT.md` content in the revise turn. Good enough for "tighten this paragraph"; weak for "rethink the whole decision."
- **No template-format validation.** The Messages API response is written to `CHAT_TO_CODE.md` as-is. If Chat returns malformed Markdown, User catches it visually in the decision page.
- **Single-project.** One `--project` per middleware process. To watch multiple projects, run multiple instances on different `--port` values.
- **`bypassPermissions`.** Code runs without per-tool prompts. If the brief is wrong, the execution will run anyway. The brief format's `CONSTRAINTS` and the User review at `/decision` are the only guardrails.

## Troubleshooting

- **`ANTHROPIC_API_KEY not set`** — create `middleware/.env` with the key. Don't `export` it from your shell — middleware specifically loads from the file so the configuration is reproducible.
- **`Agent SDK error: CLINotFoundError`** — the SDK couldn't find the `claude` CLI on PATH. Install Claude Code and verify `where claude` (Windows) / `which claude` (Unix) returns a path.
- **Status stuck at `executing` for a long time** — Code is running. The status flips to `watching` (success) or `error` when the SDK call returns. If it never returns, check the middleware terminal for tracebacks.

## History

- **v1 (initial Phase 3)** — used [AgentAPI](https://github.com/coder/agentapi) as a PTY-mediated transport with a persistent Claude Code session on port 3284. Repeatedly hit pre-stable poll timeouts and POST `/message` timeouts that resisted multiple fix attempts (auto-killing stale processes, `--dangerously-skip-permissions`, retry/backoff, increased timeouts, per-poll diagnostics).
- **v3 (current)** — replaced AgentAPI with the Claude Agent SDK. Direct programmatic interface, no subprocess management, no port for Code, no PTY. Trade-off accepted: fresh session per delivery instead of persistent. CHAT_TO_CODE.md carries the context.
