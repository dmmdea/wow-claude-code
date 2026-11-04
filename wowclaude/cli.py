"""Command-line entry point: init, install, run, doctor, selfcheck, config."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path

from wowclaude import __version__
from wowclaude import config as cfgmod
from wowclaude.backends import claude_auth_summary, claude_version, resolve_claude_bin
from wowclaude.bridge import ENVELOPE_PREFIX, Bridge
from wowclaude.clipboard import ClipboardWatcher
from wowclaude.installer import addon_source_dir, install_addons, installed_status, installed_version, uninstall_addons
from wowclaude.luaio import SAMPLE_SAVED_VARIABLES, parse_saved_variables, read_saved_variables, to_lua, write_lua_file
from wowclaude.notify import notify, notify_available
from wowclaude.presets import PRESETS


def _say(msg: str) -> None:
    print(msg, flush=True)


def _stamp(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return answer or default


# ------------------------------------------------------------------------ init

def cmd_init(args: argparse.Namespace) -> int:
    cfg = cfgmod.load()
    interactive = _interactive() and not args.non_interactive

    wow_dir = args.wow_dir or cfg.wow_dir or cfgmod.detect_wow_dir() or ""
    if interactive and not args.wow_dir:
        wow_dir = _ask("World of Warcraft install folder (the one holding _forever_, _retail_, ...)", wow_dir)
    if not wow_dir:
        _say("No install folder given or detected. Re-run with --wow-dir <path>.")
        return 1
    cfg.wow_dir = str(Path(wow_dir).expanduser())

    flavors = cfgmod.installed_flavors(cfg.wow_dir)
    flavor = args.flavor or ("_forever_" if "_forever_" in flavors else (flavors[0] if flavors else cfg.flavor))
    if interactive and not args.flavor:
        flavor = _ask(f"Game flavor folder (installed: {', '.join(flavors) or 'none found'})", flavor)
    cfg.flavor = cfgmod.normalize_flavor(flavor)

    for spec in args.project or []:
        name, _, path = spec.partition("=")
        if not name or not path:
            _say(f"--project needs NAME=PATH, got {spec!r}")
            return 1
        cfg.projects[name] = str(Path(path).expanduser())
        cfg.default_project = cfg.default_project or name
    if interactive and not cfg.projects:
        name = _ask("Name for your first project folder (for Claude Code sessions)", "default")
        path = _ask("Path of that project folder", os.getcwd())
        cfg.projects[name] = str(Path(path).expanduser())
        cfg.default_project = name

    for preset in args.backend or []:
        if preset not in PRESETS:
            _say(f"unknown backend preset {preset!r}; available: {', '.join(PRESETS)}")
            return 1
        cfg.backends[preset] = cfgmod._backend_from_dict(PRESETS[preset])  # noqa: SLF001 - same package

    problems = cfg.validate()
    path = cfgmod.save(cfg)
    _say(f"Config written to {path}")
    for p in problems:
        _say(f"  note: {p}")
    _say("Next: `python -m wowclaude install`, then `python -m wowclaude run` while you play.")
    return 0


# --------------------------------------------------------------------- install

def cmd_install(args: argparse.Namespace) -> int:
    cfg = cfgmod.load()
    if not cfg.wow_dir:
        _say("No config yet. Run `python -m wowclaude init` first.")
        return 1
    try:
        written = install_addons(cfg)
    except (FileNotFoundError, ValueError) as exc:
        _say(f"install failed: {exc}")
        return 1
    for p in written:
        _say(f"installed {p}")
    _say("If the game is running, restart it once: the client lists new addons only at launch (later updates need just /reload).")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    cfg = cfgmod.load()
    try:
        removed = uninstall_addons(cfg)
    except (FileNotFoundError, ValueError) as exc:
        _say(f"uninstall failed: {exc}")
        return 1
    for p in removed:
        _say(f"removed {p}")
    if not removed:
        _say("nothing to remove")
    return 0


# ------------------------------------------------------------------------- run

def cmd_run(args: argparse.Namespace) -> int:
    cfg = cfgmod.load()
    problems = cfg.validate()
    if problems:
        for p in problems:
            _say(f"config problem: {p}")
        return 1
    status = installed_status(cfg)
    if not all(status.values()):
        _say("the addon folders are not installed in this flavor; run `python -m wowclaude install` first")
        return 1

    bridge = Bridge(cfg, log=_stamp, notify=notify if cfg.toast and notify_available() else None)
    use_clipboard = cfg.clipboard_watch if args.clipboard is None else args.clipboard
    _say(f"wowclaude {__version__} companion")
    _say(f"  watching : {cfgmod.flavor_dir(cfg) / 'WTF' / 'Account' / '*' / 'SavedVariables' / cfgmod.SAVED_VARIABLES_NAME}")
    _say(f"  writing  : {cfgmod.data_file(cfg)}")
    _say(f"  backends : {', '.join(f'{n} ({b.type})' for n, b in cfg.backends.items())}; default {cfg.default_backend}")
    _say(f"  projects : {', '.join(cfg.projects) or '(none)'}; default {cfg.default_project or '(none)'}")
    _say(f"  clipboard: {'on (only text starting with ' + ENVELOPE_PREFIX + ' is read)' if use_clipboard else 'off (enable with: config set clipboard_watch true)'}")
    _say("  send a request from the in-game window; press Refresh (or /reload) there to read the answer")

    watcher = ClipboardWatcher(ENVELOPE_PREFIX, bridge.submit_envelope) if use_clipboard else None
    if watcher:
        watcher.start()
    bridge.write_data_file()
    try:
        for req in bridge.scan(force=True):
            bridge.submit(req)
        while True:
            if bridge.work_once():
                continue
            for req in bridge.scan():
                bridge.submit(req)
            if bridge.pending():
                continue
            if args.once:
                break
            time.sleep(max(0.2, cfg.poll_interval_sec))
    except KeyboardInterrupt:
        _say("stopping")
    finally:
        if watcher:
            watcher.stop()
        bridge.write_data_file()
    return 0


# ---------------------------------------------------------------------- doctor

def cmd_doctor(args: argparse.Namespace) -> int:
    hard = 0
    cfg = cfgmod.load()
    _say(f"wowclaude {__version__} on Python {platform.python_version()} ({platform.system()} {platform.release()})")
    _say(f"config: {cfgmod.config_path()} ({'exists' if cfgmod.config_path().is_file() else 'missing: run init'})")
    _say(f"wow_dir: {cfg.wow_dir or '(unset)'}  flavor: {cfg.flavor}")
    flavor = cfgmod.flavor_dir(cfg)
    if cfg.wow_dir and flavor.is_dir():
        _say(f"  flavor folder: ok ({flavor})")
    else:
        _say("  flavor folder: MISSING")
        hard += 1
    status = installed_status(cfg)
    for name, ok in status.items():
        _say(f"  addon {name}: {'installed' if ok else 'NOT installed'}" + (f" (version {installed_version(cfg)})" if ok and name == cfgmod.UI_ADDON else ""))
    sv = cfgmod.saved_variables_files(cfg)
    _say(f"  SavedVariables files: {len(sv)}" + ("" if sv else " (none yet: appears after the first /reload with the addon enabled)"))
    for p in sv:
        try:
            n = len(parse_saved_variables(p.read_text(encoding='utf-8', errors='replace')).get("ClaudeCodeDB", {}).get("outbox", []) or [])
            _say(f"    {p.parent.parent.name}: parsed ok, {n} request(s) in the outbox")
        except Exception as exc:  # noqa: BLE001
            _say(f"    {p}: parse error: {exc}")
    data = cfgmod.data_file(cfg)
    _say(f"  data file: {data} ({'present' if data.is_file() else 'absent'})")
    _say("backends:")
    for name, bc in cfg.backends.items():
        if bc.type == "claude-code":
            exe = resolve_claude_bin(bc)
            ver = claude_version(bc) if exe else None
            _say(f"  {name}: claude-code, binary {exe or 'NOT FOUND'}{', ' + ver if ver else ''}")
            _say(f"    auth: {claude_auth_summary(bc)}; permission_mode {bc.permission_mode}")
            if not exe:
                hard += 1
        else:
            key = "key present" if cfgmod.resolve_api_key(bc) else "no key"
            _say(f"  {name}: {bc.type}, model {bc.model}, {bc.base_url or 'default endpoint'}, {key}")
    _say(f"projects: {', '.join(f'{n} -> {p}' for n, p in cfg.projects.items()) or '(none)'}")
    for n, p in cfg.projects.items():
        if not Path(p).expanduser().is_dir():
            _say(f"  project {n}: folder missing: {p}")
    _say(f"notifications: {'available' if notify_available() else 'unavailable on this platform'}; clipboard watch: {'on' if cfg.clipboard_watch else 'off'}")
    for p in cfg.validate():
        _say(f"config problem: {p}")
        hard += 1
    _say("doctor: " + ("ok" if hard == 0 else f"{hard} problem(s)"))
    return 0 if hard == 0 else 1


# ------------------------------------------------------------------- selfcheck

def cmd_selfcheck(args: argparse.Namespace) -> int:
    parsed = parse_saved_variables(SAMPLE_SAVED_VARIABLES)
    again = parse_saved_variables(to_lua(parsed["ClaudeCodeDB"], "ClaudeCodeDB"))["ClaudeCodeDB"]
    if again != parsed["ClaudeCodeDB"]:
        _say("selfcheck FAILED: SavedVariables round-trip mismatch")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        wow = Path(tmp) / "WoW"
        (wow / "_forever_" / "Interface" / "AddOns").mkdir(parents=True)
        cfg = cfgmod.Config(wow_dir=str(wow), flavor="_forever_", projects={"demo": tmp}, default_project="demo")
        os.environ["WOWCLAUDE_HOME"] = str(Path(tmp) / "home")
        bridge = Bridge(cfg)
        path = bridge.write_data_file()
        back = read_saved_variables(path)
        if back.get("ClaudeCode_Data", {}).get("schema") != 1:
            _say("selfcheck FAILED: data file did not read back")
            return 1
        write_lua_file(path, {"ok": True}, "X")
    src = addon_source_dir()
    lines = {}
    for name in ("ClaudeCode", "ClaudeCode_Data"):
        toc = (src / name / f"{name}.toc").read_text(encoding="utf-8").splitlines()
        lines[name] = next((l for l in toc if l.startswith("## Interface:")), "")
    if lines["ClaudeCode"] != lines["ClaudeCode_Data"] or not lines["ClaudeCode"]:
        _say(f"selfcheck FAILED: TOC Interface lines differ: {lines}")
        return 1
    _say(f"selfcheck ok ({lines['ClaudeCode']})")
    return 0


# ---------------------------------------------------------------------- config

def cmd_config(args: argparse.Namespace) -> int:
    cfg = cfgmod.load()
    if args.config_command == "path":
        _say(str(cfgmod.config_path()))
        return 0
    if args.config_command == "show":
        _say(json.dumps(cfgmod.redacted(cfg), indent=2))
        return 0
    if args.config_command == "get":
        try:
            _say(json.dumps(cfgmod.get_value(cfg, args.key)))
        except KeyError:
            _say(f"unknown key {args.key}")
            return 1
        return 0
    if args.config_command == "set":
        cfg = cfgmod.set_value(cfg, args.key, args.value)
        cfgmod.save(cfg)
        for p in cfg.validate():
            _say(f"note: {p}")
        _say(f"{args.key} set")
        return 0
    if args.config_command == "add-backend":
        if args.preset not in PRESETS:
            _say(f"unknown preset {args.preset!r}; available: {', '.join(PRESETS)}")
            return 1
        cfg.backends[args.name] = cfgmod._backend_from_dict(PRESETS[args.preset])  # noqa: SLF001
        cfgmod.save(cfg)
        _say(f"backend {args.name} added from preset {args.preset}; set its model with: config set backends.{args.name}.model <id>")
        return 0
    return 1


# ---------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wowclaude", description="Companion for the ClaudeCode World of Warcraft addon.")
    parser.add_argument("--version", action="version", version=f"wowclaude {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("init", help="write the config (install folder, flavor, projects, backends)")
    p.add_argument("--wow-dir", help="World of Warcraft install folder (the one holding _forever_, _retail_, ...)")
    p.add_argument("--flavor", help="game flavor folder or alias: forever, retail, classic_era, ...")
    p.add_argument("--project", action="append", metavar="NAME=PATH", help="a project folder for Claude Code sessions (repeatable)")
    p.add_argument("--backend", action="append", metavar="PRESET", help=f"add a backend preset: {', '.join(PRESETS)}")
    p.add_argument("--non-interactive", action="store_true", help="never prompt; fail when something required is missing")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("install", help="copy the two addon folders into the game's Interface/AddOns")
    p.set_defaults(func=cmd_install)
    p = sub.add_parser("uninstall", help="remove the two addon folders")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("run", help="watch the game's SavedVariables and answer requests")
    p.add_argument("--once", action="store_true", help="process pending requests and exit")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--clipboard", dest="clipboard", action="store_true", default=None, help="also accept clipboard envelopes this run")
    group.add_argument("--no-clipboard", dest="clipboard", action="store_false", help="ignore the clipboard this run")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("doctor", help="check the install: paths, addon folders, CLI, auth, config")
    p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("selfcheck", help="exercise the file formats without the game (used by CI)")
    p.set_defaults(func=cmd_selfcheck)

    p = sub.add_parser("config", help="read or change the config")
    cs = p.add_subparsers(dest="config_command")
    cs.add_parser("path")
    cs.add_parser("show")
    g = cs.add_parser("get")
    g.add_argument("key")
    s = cs.add_parser("set")
    s.add_argument("key")
    s.add_argument("value")
    a = cs.add_parser("add-backend")
    a.add_argument("name")
    a.add_argument("preset")
    p.set_defaults(func=cmd_config)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    if args.command == "config" and not getattr(args, "config_command", None):
        parser.parse_args(["config", "--help"])
        return 0
    return int(args.func(args))
