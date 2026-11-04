"""The bridge: SavedVariables in, data addon file out.

This module owns the companion's state (``state.json`` in the companion home):
which request ids were consumed, every session's transcript and backend state,
and what the data file last contained. Nothing else writes that state.

Flow of one request:

1. The addon stores ``{id, kind, session_key, project, backend, prompt, created_at}``
   in ``ClaudeCodeDB.outbox`` and the game writes the SavedVariables file on
   /reload (or the user copies the same record as a clipboard envelope).
2. ``scan`` parses the file(s), ``submit`` queues every id not yet consumed.
3. ``work_once`` runs one queued request on its backend, writing the data file
   when the turn starts, every couple of seconds while text streams, and at
   the end.
4. The user presses Refresh (/reload) in the game; the addon reads
   ``ClaudeCode_Data`` and prunes consumed ids from its outbox.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from wowclaude import __version__
from wowclaude import config as cfgmod
from wowclaude.backends import Backend, TurnEvent, make_backend
from wowclaude.luaio import LuaParseError, read_saved_variables, write_lua_file

ENVELOPE_PREFIX = "CLAUDECODE-WOW:1:"
DATA_GLOBAL = "ClaudeCode_Data"
DATA_SCHEMA = 1
DATA_BUDGET_BYTES = 1_000_000
MAX_SESSIONS = 20
MAX_ENTRY_CHARS = 12_000
MAX_PROMPT_CHARS = 20_000
MAX_REQUESTS_PER_SCAN = 20
STREAM_FLUSH_SEC = 2.0
REQUEST_KINDS = ("prompt", "stop", "new")

Logger = Callable[[str], None]


@dataclass
class Request:
    id: str
    kind: str
    session_key: str
    project: str
    backend: str
    prompt: str
    created_at: int
    nonce: str = ""

    @classmethod
    def from_record(cls, rec: dict) -> "Request | None":
        rid = rec.get("id")
        kind = rec.get("kind", "prompt")
        if not isinstance(rid, str) or not rid or kind not in REQUEST_KINDS:
            return None
        prompt = rec.get("prompt", "")
        prompt = prompt if isinstance(prompt, str) else str(prompt)
        return cls(
            id=rid[:120],
            kind=kind,
            session_key=str(rec.get("session_key") or "")[:120],
            project=str(rec.get("project") or "")[:120],
            backend=str(rec.get("backend") or "")[:120],
            prompt=prompt[:MAX_PROMPT_CHARS],
            created_at=int(rec.get("created_at") or 0),
            nonce=str(rec.get("nonce") or ""),
        )


def _records(value: object) -> list[dict]:
    """``outbox`` as written by the client: a list, or an empty table (``{}``)."""
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    if isinstance(value, dict):
        return [r for _, r in sorted(value.items(), key=lambda kv: str(kv[0])) if isinstance(r, dict)]
    return []


def parse_requests(saved: dict) -> list[Request]:
    """Every request record in a parsed SavedVariables file, in outbox order."""
    db = saved.get("ClaudeCodeDB")
    if not isinstance(db, dict):
        return []
    out: list[Request] = []
    for rec in _records(db.get("outbox")):
        req = Request.from_record(rec)
        if req:
            out.append(req)
    return out


def parse_envelope(text: str) -> Request | None:
    """A request pasted as one line: ``CLAUDECODE-WOW:1:{...json...}``."""
    if not text.startswith(ENVELOPE_PREFIX):
        return None
    try:
        rec = json.loads(text[len(ENVELOPE_PREFIX):].strip())
    except ValueError:
        return None
    return Request.from_record(rec) if isinstance(rec, dict) else None


class Bridge:
    def __init__(self, cfg: cfgmod.Config, log: Logger | None = None, notify: Callable[[str, str], object] | None = None) -> None:
        self.cfg = cfg
        self.log = log or (lambda msg: None)
        self.notify = notify
        self.state_path = cfgmod.home() / "state.json"
        self.state: dict = self._load_state()
        self.queue: deque[Request] = deque()
        self.current: Request | None = None
        self.stop_event = threading.Event()
        self._backends: dict[str, Backend] = {}
        self._last_flush = 0.0
        self._sv_mtimes: dict[str, float] = {}
        self._sources: dict[str, str] = {}

    # ----------------------------------------------------------------- state

    def _load_state(self) -> dict:
        if self.state_path.is_file():
            try:
                with self.state_path.open(encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    data.setdefault("consumed", [])
                    data.setdefault("sessions", {})
                    data.setdefault("nonce", secrets.token_hex(8))
                    return data
                raise ValueError("not a JSON object")
            except (OSError, ValueError) as exc:
                # Keep the damaged file for inspection; never silently overwrite it.
                bad = self.state_path.with_name(f"state.json.bad-{int(time.time())}")
                try:
                    os.replace(self.state_path, bad)
                    self.log(f"state file unreadable ({exc}); moved to {bad.name} and starting fresh")
                except OSError:
                    self.log(f"state file unreadable ({exc}); starting fresh")
        return {"consumed": [], "sessions": {}, "nonce": secrets.token_hex(8)}

    def save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.state_path)

    @property
    def nonce(self) -> str:
        """A per-install token the addon echoes in clipboard envelopes, so text
        another process or a web page put on the clipboard is ignored."""
        return str(self.state.get("nonce") or "")

    def consumed(self) -> set[str]:
        return set(self.state.get("consumed", []))

    def sessions(self) -> dict:
        return self.state.setdefault("sessions", {})

    # ---------------------------------------------------------------- intake

    def scan(self, force: bool = False) -> list[Request]:
        """Requests found in the SavedVariables files that changed since the last scan."""
        found: list[Request] = []
        for path in cfgmod.saved_variables_files(self.cfg):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            key = str(path)
            if not force and self._sv_mtimes.get(key) == mtime:
                continue
            try:
                saved = read_saved_variables(path)
            except LuaParseError as exc:
                # The client is probably mid-write; the next tick re-reads it.
                self.log(f"skipping {path.name} this tick (parse error at line {exc.line}: {exc.msg})")
                continue
            except OSError as exc:
                self.log(f"skipping {path.name} this tick ({exc})")
                continue
            self._sv_mtimes[key] = mtime
            requests = parse_requests(saved)
            if len(requests) > MAX_REQUESTS_PER_SCAN:
                self.log(f"{path.name}: {len(requests)} requests in the outbox; taking the oldest {MAX_REQUESTS_PER_SCAN} this tick")
                requests = requests[:MAX_REQUESTS_PER_SCAN]
            for req in requests:
                req_source = path.parent.parent.name
                self._sources[req.id] = req_source
            found.extend(requests)
        return found

    def submit(self, req: Request, source: str = "") -> bool:
        """Queue a request unless it was consumed or queued already."""
        if req.id in self.consumed() or any(q.id == req.id for q in self.queue) or (self.current and self.current.id == req.id):
            return False
        source = source or self._sources.get(req.id, "saved variables")
        if req.kind == "stop":
            self._handle_stop(req)
            return True
        self.queue.append(req)
        head = req.prompt.strip().replace("\n", " ")[:200]
        self.log(f"queued {req.kind} {req.id} from {source} (session {req.session_key or '-'}, backend {req.backend or 'default'}, project {req.project or 'default'}): {head}")
        return True

    def submit_envelope(self, text: str) -> bool:
        req = parse_envelope(text)
        if not req:
            return False
        if not self.nonce or req.nonce != self.nonce:
            self.log(f"clipboard envelope {req.id} ignored: it does not carry this companion's nonce (press Refresh in the game once, then copy again)")
            return False
        return self.submit(req, source="clipboard")

    def _mark_consumed(self, rid: str) -> None:
        consumed = self.state.setdefault("consumed", [])
        if rid not in consumed:
            consumed.append(rid)
            del consumed[:-500]

    def _handle_stop(self, req: Request) -> None:
        self._mark_consumed(req.id)
        if self.current and (not req.session_key or self.current.session_key == req.session_key):
            self.log(f"stop requested for the running turn ({self.current.id})")
            self.stop_event.set()
        else:
            self.queue = deque(q for q in self.queue if q.session_key != req.session_key or not req.session_key)
            self.log(f"stop {req.id}: nothing running for session {req.session_key or '-'}")
        self.save_state()
        self.write_data_file()

    # ------------------------------------------------------------------ work

    def pending(self) -> int:
        return len(self.queue)

    def work_once(self) -> bool:
        """Run the oldest queued request. Returns True when something ran."""
        if not self.queue:
            return False
        req = self.queue.popleft()
        self.current = req
        self.stop_event.clear()
        # At most once: a request is consumed before it runs, so a crash or a
        # kill during the run never replays an agentic session on restart.
        self._mark_consumed(req.id)
        self.save_state()
        try:
            self._run(req)
        finally:
            self.current = None
            self.save_state()
            self.write_data_file()
        return True

    def _backend_for(self, name: str) -> tuple[str, Backend]:
        name = name or self.cfg.default_backend
        if name not in self._backends:
            self._backends[name] = make_backend(name, cfgmod.backend(self.cfg, name))
        return name, self._backends[name]

    def _session_for(self, req: Request, backend_name: str) -> tuple[str, dict]:
        sessions = self.sessions()
        key = req.session_key or f"s-{req.created_at or int(time.time())}"
        if req.kind == "new" or key not in sessions:
            sessions[key] = {
                "key": key,
                "title": _title_from(req.prompt) or key,
                "project": req.project or self.cfg.default_project,
                "backend": backend_name,
                "status": "idle",
                "cost_usd": 0.0,
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
                "entries": [],
                "state": {},
            }
        return key, sessions[key]

    def _run(self, req: Request) -> None:
        try:
            backend_name, backend = self._backend_for(req.backend)
        except (KeyError, ValueError) as exc:
            self.log(f"request {req.id}: {exc}")
            key, session = self._session_for(req, req.backend or "?")
            _append(session, "error", str(exc))
            session["status"] = "error"
            return
        key, session = self._session_for(req, backend_name)
        project_name = req.project or session.get("project") or self.cfg.default_project
        project_dir: Path | None = None
        if backend_name and cfgmod.backend(self.cfg, backend_name).type == "claude-code":
            try:
                project_dir = cfgmod.project_dir(self.cfg, project_name) if project_name else None
            except KeyError as exc:
                _append(session, "error", str(exc))
                session["status"] = "error"
                self.log(f"request {req.id}: {exc}")
                return
        session["project"] = project_name
        session["backend"] = backend_name
        if not req.prompt.strip():
            session["status"] = "idle"
            return
        _append(session, "user", req.prompt)
        session["status"] = "running"
        session["updated_at"] = int(time.time())
        self.save_state()
        self.write_data_file()
        self.log(f"running {req.id} on {backend.describe()} (session {key}, project {project_name or '-'})")

        stream: list[str] = []
        stream_ts = time.time()

        def on_event(ev: TurnEvent) -> None:
            nonlocal stream_ts
            if ev.kind == "text":
                stream.append(ev.text)
            elif ev.kind == "tool":
                _append(session, "tool", ev.text)
                self.log(f"  tool: {ev.text}")
            elif ev.kind in ("init", "status"):
                self.log(f"  {ev.text}")
            elif ev.kind == "error":
                self.log(f"  error: {ev.text}")
            if time.time() - self._last_flush >= STREAM_FLUSH_SEC:
                session["partial"] = "".join(stream)[-MAX_ENTRY_CHARS:]
                session["updated_at"] = int(time.time())
                self.write_data_file()

        result = backend.run_turn(req.prompt, session.setdefault("state", {}), project_dir, on_event, self.stop_event)
        session.pop("partial", None)
        if result.text.strip():
            _append(session, "assistant", result.text)
        if result.is_error:
            _append(session, "error", result.error or "error")
            session["status"] = "stopped" if (result.error or "").startswith("stopped") else "error"
        else:
            session["status"] = "done"
        if result.cost_usd:
            session["cost_usd"] = round(float(session.get("cost_usd") or 0.0) + float(result.cost_usd), 4)
        summary = _result_line(result)
        _append(session, "result", summary)
        session["updated_at"] = int(time.time())
        self.log(f"finished {req.id}: {summary}")
        if self.notify:
            try:
                self.notify("Claude Code", f"{session['title'][:60]}: {session['status']}. Press Refresh in the game window.")
            except Exception:  # noqa: BLE001 - notifications are best effort
                pass

    # ------------------------------------------------------------- data file

    def data_payload(self) -> dict:
        sessions = self.sessions()
        ordered = sorted(sessions.values(), key=lambda s: s.get("updated_at", 0), reverse=True)[:MAX_SESSIONS]
        exported = {}
        for s in ordered:
            exported[s["key"]] = {
                "key": s["key"],
                "title": s.get("title", s["key"]),
                "project": s.get("project", ""),
                "backend": s.get("backend", ""),
                "status": s.get("status", "idle"),
                "cost_usd": s.get("cost_usd", 0.0),
                "created_at": s.get("created_at", 0),
                "updated_at": s.get("updated_at", 0),
                "partial": s.get("partial", ""),
                "entries": [dict(e) for e in s.get("entries", [])],
            }
        payload = {
            "schema": DATA_SCHEMA,
            "generated_at": int(time.time()),
            "companion_version": __version__,
            "config": {
                "projects": sorted(self.cfg.projects),
                "default_project": self.cfg.default_project,
                "backends": sorted(self.cfg.backends),
                "default_backend": self.cfg.default_backend,
                "clipboard_watch": bool(self.cfg.clipboard_watch),
                "nonce": self.nonce,
                "running": self.current.id if self.current else "",
                "queued": self.pending(),
            },
            "consumed": list(self.state.get("consumed", []))[-500:],
            "sessions": exported,
        }
        _fit_budget(payload)
        return payload

    def write_data_file(self) -> Path:
        path = cfgmod.data_file(self.cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_lua_file(path, self.data_payload(), DATA_GLOBAL)
        self._last_flush = time.time()
        return path


# ------------------------------------------------------------------- helpers

def _append(session: dict, role: str, text: str) -> None:
    entries = session.setdefault("entries", [])
    entries.append({"role": role, "text": text[:MAX_ENTRY_CHARS], "ts": int(time.time())})


def _title_from(prompt: str) -> str:
    first = prompt.strip().splitlines()[0] if prompt.strip() else ""
    return first[:48] + ("..." if len(first) > 48 else "")


def _result_line(result) -> str:  # type: ignore[no-untyped-def]
    bits = []
    if result.duration_ms is not None:
        bits.append(f"{result.duration_ms / 1000:.1f}s")
    if result.num_turns:
        bits.append(f"{result.num_turns} turns")
    if result.cost_usd is not None:
        bits.append(f"${result.cost_usd:.4f}")
    usage = result.usage if isinstance(result.usage, dict) else {}
    tokens = usage.get("output_tokens") or usage.get("completion_tokens")
    if tokens:
        bits.append(f"{tokens} output tokens")
    if result.is_error:
        bits.append(f"error: {result.error}")
    return ", ".join(bits) or "done"


def _fit_budget(payload: dict) -> None:
    """Shrink the payload until its serialized size fits DATA_BUDGET_BYTES.

    Oldest transcript entries of the least recently updated sessions go first,
    then whole sessions beyond the newest five.
    """
    from wowclaude.luaio import to_lua

    def size() -> int:
        return len(to_lua(payload, DATA_GLOBAL).encode("utf-8"))

    sessions: dict = payload["sessions"]
    if size() <= DATA_BUDGET_BYTES:
        return
    by_age = sorted(sessions.values(), key=lambda s: s.get("updated_at", 0))
    for s in by_age:
        while len(s["entries"]) > 2 and size() > DATA_BUDGET_BYTES:
            del s["entries"][0]
        if size() <= DATA_BUDGET_BYTES:
            return
    while len(sessions) > 5 and size() > DATA_BUDGET_BYTES:
        oldest = min(sessions.values(), key=lambda s: s.get("updated_at", 0))
        del sessions[oldest["key"]]
