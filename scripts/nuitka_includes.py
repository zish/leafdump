#!/usr/bin/env python3
# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

"""Emit the ``--include-*`` flags a Nuitka build needs for the optional formats.

Nuitka decides what to bundle by *reading import statements*.  leafdump has
almost none to read: every optional dependency is reached through
``importlib.import_module(<string>)`` in :func:`leafdump.codecs._require`, and
probed with ``importlib.util.find_spec(<string>)`` in :mod:`leafdump.registry`.
A compiler cannot see through a string, so a plain ``nuitka leafdump/__main__.py``
compiles a binary that supports the stdlib formats and nothing else -- silently,
with a successful exit status and no warning.  The failure only shows up later,
as ``yaml: unavailable`` from a binary that was built on a machine where PyYAML
was installed.

This closes that gap without introducing a second list to keep in sync.  It
reads the same ``registry.FORMATS`` catalogue the CLI reads, so the rule that
adding a format means touching registry.py and codecs.py and nothing else still
holds: a new ``Format(...)`` entry is picked up here for free.

Only modules that are importable *at build time* are emitted, which makes the
build environment the thing that selects the binary's feature set:

    pip install .           ->  core formats only
    pip install '.[all]'    ->  every stable format compiled in

That is also why the resulting binary reports itself honestly.  ``registry``
probes with ``find_spec``, and inside a standalone binary that resolves against
what Nuitka compiled in rather than against the host's site-packages -- so
``leafdump --list-formats`` describes the binary, not the machine it runs on.

Usage:
    python3 scripts/nuitka_includes.py           # flags, one per line
    python3 scripts/nuitka_includes.py --list    # bare module names
    python3 scripts/nuitka_includes.py --report  # human-readable in/out summary
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

# Run from a source checkout without installing first: `make binary` compiles
# the working tree, not whatever version happens to be on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leafdump import registry


def _declared() -> list[tuple[str, str, str]]:
    """Every (format, module, pip name) the catalogue knows about, deduplicated.

    A format can name several interchangeable modules (json5 accepts `json5` or
    `pyjson5`); each is a separate candidate here, and the ones actually present
    are what gets compiled in.
    """
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for fmt in registry.FORMATS:
        groups = fmt.groups + fmt.read_groups + fmt.write_groups
        for group in groups:
            for dep in group.alternatives:
                if dep.module not in seen:
                    seen.add(dep.module)
                    out.append((fmt.name, dep.module, dep.pip))
    return out


def _is_package(module: str) -> bool:
    """True if *module* is a package, i.e. has submodules to pull in too.

    The distinction matters to Nuitka: ``--include-module`` takes the one file,
    ``--include-package`` takes the subtree.  Getting it wrong on protobuf is
    the visible case -- ``google.protobuf`` compiles fine as a module and then
    fails at runtime on the ``struct_pb2`` import it never bundled.
    """
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError, AttributeError):
        return False
    return bool(spec and spec.submodule_search_locations)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--list",
        action="store_true",
        help="print bare module names instead of Nuitka flags",
    )
    mode.add_argument(
        "--report",
        action="store_true",
        help="print which formats are in and out of this build",
    )
    args = ap.parse_args(argv)

    declared = _declared()
    present = [(f, m, p) for f, m, p in declared if registry.Dep(m, p).present()]

    if args.report:
        absent = [(f, m, p) for f, m, p in declared if (f, m, p) not in present]
        print(f"compiled in ({len(present)}):")
        for fmt, module, _pip in present:
            kind = "package" if _is_package(module) else "module"
            print(f"  {fmt:16} {module}  ({kind})")
        print(f"not available ({len(absent)}):")
        for fmt, module, pip in absent:
            print(f"  {fmt:16} {module}  -- pip install {pip}")
        return 0

    for _fmt, module, _pip in present:
        if args.list:
            print(module)
        else:
            flag = "--include-package" if _is_package(module) else "--include-module"
            print(f"{flag}={module}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
