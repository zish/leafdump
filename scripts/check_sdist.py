#!/usr/bin/env python3
# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

"""Unpack a built sdist and prove its test suite passes inside it.

    python3 scripts/check_sdist.py            # the newest tarball in dist/
    python3 scripts/check_sdist.py PATH.tar.gz

The tarball is the artifact nobody looks at.  A wheel is inspected constantly --
it is what `pip install` produces and what every developer runs against -- while
the sdist is built by the release job and read by strangers: distro packagers,
`pip install` on a platform with no wheel, anyone auditing what they are about to
sign.  Those readers are the first to discover what it is missing, and what they
report is "the tests fail", which is indistinguishable from "the release is
broken" until someone goes looking.

MANIFEST.in is what fixes that, and MANIFEST.in cannot be reviewed by reading
it.  Its patterns are evaluated by the build backend against the working tree,
with a default include set layered underneath that nobody wrote down; the only
honest way to know what the tarball contains is to build one and look.  So this
asks the tarball two questions rather than trusting the file:

  Is everything there?  Every pattern in scripts/manifest_in.py is expanded
  against the checkout, and every file it names has to appear in the archive.
  A pattern that matches nothing is an error in itself -- an `include` for a
  path that has been renamed fails silently in setuptools, which prints a
  warning nobody reads and builds the tarball anyway.

  Does it work?  The suite is run from the unpacked tree, with that tree as the
  working directory, so the fixture paths resolve the way they will for whoever
  unpacks it.  This is the question that caught the original defect: the suite
  passed in the checkout and failed in the tarball, because the checkout had the
  worked template examples and the tarball did not.

The interpreter used is this one. leafdump's core has no dependencies, so an
unpacked sdist needs nothing installed to run its own suite -- the tests import
leafdump from the tree beside them. Optional formats are whatever this
environment happens to have; the tests skip what is absent, and `make
test-isolated` is where the dependency-free claim is checked.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import manifest_in


def newest_sdist() -> Path | None:
    """The most recently built tarball in dist/, so the usual case needs no argv."""
    found = sorted(
        (ROOT / "dist").glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return found[0] if found else None


def members(archive: Path) -> set[str]:
    """Every file in the tarball, with its leading `<name>-<version>/` stripped.

    Stripping makes the names comparable with the checkout-relative paths
    manifest_in.py produces.  Directory entries are dropped: an sdist records
    them inconsistently between backends and nothing here is asking about them.
    """
    with tarfile.open(archive) as tar:
        return {
            info.name.partition("/")[2]
            for info in tar.getmembers()
            if info.isfile() and "/" in info.name
        }


def empty_patterns() -> list[str]:
    """Directives that name no file at all -- a rename nobody propagated."""
    return [
        entry.directive
        for group in manifest_in.groups()
        for entry in group.entries
        if not entry.expand()
    ]


def run_suite(tree: Path) -> int:
    """The suite, from inside the unpacked tree, exactly as a packager runs it."""
    print(f"running the suite in {tree}", flush=True)
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=tree,
        check=False,
    ).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "sdist",
        nargs="?",
        type=Path,
        help="the tarball to check (default: the newest in dist/)",
    )
    args = parser.parse_args(argv)

    archive = args.sdist or newest_sdist()
    if archive is None:
        print("error: no sdist given and dist/ holds no *.tar.gz")
        print()
        print("Build one first:")
        print("    make dist")
        return 1
    if not archive.exists():
        print(f"error: no such file: {archive}")
        return 1

    stale = empty_patterns()
    if stale:
        print("error: MANIFEST.in directives that match nothing in this checkout:")
        for directive in stale:
            print(f"    {directive}")
        print()
        print("setuptools warns about these and builds the tarball regardless,")
        print("so the file they were meant to carry goes missing quietly. Fix")
        print("the pattern in scripts/manifest_in.py and run `make manifest`.")
        return 1

    shipped = members(archive)
    wanted = manifest_in.required_paths()
    missing = [path for path in wanted if path not in shipped]
    if missing:
        print(f"error: {archive.name} is missing {len(missing)} promised file(s):")
        for path in missing:
            print(f"    {path}")
        print()
        print("MANIFEST.in and the tarball disagree. Regenerate and rebuild:")
        print("    make manifest && make dist")
        return 1

    print(f"ok: {archive.name} carries all {len(wanted)} files MANIFEST.in promises")

    with tempfile.TemporaryDirectory(prefix="leafdump-sdist-") as tmp:
        with tarfile.open(archive) as tar:
            # filter="data" is the safe extraction mode: no absolute paths, no
            # escaping the destination, no device nodes. The default changes to
            # this in a later Python and warns until it does; being explicit
            # means the same behaviour on every version this project supports.
            tar.extractall(Path(tmp), filter="data")
        roots = [child for child in Path(tmp).iterdir() if child.is_dir()]
        if len(roots) != 1:
            print(f"error: expected one top-level directory, found {len(roots)}")
            return 1
        if run_suite(roots[0]) != 0:
            print()
            print(f"error: the suite fails inside {archive.name}, and passes here.")
            print("That difference is the checkout carrying a file the tarball")
            print("does not. Add the pattern to scripts/manifest_in.py.")
            return 1

    print(f"ok: {archive.name} passes its own test suite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
