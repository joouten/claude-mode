"""
claude-mode middleware — Phase 3 automation bridge.

Wires Claude Chat (Anthropic Messages API) to Claude Code (AgentAPI persistent
HTTP server) with a Flask web UI for User approval and a watchdog file watcher
that drives the full Chat <-> Code loop automatically.

Usage:
    python middleware.py --project C:\\path\\to\\project [--port 5000] [--agentapi-port 3284]
"""

from __future__ import annotations

import argparse
import atexit
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from anthropic import Anthropic, APIStatusError
from flask import Flask, redirect, render_template, request, url_for
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# ---------- constants ----------

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096
DEFAULT_FLASK_PORT = 5000
DEFAULT_AGENTAPI_PORT = 3284
FLASK_PORT_TRIES = 3
AGENTAPI_READY_TIMEOUT = 30          # seconds
AGENTAPI_POLL_INTERVAL = 0.5
DELIVERY_STABLE_TIMEOUT = 30
ACTIVITY_LOG_SIZE = 10
DEBOUNCE_SECONDS = 2.0
MAX_RETRIES = 5
BACKOFF_SCHEDULE = (2, 4, 8, 16, 32)  # seconds before retries 1..5 on 529
CHAT_TO_CODE = "CHAT_TO_CODE.md"
CODE_TO_CHAT = "CODE_TO_CHAT.md"
CLAUDE_MD = "CLAUDE.md"
ENV_FILENAME = ".env"

MIDDLEWARE_DIR = Path(__file__).resolve().parent
CLAUDE_MODE_ROOT = MIDDLEWARE_DIR.parent
TEMPLATE_PATH = CLAUDE_MODE_ROOT / "templates" / "CHAT_TO_CODE.md"

SYSTEM_PROMPT = """You are a technical decision-maker in a Claude Chat <-> Claude Code workflow.
Claude Code has hit a strategic decision point and generated the attached context.

Respond with a CHAT_TO_CODE.md document using this exact format and section order:

{template}

Project context (CLAUDE.md):
{claude_md}

Rules:
- Use "User" — never personal names.
- Pre-answer foreseeable decisions in DEFAULT BEHAVIORS so Code does not re-escalate them.
- Write WATCH FOR items as "[Condition] -> [action]" so every entry is self-actionable.
- Only ask User about genuine strategic decisions (what is built, how it is structured, or whether it meets a required standard)."""

# ---------- .env loader (hand-rolled — no python-dotenv dep) ----------

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_dotenv(path: Path) -> dict:
    """Read KEY=VALUE pairs from `path`. Ignores blank lines and `#` comments.
    Strips matching surrounding single or double quotes. Sets `os.environ` for
    any key not already present with a non-empty value (an existing empty-string
    env var is treated as unset, so `.env` wins). Returns the parsed mapping."""
    parsed: dict[str, str] = {}
    if not path.exists():
        return parsed
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = _ENV_LINE.match(raw)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        parsed[key] = value
        existing = os.environ.get(key, "")
        if not existing.strip():
            os.environ[key] = value
    return parsed


# ---------- state ----------

@dataclass
class State:
    project_path: Path
    flask_port: int = DEFAULT_FLASK_PORT
    agentapi_port: int = DEFAULT_AGENTAPI_PORT
    status: str = "starting"          # starting | watching | processing | awaiting_approval | delivering | error
    agentapi_running: bool = False
    last_event: str = ""
    activity_log: deque = field(default_factory=lambda: deque(maxlen=ACTIVITY_LOG_SIZE))
    pending_decision: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.last_event = f"[{ts}] {message}"
            self.activity_log.appendleft({"timestamp": ts, "message": message})
        logging.info(message)

    def set_status(self, status: str) -> None:
        with self._lock:
            self.status = status

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "project_path": str(self.project_path),
                "flask_port": self.flask_port,
                "agentapi_port": self.agentapi_port,
                "status": self.status,
                "agentapi_running": self.agentapi_running,
                "last_event": self.last_event,
                "activity_log": list(self.activity_log),
            }


# ---------- port utilities ----------

def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_free_port(start: int, tries: int = FLASK_PORT_TRIES) -> Optional[int]:
    for offset in range(tries):
        candidate = start + offset
        if port_available(candidate):
            return candidate
    return None


# ---------- agentapi subprocess ----------

_agentapi_proc: Optional[subprocess.Popen] = None


def find_agentapi() -> Optional[str]:
    return shutil.which("agentapi")


def launch_agentapi() -> subprocess.Popen:
    """Per brief: `agentapi server --type=claude -- claude`. AgentAPI binds to
    its default port 3284. The --agentapi-port CLI arg controls only what port
    we connect TO, not what port AgentAPI binds to."""
    global _agentapi_proc
    cmd = ["agentapi", "server", "--type=claude", "--", "claude"]
    logging.info("Launching: %s", " ".join(cmd))
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    _agentapi_proc = proc
    atexit.register(terminate_agentapi)
    return proc


def terminate_agentapi() -> None:
    global _agentapi_proc
    if _agentapi_proc is None:
        return
    if _agentapi_proc.poll() is not None:
        _agentapi_proc = None
        return
    try:
        if os.name == "nt":
            _agentapi_proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            _agentapi_proc.terminate()
        _agentapi_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _agentapi_proc.kill()
    except Exception as exc:  # pragma: no cover - defensive
        logging.warning("agentapi shutdown error: %s", exc)
    _agentapi_proc = None


def wait_for_agentapi(port: int, timeout: float = AGENTAPI_READY_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/status"
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(AGENTAPI_POLL_INTERVAL)
    return False


def deliver_to_code(content: str, port: int) -> tuple[bool, str]:
    """POST content to AgentAPI /message, then poll /status until stable."""
    url = f"http://127.0.0.1:{port}/message"
    try:
        r = requests.post(url, json={"content": content, "type": "user"}, timeout=10)
        r.raise_for_status()
    except requests.RequestException as exc:
        return False, f"POST /message failed: {exc}"

    status_url = f"http://127.0.0.1:{port}/status"
    deadline = time.time() + DELIVERY_STABLE_TIMEOUT
    while time.time() < deadline:
        try:
            r = requests.get(status_url, timeout=2)
            if r.status_code == 200:
                try:
                    if r.json().get("status") == "stable":
                        return True, "delivered (stable)"
                except ValueError:
                    pass
        except requests.RequestException:
            pass
        time.sleep(AGENTAPI_POLL_INTERVAL)
    return True, "delivered (status did not reach stable within timeout)"


# ---------- Messages API ----------

def load_template() -> str:
    try:
        return TEMPLATE_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        logging.warning("Could not load template at %s: %s", TEMPLATE_PATH, exc)
        return ""


def _extract_text(content_blocks) -> str:
    parts = []
    for block in content_blocks:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts).strip()


def _create_with_retry(client: Anthropic, state: "State", **kwargs):
    """Call `client.messages.create(**kwargs)` with exponential backoff on 529.

    Retries up to MAX_RETRIES times with the BACKOFF_SCHEDULE waits between
    attempts. Other APIStatusError codes (auth 4xx, non-529 5xx) and other
    exceptions re-raise immediately. After exhausting retries, raises
    RuntimeError so the caller's `Error during handoff:` path fires with a
    clear message instructing User how to retry the loop.
    """
    last_exc: Optional[APIStatusError] = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return client.messages.create(**kwargs)
        except APIStatusError as exc:
            if exc.status_code != 529:
                raise
            last_exc = exc
            if attempt >= MAX_RETRIES:
                break
            wait_s = BACKOFF_SCHEDULE[attempt]
            state.log(f"Retry {attempt + 1}/{MAX_RETRIES} after {wait_s}s — API overloaded")
            time.sleep(wait_s)
    raise RuntimeError(
        f"API overloaded after {MAX_RETRIES} retries — "
        "drop CODE_TO_CHAT.md again to retry the loop"
    ) from last_exc


def call_chat(
    client: Anthropic,
    state: "State",
    code_to_chat_content: str,
    project_path: Path,
    template: str,
) -> str:
    claude_md_path = project_path / CLAUDE_MD
    claude_md_content = "None"
    if claude_md_path.exists():
        try:
            claude_md_content = claude_md_path.read_text(encoding="utf-8")
        except Exception as exc:
            logging.warning("Failed reading %s: %s", claude_md_path, exc)

    system = SYSTEM_PROMPT.format(
        template=template or "(template unavailable — render a reasonable CHAT_TO_CODE.md)",
        claude_md=claude_md_content,
    )

    resp = _create_with_retry(
        client,
        state,
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": code_to_chat_content}],
    )
    return _extract_text(resp.content)


# ---------- watchdog handler ----------

class HandoffHandler(FileSystemEventHandler):
    """Watches the project dir for CODE_TO_CHAT.md changes.

    Uses a threading.Timer-based debounce: every watchdog event for the target
    file cancels any pending timer and starts a fresh one with DEBOUNCE_SECONDS
    delay. The handoff only fires when DEBOUNCE_SECONDS elapse with no new
    events, guaranteeing one API call per save regardless of how many create /
    modify events the filesystem emits per save (File Explorer commonly emits
    2-3 events; some editors emit even more).
    """

    def __init__(self, state: State, client: Anthropic, template: str):
        super().__init__()
        self.state = state
        self.client = client
        self.template = template
        self._debounce_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def _is_target(self, src_path: str) -> bool:
        return Path(src_path).name == CODE_TO_CHAT

    def _schedule_handoff(self, src_path: str) -> None:
        """Cancel any pending timer and arm a fresh one. The handoff fires
        when the timer elapses without being canceled by another event."""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            timer = threading.Timer(DEBOUNCE_SECONDS, self._process, args=(src_path,))
            timer.daemon = True
            self._debounce_timer = timer
            timer.start()

    def cancel_pending(self) -> None:
        """Cancel any pending debounce timer. Called from shutdown so the
        handler does not fire mid-shutdown."""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None

    def on_created(self, event):
        if event.is_directory:
            return
        if self._is_target(event.src_path):
            self._schedule_handoff(event.src_path)

    def on_modified(self, event):
        if event.is_directory:
            return
        if self._is_target(event.src_path):
            self._schedule_handoff(event.src_path)

    def _process(self, src_path: str) -> None:
        try:
            self.state.set_status("processing")
            self.state.log(f"Detected {CODE_TO_CHAT} change — calling Chat")
            content = Path(src_path).read_text(encoding="utf-8")
            response = call_chat(
                self.client, self.state, content, self.state.project_path, self.template
            )
            target = self.state.project_path / CHAT_TO_CODE
            target.write_text(response, encoding="utf-8")
            with self.state._lock:
                self.state.pending_decision = response
            self.state.set_status("awaiting_approval")
            self.state.log(f"Decision written to {CHAT_TO_CODE} — awaiting User approval")
            try:
                opened = webbrowser.open(f"http://127.0.0.1:{self.state.flask_port}/decision")
                if not opened:
                    self.state.log("webbrowser.open returned False — open the URL manually")
            except Exception as exc:
                self.state.log(f"webbrowser.open failed: {exc}")
        except Exception as exc:
            logging.exception("Handoff processing failed")
            self.state.set_status("error")
            self.state.log(f"Error during handoff: {exc}")


# ---------- Flask app ----------

def build_flask_app(state: State, client: Anthropic, template: str) -> Flask:
    app = Flask(__name__)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    @app.route("/")
    def index():
        return render_template("status.html", state=state.snapshot())

    @app.route("/decision")
    def decision():
        snap = state.snapshot()
        if snap["status"] != "awaiting_approval" or not state.pending_decision:
            return redirect(url_for("index"))
        return render_template(
            "decision.html",
            decision=state.pending_decision,
            project_path=snap["project_path"],
        )

    @app.route("/approve", methods=["POST"])
    def approve():
        snap = state.snapshot()
        if snap["status"] != "awaiting_approval":
            return redirect(url_for("index"))
        state.set_status("delivering")
        state.log("User approved — delivering to Code")
        message = (
            f"CHAT_TO_CODE.md has been approved. Read the file at "
            f"{state.project_path / CHAT_TO_CODE} and execute it per "
            f"/project:mode-cc instructions."
        )
        ok, msg = deliver_to_code(content=message, port=state.agentapi_port)
        if not ok:
            state.set_status("error")
            state.log(f"Delivery failed: {msg}")
        else:
            with state._lock:
                state.pending_decision = ""
            state.set_status("watching")
            state.log(f"Delivered to Code ({msg})")
        return redirect(url_for("index"))

    @app.route("/reject", methods=["POST"])
    def reject():
        snap = state.snapshot()
        if snap["status"] != "awaiting_approval":
            return redirect(url_for("index"))
        feedback = request.form.get("feedback", "").strip()
        if not feedback:
            return redirect(url_for("decision"))
        state.set_status("processing")
        state.log("User rejected — asking Chat to revise")
        try:
            revised = _create_with_retry(
                client,
                state,
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT.format(
                    template=template or "(template unavailable)",
                    claude_md="(unchanged from prior turn)",
                ),
                messages=[
                    {"role": "user", "content": "(prior CODE_TO_CHAT context — see prior assistant response)"},
                    {"role": "assistant", "content": state.pending_decision},
                    {
                        "role": "user",
                        "content": (
                            f"REJECTED by User. Feedback:\n\n{feedback}\n\n"
                            "Revise the CHAT_TO_CODE.md document to address this feedback. "
                            "Return only the revised document — no preamble, no commentary."
                        ),
                    },
                ],
            )
            new_text = _extract_text(revised.content)
            target = state.project_path / CHAT_TO_CODE
            target.write_text(new_text, encoding="utf-8")
            with state._lock:
                state.pending_decision = new_text
            state.set_status("awaiting_approval")
            state.log("Revision ready — awaiting User approval")
        except Exception as exc:
            logging.exception("Rejection revise failed")
            state.set_status("error")
            state.log(f"Revise failed: {exc}")
        return redirect(url_for("decision"))

    return app


# ---------- main ----------

def fatal(msg: str, exit_code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(exit_code)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="claude-mode middleware (Phase 3)")
    parser.add_argument("--project", required=True,
                        help="Path to the Claude Code project directory to watch")
    parser.add_argument("--port", type=int, default=DEFAULT_FLASK_PORT,
                        help="Flask web UI port (default 5000; falls back to 5001/5002)")
    parser.add_argument("--agentapi-port", type=int, default=DEFAULT_AGENTAPI_PORT,
                        help="Port AgentAPI is expected to listen on (default 3284)")
    args = parser.parse_args()

    # validate project path
    project_path = Path(args.project).resolve()
    if not project_path.is_dir():
        fatal(f"--project path does not exist or is not a directory: {project_path}")

    # load .env from middleware/.env
    load_dotenv(MIDDLEWARE_DIR / ENV_FILENAME)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        fatal(
            "ANTHROPIC_API_KEY not set. Add it to "
            f"{MIDDLEWARE_DIR / ENV_FILENAME} as: ANTHROPIC_API_KEY=sk-ant-..."
        )

    # check agentapi
    if find_agentapi() is None:
        fatal(
            "agentapi binary not found on PATH. "
            "Install it from https://github.com/coder/agentapi/releases "
            "(or `npm i -g agentapi`) and ensure it is on PATH."
        )

    # flask port (with fallback)
    flask_port = find_free_port(args.port, tries=FLASK_PORT_TRIES)
    if flask_port is None:
        fatal(
            f"No free Flask port available in range "
            f"{args.port}..{args.port + FLASK_PORT_TRIES - 1}"
        )
    if flask_port != args.port:
        logging.info("Flask fell back from port %s to %s", args.port, flask_port)

    # agentapi port (no fallback — brief says flag to User if busy)
    if not port_available(args.agentapi_port):
        fatal(
            f"AgentAPI port {args.agentapi_port} is in use. "
            "Stop the conflicting process or pass --agentapi-port <N>."
        )

    state = State(
        project_path=project_path,
        flask_port=flask_port,
        agentapi_port=args.agentapi_port,
    )
    template = load_template()

    # launch agentapi
    state.log("Launching AgentAPI subprocess")
    launch_agentapi()

    # wait for ready
    state.log(f"Waiting for AgentAPI at http://127.0.0.1:{args.agentapi_port}/status")
    if not wait_for_agentapi(args.agentapi_port):
        terminate_agentapi()
        fatal(f"AgentAPI did not become ready within {AGENTAPI_READY_TIMEOUT}s")
    state.agentapi_running = True
    state.log("AgentAPI is ready")

    # anthropic client (reads ANTHROPIC_API_KEY from env)
    client = Anthropic()

    # watchdog
    handler = HandoffHandler(state=state, client=client, template=template)
    observer = Observer()
    observer.schedule(handler, str(project_path), recursive=False)
    observer.start()
    state.log(f"Watching {project_path} for {CODE_TO_CHAT}")

    # flask in a daemon thread
    app = build_flask_app(state, client, template)

    def run_flask():
        app.run(host="127.0.0.1", port=flask_port,
                debug=False, use_reloader=False, threaded=True)

    flask_thread = threading.Thread(target=run_flask, name="flask", daemon=True)
    flask_thread.start()
    state.set_status("watching")

    # banner
    print()
    print("claude-mode middleware running")
    print(f"Watching:   {project_path}")
    print(f"Web UI:     http://127.0.0.1:{flask_port}")
    print(f"AgentAPI:   http://127.0.0.1:{args.agentapi_port}")
    print()
    print("Press Ctrl+C to stop.")
    print()

    # shutdown coordination
    shutdown_event = threading.Event()

    def handle_signal(signum, frame):
        logging.info("Received signal %s — shutting down", signum)
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    for sig_name in ("SIGTERM", "SIGBREAK"):
        # SIGBREAK is Windows-only — Ctrl+Break / CTRL_BREAK_EVENT.
        # SIGTERM may not be settable in some Windows contexts.
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handle_signal)
        except (ValueError, OSError):
            pass

    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=1.0)
    finally:
        state.log("Shutting down")
        try:
            handler.cancel_pending()  # cancel any pending debounce timer
        except Exception:
            pass
        try:
            observer.stop()
            observer.join(timeout=5)
        except Exception:
            pass
        terminate_agentapi()
        logging.info("Shutdown complete")

    return 0


if __name__ == "__main__":
    sys.exit(main())
