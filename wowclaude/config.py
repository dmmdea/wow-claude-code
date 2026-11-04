"""Companion configuration: where the game is, which projects and backends exist.

The config file lives in the companion home (``WOWCLAUDE_HOME`` or ``~/.wowclaude``)
as ``config.json``. It never holds credentials. Backends name an environment
variable or a file that holds their key; the key is read at run time, handed to the
backend, and never logged or written anywhere.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

CONFIG_FILE = "config.json"
DATA_ADDON = "ClaudeCode_Data"
UI_ADDON = "ClaudeCode"
SAVED_VARIABLES_NAME = "ClaudeCode.lua"

# Friendly flavor names -> install subfolder (the folders beside the launcher).
FLAVORS: dict[str, str] = {
    "forever": "_forever_",
    "retail": "_retail_",
    "classic_era": "_classic_era_",
    "classic-era": "_classic_era_",
    "classic": "_classic_",
    "ptr": "_ptr_",
    "beta": "_beta_",
    "xptr": "_xptr_",
}

BACKEND_TYPES = ("claude-code", "openai", "anthropic")


@dataclass
class BackendConfig:
    """One way to answer a request.

    ``type`` is ``claude-code`` (the installed Claude Code CLI: a real agentic
    session inside a project folder), ``openai`` (any OpenAI-compatible chat
    completions endpoint: a hosted router, a local llama.cpp / vLLM / Ollama /
    LM Studio server, ...) or ``anthropic`` (the Anthropic Messages API directly).
    """

    type: str = "claude-code"
    model: str | None = None
    api_key_env: str | None = None
    api_key_file: str | None = None
    system_prompt: str | None = None
    # claude-code
    claude_bin: str = "claude"
    permission_mode: str = "acceptEdits"
    allowed_tools: list[str] = field(default_factory=list)
    max_budget_usd: float | None = 5.0
    extra_args: list[str] = field(default_factory=list)
    # openai / anthropic
    base_url: str | None = None
    max_history_messages: int = 40
    max_tokens: int = 4096
    temperature: float | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    timeout_sec: float = 600.0

    def validate(self, name: str) -> None:
        if self.type not in BACKEND_TYPES:
            raise ValueError(f"backend {name!r}: unknown type {self.type!r}; expected one of {BACKEND_TYPES}")
        if self.api_key_env and not _ENV_NAME.match(self.api_key_env):
            # Never echo the value: it is probably the key itself, pasted by mistake.
            raise ValueError(f"backend {name!r}: api_key_env must be the NAME of an environment variable, not the key; the value was not kept")
        if self.type == "openai" and not self.base_url:
            raise ValueError(f"backend {name!r}: type openai needs base_url (the server's /v1 root)")
        if self.type in ("openai", "anthropic") and not self.model:
            raise ValueError(f"backend {name!r}: type {self.type} needs a model id")


def default_backends() -> dict[str, BackendConfig]:
    return {
        "claude-code": BackendConfig(type="claude-code"),
        "anthropic": BackendConfig(type="anthropic", model="claude-sonnet-5-5", api_key_env="ANTHROPIC_API_KEY"),
    }


@dataclass
class Config:
    wow_dir: str = ""
    flavor: str = "_forever_"
    projects: dict[str, str] = field(default_factory=dict)
    default_project: str = ""
    backends: dict[str, BackendConfig] = field(default_factory=default_backends)
    default_backend: str = "claude-code"
    clipboard_watch: bool = False
    toast: bool = True
    poll_interval_sec: float = 1.0

    def validate(self) -> list[str]:
        """Return human-readable problems (empty when the config is usable)."""
        problems: list[str] = []
        if not self.wow_dir:
            problems.append("wow_dir is empty (run: wowclaude init --wow-dir <path>)")
        if not self.backends:
            problems.append("no backends configured")
        if self.default_backend not in self.backends:
            problems.append(f"default_backend {self.default_backend!r} is not a configured backend")
        for name, backend_cfg in self.backends.items():
            try:
                backend_cfg.validate(name)
            except ValueError as exc:
                problems.append(str(exc))
        if self.default_project and self.default_project not in self.projects:
            problems.append(f"default_project {self.default_project!r} is not a configured project")
        return problems


# --------------------------------------------------------------------------- paths

def home() -> Path:
    """The companion's own folder: ``WOWCLAUDE_HOME`` or ``~/.wowclaude``."""
    env = os.environ.get("WOWCLAUDE_HOME")
    return Path(env).expanduser() if env else Path.home() / ".wowclaude"


def config_path() -> Path:
    return home() / CONFIG_FILE


def normalize_flavor(value: str) -> str:
    v = value.strip()
    if v in FLAVORS:
        return FLAVORS[v]
    if v.startswith("_") and v.endswith("_"):
        return v
    return f"_{v.strip('_')}_"


def flavor_dir(cfg: Config) -> Path:
    return Path(cfg.wow_dir) / cfg.flavor


def addons_dir(cfg: Config) -> Path:
    return flavor_dir(cfg) / "Interface" / "AddOns"


def data_file(cfg: Config) -> Path:
    return addons_dir(cfg) / DATA_ADDON / f"{DATA_ADDON}.lua"


def saved_variables_files(cfg: Config) -> list[Path]:
    """Every account's ClaudeCode SavedVariables file that exists right now."""
    root = flavor_dir(cfg) / "WTF" / "Account"
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob(f"*/SavedVariables/{SAVED_VARIABLES_NAME}") if p.is_file())


def project_dir(cfg: Config, name: str) -> Path:
    if name not in cfg.projects:
        raise KeyError(f"unknown project {name!r}; configured: {', '.join(cfg.projects) or '(none)'}")
    return Path(cfg.projects[name]).expanduser()


def backend(cfg: Config, name: str) -> BackendConfig:
    if name not in cfg.backends:
        raise KeyError(f"unknown backend {name!r}; configured: {', '.join(cfg.backends) or '(none)'}")
    return cfg.backends[name]


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_HEADER = re.compile(r"(?i)auth|key|token|secret|cookie")


def _clean_key(value: str) -> str | None:
    """One token on the first non-empty line; anything else is not a key."""
    for line in value.splitlines():
        line = line.strip()
        if line:
            return line if not any(ch.isspace() or ord(ch) < 32 for ch in line) else None
    return None


def resolve_api_key(bc: BackendConfig) -> str | None:
    """The backend's key from its env var or file, or None. Never log the result."""
    if bc.api_key_env and _ENV_NAME.match(bc.api_key_env):
        value = _clean_key(os.environ.get(bc.api_key_env, ""))
        if value:
            return value
    if bc.api_key_file:
        path = Path(bc.api_key_file).expanduser()
        if path.is_file():
            value = _clean_key(path.read_text(encoding="utf-8"))
            if value:
                return value
    return None


# ----------------------------------------------------------------------- load/save

def _backend_from_dict(raw: dict) -> BackendConfig:
    known = {f.name for f in fields(BackendConfig)}
    return BackendConfig(**{k: v for k, v in raw.items() if k in known})


def from_dict(raw: dict) -> Config:
    known = {f.name for f in fields(Config)}
    data = {k: v for k, v in raw.items() if k in known}
    backends_raw = data.pop("backends", None)
    cfg = Config(**data)
    if isinstance(backends_raw, dict):
        cfg.backends = {name: _backend_from_dict(b) for name, b in backends_raw.items() if isinstance(b, dict)}
    cfg.flavor = normalize_flavor(cfg.flavor)
    return cfg


def to_dict(cfg: Config) -> dict:
    return asdict(cfg)


def load(path: Path | None = None) -> Config:
    """Load the config, or defaults when the file does not exist yet."""
    p = path or config_path()
    if not p.is_file():
        return Config()
    with p.open(encoding="utf-8") as fh:
        return from_dict(json.load(fh))


def save(cfg: Config, path: Path | None = None) -> Path:
    p = path or config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(to_dict(cfg), fh, indent=2)
        fh.write("\n")
    os.replace(tmp, p)
    return p


def redacted(cfg: Config) -> dict:
    """A copy safe to print: key sources are named, key material never appears."""
    out = to_dict(cfg)
    for b in out.get("backends", {}).values():
        headers = b.get("extra_headers") or {}
        b["extra_headers"] = {k: ("***" if _SECRET_HEADER.search(k) else v) for k, v in headers.items()}
        if b.get("api_key_env") and not _ENV_NAME.match(b["api_key_env"]):
            b["api_key_env"] = "<invalid: looks like a key, not a variable name>"
        if b.get("api_key_env"):
            b["api_key"] = "from env " + b["api_key_env"]
        elif b.get("api_key_file"):
            b["api_key"] = "from file " + b["api_key_file"]
        elif b.get("type") == "claude-code":
            b["api_key"] = "none (uses the Claude Code login)"
        else:
            b["api_key"] = "none"
    return out


def set_value(cfg: Config, dotted_key: str, raw_value: str) -> Config:
    """Set a config value by dotted key, parsing JSON values when they parse.

    Examples: ``clipboard_watch true``, ``projects.site C:/dev/site``,
    ``backends.anthropic.model <model id>``, ``default_backend anthropic``.
    """
    try:
        value: object = json.loads(raw_value)
    except json.JSONDecodeError:
        value = raw_value
    if dotted_key.endswith(".api_key_env") and isinstance(value, str) and value and not _ENV_NAME.match(value):
        raise ValueError("api_key_env takes the NAME of an environment variable (for example ANTHROPIC_API_KEY), never the key itself; nothing was saved")
    data = to_dict(cfg)
    parts = dotted_key.split(".")
    node: dict = data
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value
    return from_dict(data)


def get_value(cfg: Config, dotted_key: str) -> object:
    node: object = to_dict(cfg)
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(dotted_key)
        node = node[part]
    return node


# ------------------------------------------------------------------- detection

def detect_wow_dir() -> str | None:
    """Best-effort guess of the World of Warcraft install folder (the one holding
    ``_forever_`` / ``_retail_``). Returns None when nothing is found."""
    candidates: list[Path] = []
    if sys.platform == "win32":
        try:
            import winreg  # type: ignore[import-not-found]

            for root, key in (
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Blizzard Entertainment\World of Warcraft"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Blizzard Entertainment\World of Warcraft"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Blizzard Entertainment\World of Warcraft"),
            ):
                try:
                    with winreg.OpenKey(root, key) as k:
                        value, _ = winreg.QueryValueEx(k, "InstallPath")
                        candidates.append(Path(str(value)))
                except OSError:
                    continue
        except ImportError:
            pass
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            base = os.environ.get(env)
            if base:
                candidates.append(Path(base) / "World of Warcraft")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/World of Warcraft"))
    candidates.extend(_battle_net_install_paths())
    for c in candidates:
        # The registry value may point at a flavor folder; climb to the parent that
        # holds the flavor folders.
        for probe in (c, c.parent):
            if any((probe / f).is_dir() for f in set(FLAVORS.values())):
                return str(probe)
    return None


def _battle_net_install_paths() -> list[Path]:
    """Install folders recorded by the Battle.net agent (its product database is
    a binary file, but the paths in it are plain text)."""
    import re

    dbs: list[Path] = []
    if sys.platform == "win32":
        program_data = os.environ.get("ProgramData", r"C:\ProgramData")
        dbs.append(Path(program_data) / "Battle.net" / "Agent" / "product.db")
    elif sys.platform == "darwin":
        dbs.append(Path("/Users", "Shared", "Battle.net", "Agent", "product.db"))
    found: list[Path] = []
    for db in dbs:
        try:
            blob = db.read_bytes()
        except OSError:
            continue
        for m in re.finditer(rb"(?:[A-Za-z]:[\\/]|/)[\x20-\x7e]{3,240}", blob):
            text = m.group().decode("ascii", errors="ignore")
            if "World of Warcraft" in text:
                head = text.split("World of Warcraft")[0] + "World of Warcraft"
                found.append(Path(head))
    return found


def installed_flavors(wow_dir: str) -> list[str]:
    base = Path(wow_dir)
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and p.name.startswith("_") and p.name.endswith("_"))
