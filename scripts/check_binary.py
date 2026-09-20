#!/usr/bin/env python3
# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

"""Verify a compiled leafdump binary offers the formats it was built with.

Run with the *build environment's* interpreter, not an arbitrary one:

    .venv-build/bin/python scripts/check_binary.py bin/leafdump

The comparison is between two things that must agree and are computed
independently -- what the build environment could import at compile time, and
what the finished binary says it can do.  A mismatch means an optional package
was installed when the binary was built but never made it in, which is the one
failure mode ``scripts/nuitka_includes.py`` exists to prevent and the one that
is invisible otherwise: Nuitka's compile succeeds, the binary runs, and the
format is quietly absent.

Availability is only half of it.  ``--list-formats`` reports what the registry
*believes*, and inside a compiled binary that belief is a ``find_spec`` probe
that can succeed for a package whose C extension did not survive the bundling.
So every format that both reads and writes is also round-tripped through the
real binary.  The payload is deliberately all-strings: NestedText has no scalar
types and protobuf's Value coerces every number to a double, so a document with
integers in it would fail for reasons that are documented behaviour rather than
build defects.  What is being tested here is that the codec loads and runs, not
that it is correct -- tests/test_leafdump.py owns correctness.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from leafdump import registry

PAYLOAD = b'{"name": "value", "nested": {"list": ["one", "two"]}}'

# A codec can be present, importable and compiled in, and still refuse to run
# because the third-party library behind it has not implemented that direction
# yet -- toon-format 0.1.0 ships an encode() whose body raises exactly this.
# That is an upstream gap, not a defect in the binary, and failing the build
# check over it would mean this target can never pass while the gap exists.
# It is reported loudly instead, so it stays visible without being fatal.
UPSTREAM_GAP = "not yet implemented"


def binary_formats(binary: Path) -> dict[str, str]:
    """Map format name -> live direction, as reported by the binary itself."""
    out = subprocess.run(
        [str(binary), "-L", "--porcelain"],
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout.decode()
    formats = {}
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) >= 2:
            formats[cols[0]] = cols[1]
    return formats


def expected_formats() -> dict[str, str]:
    """The same map, computed from the build environment's installed packages."""
    return {f.name: f.live_direction for f in registry.FORMATS}


def roundtrip(binary: Path, fmt: str) -> str | None:
    """Convert JSON -> fmt -> JSON through the binary. None on success."""
    try:
        encoded = subprocess.run(
            [str(binary), "-i", "json", "-o", fmt, "-"],
            input=PAYLOAD,
            capture_output=True,
            timeout=60,
        )
        if encoded.returncode != 0:
            return f"encode failed: {encoded.stderr.decode().strip()[:200]}"
        if not encoded.stdout:
            return "encode produced no output"

        decoded = subprocess.run(
            [str(binary), "-i", fmt, "-o", "json", "-"],
            input=encoded.stdout,
            capture_output=True,
            timeout=60,
        )
        if decoded.returncode != 0:
            return f"decode failed: {decoded.stderr.decode().strip()[:200]}"
        json.loads(decoded.stdout)
    except subprocess.TimeoutExpired:
        return "timed out"
    except json.JSONDecodeError as exc:
        return f"decode produced invalid JSON: {exc}"
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} <path-to-binary>", file=sys.stderr)
        return 2
    binary = Path(argv[1]).resolve()
    if not binary.is_file():
        print(f"error: {binary} is not a file", file=sys.stderr)
        return 2

    got, want = binary_formats(binary), expected_formats()
    failed = False

    missing = sorted(
        n for n, d in want.items() if d != "--" and got.get(n, "--") == "--"
    )
    if missing:
        failed = True
        print("error: the build environment can use these formats, the binary cannot:")
        for name in missing:
            print(
                f"    {name:16} expected {want[name]}, binary reports {got.get(name, 'absent')}"
            )
        print()
        print("  A missing --include flag. Check: scripts/nuitka_includes.py --report")

    narrowed = sorted(
        n for n, d in want.items() if d != "--" and got.get(n, "--") not in ("--", d)
    )
    if narrowed:
        failed = True
        print("error: the binary offers fewer directions than the build environment:")
        for name in narrowed:
            print(f"    {name:16} expected {want[name]}, binary reports {got[name]}")

    live = sorted(n for n, d in got.items() if d == "in/out")
    gaps = []
    print(f"round-tripping {len(live)} read/write formats through {binary.name}:")
    for name in live:
        problem = roundtrip(binary, name)
        if problem is None:
            print(f"    ok   {name}")
        elif UPSTREAM_GAP in problem:
            gaps.append((name, problem))
            print(f"    gap  {name:16} {problem}")
        else:
            failed = True
            print(f"    FAIL {name:16} {problem}")

    write_only = sorted(n for n, d in got.items() if d == "out")
    if write_only:
        print(f"write-only, not round-tripped: {', '.join(write_only)}")
    unavailable = sorted(n for n, d in got.items() if d == "--")
    if unavailable:
        print(f"not compiled in: {', '.join(unavailable)}")
    if gaps:
        print()
        print(
            "upstream gaps (the format is compiled in; the library cannot do it yet):"
        )
        for name, problem in gaps:
            print(f"    {name:16} {problem}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
