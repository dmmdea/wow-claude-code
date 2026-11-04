"""Command-line entry point. Subcommands are filled in by the companion build."""

from __future__ import annotations

import argparse

from wowclaude import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wowclaude",
        description="Companion for the ClaudeCode World of Warcraft addon.",
    )
    parser.add_argument("--version", action="version", version=f"wowclaude {__version__}")
    parser.add_subparsers(dest="command")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    return 0
