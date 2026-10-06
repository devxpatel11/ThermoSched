"""Initial command-line entry point; feature commands land in D3-C3."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from thermosched import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thermosched",
        description="Linux user-space CPU thermal pacing scheduler prototype",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
