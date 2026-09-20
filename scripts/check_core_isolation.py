#!/usr/bin/env python3
# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

"""Assert that importing leafdump pulls in nothing outside the standard library.

This is the executable form of the project's first invariant: `pip install
leafdump` with no extras has to give working perl/python/json/jsonl/repr output
and TOML input.  That holds only while every optional dependency stays behind a
lazy import, and a violation is close to invisible during development -- a
module-scope ``import yaml`` works perfectly on any machine that has PyYAML,
which is every machine anyone develops this on.  The person it breaks for is the
one who ran a bare ``pip install leafdump``, and by then it is shipped.

The check does not need a clean environment, which is what makes it worth
running everywhere rather than only in CI's `core` matrix leg.  It asks what was
*imported*, not what is *installed*, so a tree with every extra present still
fails here the moment an import escapes to module scope.

Directness matters more than cleverness in the implementation: the modules are
imported in this interpreter and ``sys.modules`` is compared against
``sys.stdlib_module_names``.  Anything left over is third-party by definition.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The public surface, imported the way the CLI imports it. registry and codecs
# are the two that matter -- registry probes for optional packages and codecs
# uses them -- but the entry point is included so that anything cli.py pulls in
# at import time is caught as well.
MODULES = [
    "leafdump",
    "leafdump.registry",
    "leafdump.codecs",
    "leafdump.flatten",
    "leafdump.templates",
    "leafdump.merge",
    "leafdump.cli",
]


def third_party_imports() -> list[str]:
    before = set(sys.modules)
    for name in MODULES:
        __import__(name)
    added = set(sys.modules) - before

    offenders = set()
    for name in added:
        top = name.partition(".")[0]
        if top in sys.stdlib_module_names or top.startswith("_"):
            continue
        if top == "leafdump":
            continue
        module = sys.modules.get(name)
        # Namespace packages and other module-shaped objects with no file are
        # not evidence of anything; a real third-party import has a location.
        if module is not None and getattr(module, "__file__", None):
            offenders.add(top)
    return sorted(offenders)


def main() -> int:
    offenders = third_party_imports()
    if offenders:
        print("error: importing leafdump loaded third-party modules:")
        for name in offenders:
            location = getattr(sys.modules[name], "__file__", "?")
            print(f"    {name:20} {location}")
        print()
        print("The core must run on the standard library alone. Move the import")
        print("inside the function that needs it -- see codecs._require.")
        return 1

    print(f"ok: {len(MODULES)} modules imported, nothing outside the stdlib")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
