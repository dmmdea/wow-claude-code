"""Backends: the ways a request from the game gets answered.

Three kinds, one interface:

* ``ClaudeCodeBackend`` runs the installed Claude Code CLI in print mode inside a
  project folder (a real agentic session: it reads and edits files, runs
  commands as allowed by the permission mode, and can be resumed by session id).
* ``OpenAIChatBackend`` talks to any OpenAI-compatible ``/chat/completions``
  endpoint (a hosted router, a local llama.cpp / vLLM / Ollama / LM Studio
  server, ...) with the conversation kept by the companion.
* ``AnthropicBackend`` talks to the Anthropic Messages API directly.

Every backend streams progress through ``on_event`` and returns a ``TurnResult``.
Keys come from ``config.resolve_api_key`` and are only ever placed in request
headers or the child process environment; they are never logged.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from wowclaude import __version__
from wowclaude.config import BackendConfig, resolve_api_key

DEFAULT_CHAT_SYSTEM_PROMPT = (
    "You are answering inside a small text window of a World of Warcraft addon. "
    "Be concise and use plain text: no markdown tables, no code fences wider than 70 columns."
)

# Environment variables that identify a running Claude Code session. The companion
# may itself be started from a Claude Code terminal; the child must not inherit
# them or it would behave as a nested session. User settings such as provider
# switches are left alone.
_NESTED_SESSION_MARKERS = (
    "CLAUDECODE",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_PID",
)


@dataclass
class TurnEvent:
    """One piece of progress: ``init``, ``text``, ``tool``, ``tool_result``,
    ``status``, ``result`` or ``error``."""

    kind: str
    text: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class TurnResult:
    text: str = ""
    session_id: str | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
    is_error: bool = False
    error: str | None = None
    usage: dict = field(default_factory=dict)


EventSink = Callable[[TurnEvent], None]


class Backend(Protocol):
    name: str

    def describe(self) -> str: ...

    def run_turn(
        self,
        prompt: str,
        session: dict,
        project_dir: Path | None,
        on_event: EventSink,
        stop: threading.Event,
    ) -> TurnResult: ...


def make_backend(name: str, bc: BackendConfig) -> Backend:
    if bc.type == "claude-code":
        return ClaudeCodeBackend(name, bc)
    if bc.type == "openai":
        return OpenAIChatBackend(name, bc)
    if bc.type == "anthropic":
        return AnthropicBackend(name, bc)
    raise ValueError(f"backend {name!r}: unknown type {bc.type!r}")


def _popen_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _truncate(text: str, limit: int = 300) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ----------------------------------------------------------------- claude-code

def resolve_claude_bin(bc: BackendConfig) -> str | None:
    """Absolute path of the Claude Code CLI named by the config, or None."""
    candidate = os.path.expanduser(bc.claude_bin)
    if os.path.isabs(candidate) and os.path.isfile(candidate):
        return candidate
    found = shutil.which(candidate)
    if found:
        return found
    if sys.platform == "win32":
        for name in ("claude.exe", "claude.cmd"):
            found = shutil.which(name)
            if found:
                return found
    return None


def _child_env(bc: BackendConfig) -> dict[str, str]:
    env = dict(os.environ)
    for marker in _NESTED_SESSION_MARKERS:
        env.pop(marker, None)
    key = resolve_api_key(bc)
    if key:
        env["ANTHROPIC_API_KEY"] = key
    return env


def claude_version(bc: BackendConfig) -> str | None:
    exe = resolve_claude_bin(bc)
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=30,
            creationflags=_popen_flags(), env=_child_env(bc),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout or out.stderr).strip() or None


def claude_auth_summary(bc: BackendConfig) -> str:
    """A one-line description of how the CLI will authenticate. Never includes a key."""
    if resolve_api_key(bc):
        source = f"env {bc.api_key_env}" if bc.api_key_env and os.environ.get(bc.api_key_env) else f"file {bc.api_key_file}"
        return f"API key from {source} (passed to the CLI as ANTHROPIC_API_KEY)"
    exe = resolve_claude_bin(bc)
    if not exe:
        return "no key configured and the CLI was not found"
    try:
        out = subprocess.run(
            [exe, "auth", "status"], capture_output=True, text=True, timeout=30,
            creationflags=_popen_flags(), env=_child_env(bc),
        )
        info = json.loads(out.stdout) if out.stdout.strip().startswith("{") else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        info = {}
    if info.get("loggedIn"):
        return f"CLI login ({info.get('authMethod', 'unknown method')})"
    return "no key configured and the CLI is not logged in (run: claude, then /login)"


class ClaudeCodeBackend:
    def __init__(self, name: str, bc: BackendConfig) -> None:
        self.name = name
        self.bc = bc

    def describe(self) -> str:
        return f"claude-code (bin={self.bc.claude_bin}, permission_mode={self.bc.permission_mode})"

    def build_argv(self, exe: str, resume_id: str | None) -> list[str]:
        """The command line. The prompt is NOT part of it: it is written to the
        child's stdin, so prompt text can never be parsed as an option and never
        meets a shell (an npm ``claude.cmd`` shim runs through cmd.exe)."""
        bc = self.bc
        argv = [
            exe, "-p", "--output-format", "stream-json", "--verbose",
            "--permission-mode", bc.permission_mode, "--permission-prompts", "none",
        ]
        # The budget cap protects an API key from a runaway run. With a CLI
        # login the CLI still computes a nominal cost that nobody is billed, and
        # a cap would only cut turns short, so it applies with a key only.
        if bc.max_budget_usd is not None and resolve_api_key(bc):
            argv += ["--max-budget-usd", str(bc.max_budget_usd)]
        if resume_id:
            argv += ["--resume", resume_id]
        if bc.model:
            argv += ["--model", bc.model]
        if bc.allowed_tools:
            argv += ["--allowedTools", *bc.allowed_tools]
        if bc.system_prompt:
            argv += ["--append-system-prompt", bc.system_prompt]
        argv += list(bc.extra_args)
        return argv

    def run_turn(self, prompt: str, session: dict, project_dir: Path | None, on_event: EventSink, stop: threading.Event) -> TurnResult:
        exe = resolve_claude_bin(self.bc)
        if not exe:
            msg = f"Claude Code CLI not found (claude_bin={self.bc.claude_bin!r}); install it or set backends.{self.name}.claude_bin"
            on_event(TurnEvent("error", msg))
            return TurnResult(is_error=True, error=msg)
        cwd = project_dir if project_dir and project_dir.is_dir() else None
        if project_dir and cwd is None:
            msg = f"project folder does not exist: {project_dir}"
            on_event(TurnEvent("error", msg))
            return TurnResult(is_error=True, error=msg)
        resume_id = session.get("claude_session_id")
        argv = self.build_argv(exe, resume_id)
        started = time.time()
        try:
            proc = subprocess.Popen(
                argv, cwd=cwd, env=_child_env(self.bc),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", creationflags=_popen_flags(),
            )
        except OSError as exc:
            msg = f"could not start the Claude Code CLI: {exc}"
            on_event(TurnEvent("error", msg))
            return TurnResult(is_error=True, error=msg)

        stderr_chunks: list[str] = []

        def feed_prompt() -> None:
            assert proc.stdin is not None
            try:
                proc.stdin.write(prompt)
            except (OSError, ValueError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        def drain_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_chunks.append(line)

        threading.Thread(target=feed_prompt, daemon=True).start()

        def watch_stop() -> None:
            while proc.poll() is None:
                if stop.wait(0.25):
                    proc.terminate()
                    return

        threading.Thread(target=drain_stderr, daemon=True).start()
        threading.Thread(target=watch_stop, daemon=True).start()

        result = TurnResult(session_id=resume_id)
        text_parts: list[str] = []
        saw_result = False
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                on_event(TurnEvent("status", _truncate(line)))
                continue
            kind = msg.get("type")
            if kind == "system" and msg.get("subtype") == "init":
                result.session_id = msg.get("session_id") or result.session_id
                on_event(TurnEvent("init", f"session {result.session_id} model {msg.get('model', '?')}", msg))
            elif kind == "assistant":
                for block in (msg.get("message") or {}).get("content") or []:
                    btype = block.get("type")
                    if btype == "text" and block.get("text"):
                        text_parts.append(block["text"])
                        on_event(TurnEvent("text", block["text"]))
                    elif btype == "tool_use":
                        on_event(TurnEvent("tool", _tool_summary(block), block))
            elif kind == "user":
                for block in (msg.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        content = block.get("content")
                        if isinstance(content, list):
                            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                        on_event(TurnEvent("tool_result", _truncate(str(content or ""), 200), block))
            elif kind == "result":
                saw_result = True
                result.session_id = msg.get("session_id") or result.session_id
                result.cost_usd = msg.get("total_cost_usd")
                result.duration_ms = msg.get("duration_ms")
                result.num_turns = msg.get("num_turns")
                result.usage = msg.get("usage") or {}
                result.is_error = bool(msg.get("is_error"))
                final = msg.get("result")
                if isinstance(final, str) and final.strip():
                    if not text_parts or text_parts[-1].strip() != final.strip():
                        text_parts.append(final)
                if result.is_error:
                    result.error = _result_error(msg)
                on_event(TurnEvent("result", _truncate(str(final or ""), 200), msg))
        code = proc.wait()
        if stop.is_set():
            result.is_error = True
            result.error = "stopped by the user"
        elif not saw_result:
            tail = _truncate("".join(stderr_chunks[-20:]), 500)
            result.is_error = True
            result.error = f"claude exited with code {code} without a result" + (f": {tail}" if tail else "")
            on_event(TurnEvent("error", result.error))
        result.text = "\n\n".join(p.strip() for p in text_parts if p.strip())
        if result.duration_ms is None:
            result.duration_ms = int((time.time() - started) * 1000)
        if result.session_id:
            session["claude_session_id"] = result.session_id
        return result


_RESULT_SUBTYPES = {
    "error_max_budget_usd": "budget cap reached (max_budget_usd); raise it in the backend config to continue",
    "error_max_turns": "turn limit reached",
    "error_during_execution": "the CLI reported an error during execution",
}


def _result_error(msg: dict) -> str:
    subtype = str(msg.get("subtype") or "")
    text = msg.get("result")
    if subtype in _RESULT_SUBTYPES:
        return _RESULT_SUBTYPES[subtype]
    if isinstance(text, str) and text.strip():
        return _truncate(text, 500)
    return subtype or "error"


def _tool_summary(block: dict) -> str:
    name = block.get("name", "tool")
    inp = block.get("input") or {}
    if not isinstance(inp, dict):
        return name
    for key in ("file_path", "path", "command", "pattern", "query", "url", "description"):
        if key in inp and isinstance(inp[key], str):
            return f"{name}: {_truncate(inp[key], 120)}"
    return name


# ------------------------------------------------------------------- HTTP chat

def _trim_history(messages: list[dict], limit: int) -> list[dict]:
    if limit <= 0 or len(messages) <= limit:
        return messages
    return messages[-limit:]


def _sse_data_lines(resp) -> "iter[str]":  # type: ignore[valid-type]
    """Yield the payload of each ``data:`` line of a server-sent-events body."""
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line.startswith("data:"):
            yield line[5:].strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: urllib would replay the key headers to the new host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise urllib.error.HTTPError(req.full_url, code, f"redirect refused (to {newurl}); set base_url to the final host", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _post(url: str, headers: dict[str, str], body: dict, timeout: float):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", f"wowclaude/{__version__}")
    return _OPENER.open(req, timeout=timeout)


_BEARER = re.compile(r"(?i)(bearer\s+)\S+")
_KEYISH = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")


def _scrub(text: str, key: str | None) -> str:
    """Remove key material from text that may be logged or shown in the game."""
    if key:
        text = text.replace(key, "***")
    text = _BEARER.sub(r"\1***", text)
    return _KEYISH.sub("sk-***", text)


def _http_error_text(exc: Exception, key: str | None = None) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - best effort
            body = ""
        return _scrub(f"HTTP {exc.code}: {_truncate(body, 400) or exc.reason}", key)
    if isinstance(exc, urllib.error.URLError):
        return _scrub(f"connection failed: {exc.reason}", key)
    return _scrub(f"{type(exc).__name__}: {exc}", key)


class _ChatBackendBase:
    def __init__(self, name: str, bc: BackendConfig) -> None:
        self.name = name
        self.bc = bc

    def _history(self, session: dict) -> list[dict]:
        messages = session.setdefault("messages", [])
        if not isinstance(messages, list):
            messages = []
            session["messages"] = messages
        return messages

    def _finish(self, session: dict, prompt: str, reply: str, result: TurnResult) -> TurnResult:
        history = self._history(session)
        history.append({"role": "user", "content": prompt})
        if reply.strip():
            history.append({"role": "assistant", "content": reply if not result.is_error else reply + "\n[turn ended early: " + (result.error or "error") + "]"})
        result.text = reply
        return result


class OpenAIChatBackend(_ChatBackendBase):
    def describe(self) -> str:
        return f"openai-compatible ({self.bc.base_url}, model={self.bc.model})"

    def run_turn(self, prompt: str, session: dict, project_dir: Path | None, on_event: EventSink, stop: threading.Event) -> TurnResult:
        bc = self.bc
        url = (bc.base_url or "").rstrip("/") + "/chat/completions"
        headers = dict(bc.extra_headers)
        key = resolve_api_key(bc)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        system = bc.system_prompt or DEFAULT_CHAT_SYSTEM_PROMPT
        messages = [{"role": "system", "content": system}]
        messages += _trim_history(self._history(session), bc.max_history_messages)
        messages.append({"role": "user", "content": prompt})
        body: dict = {
            "model": bc.model, "messages": messages, "stream": True, "max_tokens": bc.max_tokens,
            "stream_options": {"include_usage": True},
        }
        if bc.temperature is not None:
            body["temperature"] = bc.temperature
        result = TurnResult(session_id=session.get("key"))
        parts: list[str] = []
        started = time.time()
        on_event(TurnEvent("init", f"{self.describe()}"))
        try:
            try:
                resp = _post(url, headers, body, bc.timeout_sec)
            except urllib.error.HTTPError as exc:
                # A server that does not know stream_options answers 400; retry without it.
                if exc.code != 400 or "stream_options" not in body:
                    raise
                body.pop("stream_options", None)
                resp = _post(url, headers, body, bc.timeout_sec)
            with resp:
                ctype = resp.headers.get("Content-Type", "")
                if "text/event-stream" not in ctype:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
                    choice = (data.get("choices") or [{}])[0]
                    content = (choice.get("message") or {}).get("content") or ""
                    parts.append(content)
                    on_event(TurnEvent("text", content))
                    result.usage = data.get("usage") or {}
                else:
                    for payload in _sse_data_lines(resp):
                        if stop.is_set():
                            result.is_error = True
                            result.error = "stopped by the user"
                            break
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except ValueError:
                            continue
                        if chunk.get("usage"):
                            result.usage = chunk["usage"]
                        for choice in chunk.get("choices") or []:
                            delta = (choice.get("delta") or {}).get("content")
                            if delta:
                                parts.append(delta)
                                on_event(TurnEvent("text", delta))
                        if chunk.get("error"):
                            result.is_error = True
                            result.error = _truncate(json.dumps(chunk["error"]), 400)
                            break
        except Exception as exc:  # noqa: BLE001 - every failure becomes a result
            result.is_error = True
            result.error = _http_error_text(exc, key)
            on_event(TurnEvent("error", result.error))
        if result.error:
            result.error = _scrub(result.error, key)
        result.duration_ms = int((time.time() - started) * 1000)
        cost = result.usage.get("cost") if isinstance(result.usage, dict) else None
        if isinstance(cost, (int, float)):
            result.cost_usd = float(cost)
        reply = "".join(parts)
        on_event(TurnEvent("result", _truncate(reply, 200)))
        return self._finish(session, prompt, reply, result)


class AnthropicBackend(_ChatBackendBase):
    DEFAULT_BASE_URL = "https://api.anthropic.com"
    API_VERSION = "2023-06-01"

    def describe(self) -> str:
        return f"anthropic messages api (model={self.bc.model})"

    def run_turn(self, prompt: str, session: dict, project_dir: Path | None, on_event: EventSink, stop: threading.Event) -> TurnResult:
        bc = self.bc
        key = resolve_api_key(bc)
        if not key:
            msg = f"backend {self.name}: no API key (set env {bc.api_key_env or 'ANTHROPIC_API_KEY'} or backends.{self.name}.api_key_file)"
            on_event(TurnEvent("error", msg))
            return TurnResult(is_error=True, error=msg)
        url = (bc.base_url or self.DEFAULT_BASE_URL).rstrip("/") + "/v1/messages"
        headers = dict(bc.extra_headers)
        headers["x-api-key"] = key
        headers["anthropic-version"] = self.API_VERSION
        messages = _trim_history(self._history(session), bc.max_history_messages) + [{"role": "user", "content": prompt}]
        body: dict = {
            "model": bc.model, "max_tokens": bc.max_tokens, "messages": messages, "stream": True,
            "system": bc.system_prompt or DEFAULT_CHAT_SYSTEM_PROMPT,
        }
        if bc.temperature is not None:
            body["temperature"] = bc.temperature
        result = TurnResult(session_id=session.get("key"))
        parts: list[str] = []
        started = time.time()
        on_event(TurnEvent("init", self.describe()))
        try:
            with _post(url, headers, body, bc.timeout_sec) as resp:
                for payload in _sse_data_lines(resp):
                    if stop.is_set():
                        result.is_error = True
                        result.error = "stopped by the user"
                        break
                    try:
                        event = json.loads(payload)
                    except ValueError:
                        continue
                    etype = event.get("type")
                    if etype == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            parts.append(delta["text"])
                            on_event(TurnEvent("text", delta["text"]))
                    elif etype == "message_start":
                        result.usage.update(((event.get("message") or {}).get("usage")) or {})
                    elif etype == "message_delta":
                        result.usage.update(event.get("usage") or {})
                    elif etype == "error":
                        result.is_error = True
                        result.error = _truncate(json.dumps(event.get("error")), 400)
                        break
                    elif etype == "message_stop":
                        break
        except Exception as exc:  # noqa: BLE001
            result.is_error = True
            result.error = _http_error_text(exc, key)
            on_event(TurnEvent("error", result.error))
        if result.error:
            result.error = _scrub(result.error, key)
        result.duration_ms = int((time.time() - started) * 1000)
        reply = "".join(parts)
        on_event(TurnEvent("result", _truncate(reply, 200)))
        return self._finish(session, prompt, reply, result)
