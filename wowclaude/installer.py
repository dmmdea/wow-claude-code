"""Install and remove the two addon folders.

The only places this module ever writes are ``<flavor>/Interface/AddOns/ClaudeCode``
and ``<flavor>/Interface/AddOns/ClaudeCode_Data``. Any other destination is refused.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from wowclaude import config as cfgmod

ADDON_FOLDERS = (cfgmod.UI_ADDON, cfgmod.DATA_ADDON)


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


def uninstall_addons(cfg: cfgmod.Config) -> list[Path]:
    target = _checked_addons_dir(cfg)
    removed: list[Path] = []
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
