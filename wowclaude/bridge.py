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
   when the turn starts, every second or two while text streams, and at the
   end. Each write also puts a small copy of the newest sessions in every live
   slot (``publish_live``).
4. The addon loads the next unused live slot and merges what it finds, or,
   after a /reload, reads the full ``ClaudeCode_Data``. Either way it prunes
   consumed ids from its outbox.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from wowclaude import __version__
from wowclaude import config as cfgmod
from wowclaude.backends import Backend, TurnEvent, make_backend
from wowclaude.installer import LIVE_FILE, LIVE_GLOBAL, installed_live_slots
from wowclaude.luaio import LuaParseError, read_saved_variables, to_lua, write_lua_file

ENVELOPE_PREFIX = "CLAUDECODE-WOW:1:"
DATA_GLOBAL = "ClaudeCode_Data"
DATA_SCHEMA = 1
DATA_BUDGET_BYTES = 1_000_000
MAX_SESSIONS = 20
MAX_ENTRY_CHARS = 12_000
MAX_PROMPT_CHARS = 20_000
MAX_REQUESTS_PER_SCAN = 20
STREAM_FLUSH_SEC = 1.5
# The live channel: one master copy beside the data file (not in that addon's
# TOC, so the game never loads it from there), hard-linked into every slot.
LIVE_MASTER = "ClaudeCode_Live.lua"
LIVE_SESSIONS = 3
LIVE_SESSION_BYTES = 60_000
LIVE_CONSUMED = 40
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
        if not self.state_path.is_file():
            # Persist the fresh nonce at once so it is per install, not per run.
            self.save_state()
        self.queue: deque[Request] = deque()
        self.current: Request | None = None
        self.stop_event = threading.Event()
        self._backends: dict[str, Backend] = {}
        self._last_flush = 0.0
        self._sv_mtimes: dict[str, float] = {}
        self._sources: dict[str, str] = {}
        # write_data_file runs on the main loop and on the clipboard thread (stop requests).
        self._io_lock = threading.RLock()
        self._seq = 0
        self._live_slots: list[Path] | None = None
        self._live_stale: list[Path] = []
        self._live_copy_mode = False
        self._live_warned = False

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
        # A failed turn often carries its error as the answer text too; show it once.
        if result.text.strip() and not (result.is_error and result.text.strip() == (result.error or "").strip()):
            _append(session, "assistant", result.text)
        if result.is_error:
            _append(session, "error", result.error or "error")
            session["status"] = "stopped" if (result.error or "").startswith("stopped") else "error"
        else:
            session["status"] = "done"
        if result.cost_usd:
            session["cost_usd"] = round(float(session.get("cost_usd") or 0.0) + float(result.cost_usd), 4)
        _append(session, "result", _result_line(result, include_error=False))
        session["updated_at"] = int(time.time())
        self.log(f"finished {req.id}: {_result_line(result)}")
        if self.notify:
            try:
                self.notify("Claude Code", f"{session['title'][:60]}: {session['status']}")
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
                "live_slots": len(self._live_slot_list()),
            },
            "consumed": list(self.state.get("consumed", []))[-500:],
            "sessions": exported,
        }
        _fit_budget(payload)
        return payload

    def write_data_file(self) -> Path:
        """Write the full data file, then the live copy; both carry the same ``seq``."""
        with self._io_lock:
            # Milliseconds, and never repeated or going back, so the game can
            # tell which of two snapshots is newer.
            self._seq = max(self._seq + 1, int(time.time() * 1000))
            path = cfgmod.data_file(self.cfg)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = self.data_payload()
            payload["seq"] = self._seq
            write_lua_file(path, payload, DATA_GLOBAL)
            self.publish_live(payload)
            self._last_flush = time.time()
            return path

    # ------------------------------------------------------------- live slots

    def _live_slot_list(self) -> list[Path]:
        """The slot folders, read from disk once and cached (a listdir per write is waste)."""
        if self._live_slots is None:
            self._live_slots = installed_live_slots(self.cfg)
        return self._live_slots

    def refresh_live_slots(self) -> bool:
        """Re-read the slot folders. True when they changed, for example because
        ``install`` ran during this run; the caller then writes the data file."""
        with self._io_lock:
            before = self._live_slot_list()
            self._live_slots = installed_live_slots(self.cfg)
            changed = self._live_slots != before
            if changed:
                self.log(f"live slots: now {len(self._live_slots)} folder(s) (was {len(before)})")
            return changed

    def live_payload(self, full: dict) -> dict:
        """A small snapshot for the slot files: the newest sessions, each trimmed
        from the front to LIVE_SESSION_BYTES, because the game parses the whole
        file in one go at load time."""
        sessions = full.get("sessions", {})
        newest = sorted(sessions.values(), key=lambda s: s.get("updated_at", 0), reverse=True)[:LIVE_SESSIONS]
        return {
            "schema": full.get("schema", DATA_SCHEMA),
            "seq": full.get("seq", 0),
            "generated_at": full.get("generated_at", 0),
            "companion_version": full.get("companion_version", __version__),
            "config": full.get("config", {}),
            "consumed": list(full.get("consumed", []))[-LIVE_CONSUMED:],
            "sessions": {s["key"]: _trim_session(s) for s in newest},
        }

    def publish_live(self, full: dict) -> int:
        """Put the live snapshot in every slot's Live.lua. Returns the slots updated.

        Never raises: a slot the game holds open is skipped, remembered, and
        brought up to date by ``retry_live`` or the next write. The master is
        hard-linked to a temp name in each slot and renamed over Live.lua,
        because the game only reads file names it already knows.
        """
        slots = self._live_slot_list()
        if not slots:
            return 0
        with self._io_lock:
            master = self._live_master()
            try:
                write_lua_file(master, self.live_payload(full), LIVE_GLOBAL)
            except (OSError, ValueError, TypeError) as exc:
                self._warn_live(f"live copy not written ({exc}); answers will be read with a /reload")
                return 0
            updated = self._link_slots(master, slots)
            if not updated:
                self._warn_live(f"live copy: none of the {len(slots)} slot folders could be updated (is the game holding the files? did install change them?)")
            return updated

    def retry_live(self) -> int:
        """Update the slots the last write had to skip. Returns how many caught up."""
        with self._io_lock:
            if not self._live_stale:
                return 0
            master = self._live_master()
            if not master.is_file():
                return 0
            return self._link_slots(master, list(self._live_stale))

    def _live_master(self) -> Path:
        return cfgmod.data_file(self.cfg).with_name(LIVE_MASTER)

    def _link_slots(self, master: Path, slots: list[Path]) -> int:
        """Put ``master`` in each slot as Live.lua; the ones that fail become ``_live_stale``."""
        updated = 0
        stale: list[Path] = []
        for slot in slots:
            temp = slot / (LIVE_FILE + ".new")
            try:
                temp.unlink(missing_ok=True)
                if self._live_copy_mode:
                    shutil.copyfile(master, temp)
                else:
                    try:
                        os.link(master, temp)
                    except OSError:
                        # No hard links on this volume: copy instead. If the copy
                        # fails too, the slot is the problem and it is skipped.
                        shutil.copyfile(master, temp)
                        self._live_copy_mode = True
                        self.log("live slots: this file system has no hard links; copying the live file into each slot instead")
                os.replace(temp, slot / LIVE_FILE)
                updated += 1
            except OSError:
                stale.append(slot)
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
        self._live_stale = stale
        return updated

    def _warn_live(self, msg: str) -> None:
        """Log a live-channel problem once per run; it would repeat on every write."""
        if not self._live_warned:
            self._live_warned = True
            self.log(msg)


# ------------------------------------------------------------------- helpers

def _append(session: dict, role: str, text: str) -> None:
    entries = session.setdefault("entries", [])
    entries.append({"role": role, "text": text[:MAX_ENTRY_CHARS], "ts": int(time.time())})


def _trim_session(session: dict) -> dict:
    """A copy of ``session`` whose oldest entries are dropped until it serializes
    to at most LIVE_SESSION_BYTES (always keeping the last two entries).
    ``first`` is how many leading entries were left out, ``total`` the full count."""
    entries = list(session.get("entries", []))
    total = len(entries)

    def build(skip: int) -> dict:
        out = {k: v for k, v in session.items() if k != "entries"}
        out["entries"] = entries[skip:]
        out["first"] = skip
        out["total"] = total
        return out

    def fits(skip: int) -> bool:
        return len(to_lua(build(skip), LIVE_GLOBAL).encode("utf-8")) <= LIVE_SESSION_BYTES

    lo, hi = 0, max(0, total - 2)
    if fits(0):
        return build(0)
    # Size only shrinks as more leading entries go, so bisect for the smallest skip that fits.
    while lo < hi:
        mid = (lo + hi) // 2
        if fits(mid):
            hi = mid
        else:
            lo = mid + 1
    return build(lo)


def _title_from(prompt: str) -> str:
    first = prompt.strip().splitlines()[0] if prompt.strip() else ""
    return first[:48] + ("..." if len(first) > 48 else "")


def _result_line(result, include_error: bool = True) -> str:  # type: ignore[no-untyped-def]
    """Timing and usage of a finished turn. The transcript shows the error in
    its own entry, so its result line leaves the error text out."""
    bits = []
    if result.duration_ms is not None:
        bits.append(f"{result.duration_ms / 1000:.1f}s")
    if result.num_turns:
        bits.append(f"{result.num_turns} turn{'' if result.num_turns == 1 else 's'}")
    if result.cost_usd:
        bits.append(f"${result.cost_usd:.4f}")
    usage = result.usage if isinstance(result.usage, dict) else {}
    tokens = usage.get("output_tokens") or usage.get("completion_tokens")
    if tokens:
        bits.append(f"{tokens} output tokens")
    if result.is_error:
        bits.append(f"error: {result.error}" if include_error else "failed")
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
