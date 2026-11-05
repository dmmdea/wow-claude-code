"""Install and remove the addon folders.

The only places this module ever writes are, under ``<flavor>/Interface/AddOns``:
``ClaudeCode``, ``ClaudeCode_Data`` and the live slot folders ``ClaudeCode_Live001``,
``ClaudeCode_Live002``, ... Any other destination is refused, and a folder is only
removed when this module created it.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from wowclaude import config as cfgmod

ADDON_FOLDERS = (cfgmod.UI_ADDON, cfgmod.DATA_ADDON)

# Live slots: load-on-demand addons that carry answers into a running game.
# The client reads a load-on-demand addon's file when the addon is loaded, and
# an addon loads once per UI session, so there is a pool of them. The client
# lists addon folders when it starts and on a /reload, so the folders have to
# exist before one of those; the companion only ever rewrites the one file
# inside each.
LIVE_PREFIX = "ClaudeCode_Live"
LIVE_FILE = "Live.lua"
LIVE_GLOBAL = "ClaudeCode_Live"
LIVE_MARKER = "## X-ClaudeCode-Live: 1"
MAX_LIVE_SLOTS = 400
_LIVE_NAME = re.compile(r"^ClaudeCode_Live\d{3}$")


def addon_source_dir() -> Path:
    """Where the addon folders live: inside the package (wheel) or beside it (checkout)."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "_addon", here.parent / "addon"):
        if all((candidate / name / f"{name}.toc").is_file() for name in ADDON_FOLDERS):
            return candidate
    raise FileNotFoundError("addon sources not found next to the wowclaude package; download the full repository")


def _checked_addons_dir(cfg: cfgmod.Config) -> Path:
    target = cfgmod.addons_dir(cfg)
    if tuple(p.lower() for p in target.parts[-2:]) != ("interface", "addons"):
        raise ValueError(f"refusing to install anywhere but an Interface/AddOns folder: {target}")
    flavor = cfgmod.flavor_dir(cfg)
    if not flavor.is_dir():
        raise FileNotFoundError(f"game flavor folder not found: {flavor} (check wow_dir and flavor in the config)")
    return target


def install_addons(cfg: cfgmod.Config) -> list[Path]:
    """Copy both addon folders into the configured flavor's AddOns folder.

    Idempotent. An existing ``ClaudeCode_Data.lua`` is kept, so reinstalling does
    not erase companion data the game has not read yet.
    """
    source = addon_source_dir()
    target = _checked_addons_dir(cfg)
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in ADDON_FOLDERS:
        src = source / name
        dst = target / name
        keep = dst / f"{cfgmod.DATA_ADDON}.lua" if name == cfgmod.DATA_ADDON else None
        preserved = keep.read_bytes() if keep and keep.is_file() else None
        shutil.copytree(src, dst, dirs_exist_ok=True)
        if keep and preserved is not None:
            keep.write_bytes(preserved)
        written.append(dst)
    return written


# ------------------------------------------------------------------ live slots

def live_slot_name(index: int) -> str:
    return f"{LIVE_PREFIX}{index:03d}"


def _interface_line(source: Path) -> str:
    """The ``## Interface:`` line of the window addon, so every slot matches it."""
    toc = source / cfgmod.UI_ADDON / f"{cfgmod.UI_ADDON}.toc"
    for line in toc.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Interface:"):
            return line.strip()
    raise ValueError(f"no ## Interface line in {toc}")


def live_slot_toc(index: int, interface_line: str) -> str:
    return "\n".join([
        interface_line,
        f"## Title: Claude Code live slot {index:03d}",
        "## Notes: Carries the companion's answers into the game without a reload. Claude Code loads it on demand; leave it enabled.",
        "## Author: dmmdea",
        "## LoadOnDemand: 1",
        f"## Dependencies: {cfgmod.UI_ADDON}",
        f"## Group: {cfgmod.UI_ADDON}",
        "## IconTexture: Interface\\Icons\\INV_Misc_Note_01",
        LIVE_MARKER,
        "",
        LIVE_FILE,
        "",
    ])


def _is_live_slot(folder: Path) -> bool:
    """True for a folder this module created: the name pattern and our TOC marker."""
    if not folder.is_dir() or not _LIVE_NAME.match(folder.name):
        return False
    try:
        return LIVE_MARKER in (folder / f"{folder.name}.toc").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def installed_live_slots(cfg: cfgmod.Config) -> list[Path]:
    """The live slot folders present in the game's AddOns folder, in order."""
    target = cfgmod.addons_dir(cfg)
    if not target.is_dir():
        return []
    return sorted(p for p in target.glob(f"{LIVE_PREFIX}[0-9][0-9][0-9]") if _is_live_slot(p))


def install_live_slots(cfg: cfgmod.Config) -> tuple[int, int]:
    """Create ``cfg.live_slots`` slot folders and remove surplus ones.

    Returns ``(present, removed)``. Idempotent: an existing slot keeps its
    ``Live.lua``, which may hold an answer the game has not read yet.
    """
    target = _checked_addons_dir(cfg)
    want = max(0, min(int(cfg.live_slots), MAX_LIVE_SLOTS))
    interface_line = _interface_line(addon_source_dir()) if want else ""
    for index in range(1, want + 1):
        folder = target / live_slot_name(index)
        if folder.exists() and not _is_live_slot(folder) and any(folder.iterdir()):
            raise ValueError(f"refusing to reuse {folder}: it exists and was not created by wowclaude")
        folder.mkdir(exist_ok=True)
        toc = folder / f"{folder.name}.toc"
        text = live_slot_toc(index, interface_line)
        if not toc.is_file() or toc.read_text(encoding="utf-8", errors="replace") != text:
            toc.write_text(text, encoding="utf-8", newline="\n")
        live = folder / LIVE_FILE
        if not live.is_file():
            live.write_text(f"{LIVE_GLOBAL} = nil\n", encoding="utf-8", newline="\n")
    removed = 0
    for folder in installed_live_slots(cfg):
        if int(folder.name[-3:]) > want:
            shutil.rmtree(folder)
            removed += 1
    return want, removed


# ------------------------------------------------------------------- uninstall

def uninstall_addons(cfg: cfgmod.Config) -> list[Path]:
    """Remove the two addon folders and every live slot folder this module created."""
    target = _checked_addons_dir(cfg)
    removed: list[Path] = []
    for folder in installed_live_slots(cfg):
        shutil.rmtree(folder)
        removed.append(folder)
    for name in ADDON_FOLDERS:
        dst = target / name
        if dst.is_dir():
            shutil.rmtree(dst)
            removed.append(dst)
    return removed


def installed_status(cfg: cfgmod.Config) -> dict[str, bool]:
    target = cfgmod.addons_dir(cfg)
    return {name: (target / name / f"{name}.toc").is_file() for name in ADDON_FOLDERS}


def installed_version(cfg: cfgmod.Config) -> str | None:
    toc = cfgmod.addons_dir(cfg) / cfgmod.UI_ADDON / f"{cfgmod.UI_ADDON}.toc"
    if not toc.is_file():
        return None
    for line in toc.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("## Version:"):
            return line.split(":", 1)[1].strip()
    return None
