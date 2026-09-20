#!/usr/bin/env python3
# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

"""Generate MANIFEST.in -- what the sdist carries beyond the package itself.

setuptools builds a source distribution from two things: a default set it picks
on its own, and MANIFEST.in.  The default set is the trap.  It sweeps ``*.py``
from the project root, which is why ``tests/`` has always been in the tarball,
and it takes data files not at all, which is why none of the fixtures those
tests read have ever been in it.  Nobody wrote either rule, so nobody reviewed
the combination: a tarball carrying a test suite it cannot possibly pass.  That
is what a distro packager builds from, and a suite that fails during their
build reads as a broken release rather than a packaging bug.

Three groups ship, for three unrelated reasons, and they are kept apart here so
that adding to one does not look like a licence to add to another:

  documentation   the external collection -- written for someone holding a
                  package with no checkout.  README.md and LICENSE arrive by
                  themselves because pyproject.toml names them in `readme` and
                  `license-files`; the rest have no such mention anywhere.

  data files      derived, not listed.  Read straight out of pyproject.toml's
                  [tool.setuptools.data-files], which is already the one place
                  the manpage and the three completion scripts are named.

  test fixtures   everything tests/test_leafdump.py opens from the checkout.

The fixture group is declared rather than discovered.  The original argument for
that was specific and is now gone: TestPerlParity used to reach json_dump.pl and
contrib/*.json through subprocess, never opening them, and skipped itself where
perl was absent -- so an audit hook watching ``open()`` during a suite run would
have derived a *shorter* list on a machine without perl, and the omission would
not have surfaced until someone unpacked the tarball.  The Perl script has since
been retired and its output frozen into tests/golden/, which the suite reads
like any other file.

What survives that is the weaker, more general form of the same objection: a
derivation from one run describes the machine it ran on.  A test skipped for a
missing optional package contributes nothing, and nothing says so.

So it stays a list, with the two properties that make a list survivable.  The
entries are *patterns*: a fourth worked example, a fifth parity sample, or a
whole directory of frozen output arrives with no edit here -- ``graft tests``
picked up tests/golden/ on its own.  And `make sdist-check` runs the suite from
the built tarball, so an edit that was needed and forgotten fails a gate instead
of a release.

Usage:
    python3 scripts/manifest_in.py            # print the file
    python3 scripts/manifest_in.py --write    # write it (this is `make manifest`)
    python3 scripts/manifest_in.py --check    # fail if the committed file differs
    python3 scripts/manifest_in.py --report   # what ships, expanded, with counts
"""

from __future__ import annotations

import argparse
import difflib
import sys
import textwrap
import tomllib
from fnmatch import fnmatch
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "MANIFEST.in"

# Bytecode is the one thing a developer's tree reliably has that a tarball must
# not. It matters because `graft` takes what is on the disk rather than what is
# in the index -- git's ignore rules are not consulted and never have been.
# Named once: emitted as the trailing `global-exclude`, and applied to this
# script's own expansion so that what it promises and what it checks agree.
EXCLUDE = ("*.py[cod]",)


class Entry(NamedTuple):
    """One thing the sdist carries, as a checkout-relative pattern.

    A trailing ``/`` means the whole subtree and renders as ``graft``; anything
    else is a glob and renders as ``include``.  Both the directive and the file
    list are derived from the same string, which is the point: scripts/
    check_sdist.py expands these against the checkout and requires every result
    to be present in the built tarball, and it can only do that honestly while
    there is exactly one pattern rather than a directive and a copy of it.
    """

    pattern: str
    why: str

    @property
    def directive(self) -> str:
        if self.pattern.endswith("/"):
            return f"graft {self.pattern.rstrip('/')}"
        return f"include {self.pattern}"

    def expand(self, root: Path = ROOT) -> list[str]:
        """Every file this pattern names, relative to *root*, sorted."""
        if self.pattern.endswith("/"):
            found = (root / self.pattern.rstrip("/")).rglob("*")
        else:
            found = root.glob(self.pattern)
        return sorted(
            str(path.relative_to(root))
            for path in found
            if path.is_file() and not any(fnmatch(path.name, e) for e in EXCLUDE)
        )


class Group(NamedTuple):
    title: str
    rationale: str
    entries: tuple[Entry, ...]


# The external collection, minus the two pyproject.toml already names. Every one
# of these is written for a reader who has a package and no checkout, which is
# the whole argument for putting them in the package.
DOCS = (
    Entry("AUTHORS.md", "who wrote it"),
    Entry("INSTALL.md", "extras, source installs, completion, the manpage"),
    Entry("ROADMAP.md", "what is not built yet, and why"),
    Entry("SECURITY.md", "how to report something -- the first file an auditor opens"),
    Entry("VERSIONING.md", "what a major bump means -- the compatibility promise"),
)

# Everything tests/test_leafdump.py reads from the checkout. The comment on
# each is the test that breaks without it, because that is the question anyone
# reading this list is actually asking.
FIXTURES = (
    Entry("tests/", "the suite itself, and any data file added beside it"),
    Entry(
        "contrib/templates/*.json",
        "TestTemplates.test_shipped_examples_load_and_render -- asserts, "
        "does not skip, so this group is the one that fails the build",
    ),
    Entry("contrib/templates/*.md", "what those examples are for"),
    Entry("contrib/*.json", "TestPerlParity.SAMPLES -- fed to both implementations"),
)


def data_files() -> tuple[Entry, ...]:
    """The installed data files, read from pyproject.toml rather than listed.

    setuptools does already carry a path referenced by ``data-files`` into the
    sdist, so today these lines change nothing.  They are emitted anyway: that
    behaviour is an implementation detail of the backend and nothing in this
    repository would notice it changing, whereas a missing manpage in a distro
    build is somebody's bug report.
    """
    with (ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    table = config.get("tool", {}).get("setuptools", {}).get("data-files", {})
    return tuple(
        sorted(
            (
                Entry(path, f"installed into {dest}")
                for dest, paths in table.items()
                for path in paths
            ),
            key=lambda entry: entry.pattern,
        )
    )


def groups() -> tuple[Group, ...]:
    return (
        Group(
            "external documentation",
            "README.md and LICENSE arrive on their own -- pyproject.toml names\n"
            "them in `readme` and `license-files`. Nothing names these four.",
            DOCS,
        ),
        Group(
            "installed data files",
            "Derived from pyproject.toml's [tool.setuptools.data-files], so the\n"
            "manpage and the completions stay named in exactly one place.",
            data_files(),
        ),
        Group(
            "test fixtures",
            "The sdist ships tests/, so it has to ship what the tests read.\n"
            "setuptools sweeps *.py from the project root by default and data\n"
            "files not at all, which is how a tarball ends up carrying a suite\n"
            "it cannot pass. Patterns rather than filenames: another worked\n"
            "example or parity sample needs no edit. `make sdist-check` runs\n"
            "the suite from the built tarball, so a group that was forgotten\n"
            "fails a gate rather than a release.",
            FIXTURES,
        ),
    )


HEADER = """\
# MANIFEST.in -- what the source distribution carries beyond the package.
#
# Generated by scripts/manifest_in.py. Edit that script and run
# `make manifest`; editing this file works until the next regeneration
# discards it, and `make manifest-check` fails the moment the two disagree.
"""


WIDTH = 78


def rule(title: str) -> str:
    """A section banner padded to WIDTH, so the file reads as blocks."""
    head = f"# --- {title} "
    return head + "-" * max(3, WIDTH - len(head)) + "\n"


def comment(text: str) -> str:
    """Wrap prose into `# ` lines, preserving the blank lines between blocks."""
    out: list[str] = []
    for block in text.split("\n\n"):
        out.extend(
            f"{line}\n"
            for line in textwrap.wrap(
                " ".join(block.split()),
                width=WIDTH,
                initial_indent="# ",
                subsequent_indent="# ",
            )
        )
    return "".join(out)


def render() -> str:
    out = [HEADER]
    for group in groups():
        out.append("\n" + rule(group.title))
        out.append(comment(group.rationale))
        for entry in group.entries:
            out.append("\n" + comment(entry.why) + entry.directive + "\n")
    out.append("\n" + rule("never"))
    out.append(
        comment(
            "`graft` takes what is on the disk, and git's ignore rules have no "
            "say in a source distribution. Last, because MANIFEST.in applies "
            "its directives in the order it reads them."
        )
    )
    out.extend(f"global-exclude {pattern}\n" for pattern in EXCLUDE)
    return "".join(out)


def required_paths(root: Path = ROOT) -> list[str]:
    """Every file the manifest promises, expanded against a tree.

    Used against the checkout to say what the tarball must contain, and against
    the unpacked tarball to say what it does.  Same patterns, same exclusions,
    so a difference is a real difference rather than a discrepancy between two
    ways of asking.
    """
    seen: set[str] = set()
    for group in groups():
        for entry in group.entries:
            seen.update(entry.expand(root))
    return sorted(seen)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write MANIFEST.in")
    mode.add_argument(
        "--check", action="store_true", help="fail if MANIFEST.in is out of date"
    )
    mode.add_argument(
        "--report", action="store_true", help="show what ships, expanded, with counts"
    )
    args = parser.parse_args(argv)

    wanted = render()

    if args.report:
        total = 0
        for group in groups():
            print(f"{group.title}:")
            for entry in group.entries:
                found = entry.expand()
                total += len(found)
                print(f"  {entry.directive}")
                for path in found:
                    print(f"      {path}")
                if not found:
                    print("      (nothing -- this pattern matches no file)")
        print(f"\n{total} files beyond leafdump/, README.md and LICENSE")
        return 0

    if args.write:
        MANIFEST.write_text(wanted, encoding="utf-8")
        print(f"wrote {MANIFEST.relative_to(ROOT)}")
        return 0

    if args.check:
        have = MANIFEST.read_text(encoding="utf-8") if MANIFEST.exists() else ""
        if have == wanted:
            print(f"ok: MANIFEST.in matches {Path(__file__).name}")
            return 0
        print("error: MANIFEST.in is not what scripts/manifest_in.py generates:")
        print()
        sys.stdout.writelines(
            difflib.unified_diff(
                have.splitlines(keepends=True),
                wanted.splitlines(keepends=True),
                "MANIFEST.in (committed)",
                "MANIFEST.in (generated)",
            )
        )
        print()
        print("The script is the source. Take the generated version with:")
        print("    make manifest")
        return 1

    sys.stdout.write(wanted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
