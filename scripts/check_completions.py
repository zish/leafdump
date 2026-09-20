#!/usr/bin/env python3
# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

"""Fail if a CLI option is missing from one of the shell completion scripts.

The completions do not need checking for *format* names: all three query the
binary at runtime (``leafdump -L --porcelain``), so the format list they offer
is always the live one and cannot go stale.

Options are the opposite case.  Each completion spells the flags out by hand --
bash keeps a word list, zsh a ``_arguments`` spec, fish one ``complete -c`` line
per flag -- because that is the only way to attach per-flag descriptions and
argument types.  A flag added to ``cli.build_parser`` is therefore invisible to
all three until somebody remembers, and nothing about the result looks broken:
completion simply stops offering the new flag.

This walks the real parser rather than grepping cli.py, so it sees exactly what
argparse sees, including flags added through groups or by argparse itself.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from leafdump.cli import build_parser

COMPLETIONS = {
    "bash": ROOT / "contrib" / "completions" / "leafdump.bash",
    "fish": ROOT / "contrib" / "completions" / "leafdump.fish",
    "zsh": ROOT / "contrib" / "completions" / "_leafdump",
}

# argparse generates -h/--help itself and every shell has its own opinion about
# whether to offer it; not worth failing a build over.
EXEMPT = {"-h", "--help"}


def parser_options() -> set[str]:
    """Every option string the parser accepts, across all argument groups."""
    return {
        opt for action in build_parser()._actions for opt in action.option_strings
    } - EXEMPT


def mentioned(text: str, opt: str) -> bool:
    """True if *opt* appears in *text* as a whole word.

    The word boundary matters in both directions: a bare substring test says
    ``--porcelain`` is present when only ``--porcelain-format`` is, and reports
    ``-i`` present because ``--indent`` contains an i.  The zsh spec writes
    flags as ``'-i[description]'`` and fish as ``-s i``/``-l indent``, so the
    match is anchored on the flag text and allows the punctuation each shell
    puts around it.
    """
    stripped = opt.lstrip("-")
    patterns = [
        re.escape(opt) + r"(?![\w-])",  # bash word lists, zsh specs
        r"-[sl]\s+" + re.escape(stripped) + r"(?![\w-])",  # fish: -s i / -l indent
    ]
    return any(re.search(p, text) for p in patterns)


def main() -> int:
    options = parser_options()
    failed = False

    for shell, path in sorted(COMPLETIONS.items()):
        if not path.exists():
            print(f"error: {path.relative_to(ROOT)} is missing")
            failed = True
            continue
        text = path.read_text(encoding="utf-8")
        missing = sorted(o for o in options if not mentioned(text, o))
        if missing:
            failed = True
            print(f"error: {path.relative_to(ROOT)} ({shell}) does not offer:")
            for opt in missing:
                print(f"    {opt}")

    if failed:
        print()
        print("Add the flags above to the completion scripts in contrib/completions/,")
        print("then re-run: make completions-check")
        return 1

    print(
        f"ok: all {len(options)} options are offered by all {len(COMPLETIONS)} completions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
