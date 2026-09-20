#!/usr/bin/env python3
# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

"""Assert that everything naming a version names the same one.

``leafdump/__init__.py`` is the single source.  ``pyproject.toml`` reads it
through ``[tool.setuptools.dynamic]`` rather than restating it, so pip metadata
and ``leafdump --version`` cannot drift apart -- and the first thing this
checks is that the arrangement is still in place, because a static ``version =``
put back into ``[project]`` would take effect silently and look tidier than what
it replaced.

Two places still carry the number as text: the manpage's ``.TH`` line, which is
what a reader sees in the footer, and a ``v*`` tag when there is one.  The tag is
the one that matters.  ``release.yml`` fires on ``tags: ["v*"]`` and builds
whatever the tag points at, so tagging ``v1.1.0`` on a commit that still says
``1.0.0`` uploads a ``1.0.0`` artifact under a ``v1.1.0`` tag.  PyPI version
numbers are permanent: a wrong upload cannot be replaced, only yanked, and
yanking hides the number without freeing it.  There is no repair for that, which
is the whole argument for a check that takes milliseconds.

The version string itself is checked against what a package index will accept,
not against semver.org.  The two disagree exactly where it is easiest to get
wrong: a semver pre-release is ``1.0.0-rc.1`` and PEP 440 spells the same thing
``1.0.0rc1``.  VERSIONING.md promises semver for the *meaning* of the numbers --
what a major implies -- and the index is what has to parse them, so the spelling
follows PEP 440 and this is where that is written down.

Usage:
    python3 scripts/check_version.py          # compare every place
    python3 scripts/check_version.py --report # show them, with sources
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from leafdump import __version__

MANPAGE = ROOT / "man" / "leafdump.1"

# X.Y.Z, optionally a PEP 440 pre-release. Deliberately narrower than PEP 440
# allows: no epochs, no post-releases, no local versions, no `.dev`. Those are
# all legal on an index and none of them mean anything under a semver policy,
# so a version carrying one is a mistake rather than an intention.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?$")

# .TH LEAFDUMP 1 "<date>" "leafdump <version>" "User Commands"
#                                        ^^^^^^^^^ the fourth field carries it
TH_RE = re.compile(r'^\.TH\s+\S+\s+\d+\s+"([^"]*)"\s+"leafdump\s+([^"]+)"')


def pyproject_problems() -> list[str]:
    """Ways pyproject.toml could have stopped deferring to __init__.py."""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    project = config.get("project", {})
    problems = []
    if "version" in project:
        problems.append(
            f"[project] has a static version = {project['version']!r}; "
            "it must be dynamic so there is only one place to change"
        )
    if "version" not in project.get("dynamic", []):
        problems.append('[project] dynamic does not list "version"')
    attr = (
        config.get("tool", {})
        .get("setuptools", {})
        .get("dynamic", {})
        .get("version", {})
        .get("attr")
    )
    if attr != "leafdump.__version__":
        problems.append(
            f"[tool.setuptools.dynamic] version.attr is {attr!r}, "
            "expected 'leafdump.__version__'"
        )
    return problems


def manpage_fields() -> tuple[str, str] | None:
    """The ``.TH`` line's (date, version), or None if there is no such line."""
    for line in MANPAGE.read_text(encoding="utf-8").splitlines():
        found = TH_RE.match(line)
        if found:
            return found.group(1), found.group(2)
    return None


def head_tags() -> list[str]:
    """Every ``v*`` tag pointing at HEAD.

    Absent git, or a tree that is not a checkout, means no tag to disagree with
    -- the same quiet no-op the documentation check makes for the same reason.
    """
    git = shutil.which("git")
    if git is None:
        return []
    try:
        out = subprocess.run(
            [git, "-C", str(ROOT), "tag", "--points-at", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [t for t in out.stdout.split() if t.startswith("v")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--report", action="store_true", help="show every version string found"
    )
    args = parser.parse_args(argv)

    manpage = manpage_fields()
    tags = head_tags()

    if args.report:
        print(f"leafdump.__version__          {__version__}")
        print("pyproject.toml                 dynamic -> leafdump.__version__")
        if manpage:
            print(f"man/leafdump.1 .TH            {manpage[1]}  (dated {manpage[0]})")
        else:
            print("man/leafdump.1 .TH            (no .TH line found)")
        print(f"tags on HEAD                   {', '.join(tags) or '(none)'}")
        return 0

    if not VERSION_RE.match(__version__):
        print(f"error: __version__ is {__version__!r}, which a package index")
        print("       will not accept as written. Use X.Y.Z, or X.Y.ZrcN for a")
        print("       pre-release -- PEP 440 spells it 'rc1', not '-rc.1'.")
        return 1

    problems = pyproject_problems()
    if problems:
        print("error: pyproject.toml no longer reads the version from the package:")
        for problem in problems:
            print(f"    {problem}")
        print()
        print("Restore the single source, or `pip show` and `--version` will")
        print("disagree from the same install with nothing to notice it.")
        return 1

    if manpage is None:
        print(f"error: {MANPAGE.relative_to(ROOT)} has no recognisable .TH line")
        return 1
    if manpage[1] != __version__:
        print(f"error: the manpage says {manpage[1]}, the package says {__version__}")
        print()
        print(f"Update the .TH line in {MANPAGE.relative_to(ROOT)} -- the version")
        print(f"and the date, which currently reads {manpage[0]}.")
        return 1

    mismatched = [t for t in tags if t != f"v{__version__}"]
    if mismatched:
        print(f"error: HEAD is tagged {', '.join(mismatched)} but says {__version__}")
        print()
        print("release.yml builds whatever the tag points at, and a package")
        print("index keeps version numbers forever -- a wrong upload can only")
        print("be yanked, which hides the number without freeing it. Retag, or")
        print("bump __version__ to match before tagging.")
        return 1

    tagged = f", tagged v{__version__}" if tags else ""
    print(f"ok: {__version__} in the package, pyproject and the manpage{tagged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
