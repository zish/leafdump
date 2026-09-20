#!/usr/bin/env python3
# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

"""Assert that nothing private reaches the published repository.

Three questions, in the order they are worth asking.

**Is this project's internal documentation tracked?**  ``CLAUDE.md`` and
``.claude/`` are written for people working *on* leafdump; this repository is
public, so they are kept on the developer's machine.  ``.gitignore`` lists them,
which is necessary and nowhere near sufficient: it has no effect on a path that
is *already tracked*, and ``git add -f`` overrides it silently.  Neither failure
is visible in a diff, and the commit is the moment that matters -- once a path
is in a commit it is in the history, and deleting it later does not take it out.

**Is another tool's equivalent tracked?**  Not every contributor uses the same
assistant, and each one invents its own dotfile.  Some of those are merely
someone's private notes; several are worse.  ``.mcp.json`` holds server
definitions whose environment blocks routinely carry API tokens,
``.continue/config.json`` and ``.aider.conf.yml`` can hold model keys, and
``.aider.chat.history.md`` is a verbatim transcript of everything discussed.
Those are credential and transcript leaks rather than policy violations, and the
argument for catching them does not depend on this project's documentation
policy at all.

**Is there a top-level entry nobody decided to publish?**  The list above is a
deny-list, and a deny-list is permanently behind the tools -- the one that leaks
will be the one invented next quarter.  So the guarantee does not rest on it.
Agent tooling lands at the repository root or in a new root dotdir essentially
without exception, and this project's root is small and stable, so every
top-level entry is enumerated in ``ALLOWED_ROOT`` and anything else is an error.
That catches tools that do not exist yet, which is the thing a deny-list
structurally cannot do.  Nested paths are unconstrained: a new module under
``leafdump/`` or a new test is nobody's business but the author's.

The deny-list still earns its place.  Both checks would catch a stray
``.cursor/rules/go.mdc``, but only one of them can say *what it is*.

Finally, the rule the internal/external split exists for: nothing published may
*point at* the internal collection.  Someone reading the manpage from a distro
package, or the README rendered on PyPI, cannot follow a "see CLAUDE.md"
reference -- it resolves to nothing and advertises that the real explanation is
somewhere they cannot reach.  Where a shipped file needs a fact the internal
docs also record, it has to state the fact.
"""

from __future__ import annotations

import fnmatch
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# This project's own internal collection. Separate from the table below because
# the remedy differs: these are ours and deliberate, so the advice is about the
# split and about history, not "some tool wrote this".
INTERNAL_PATHS = ("CLAUDE.md", "CLAUDE.local.md", ".claude")

# The strings that constitute a *reference* to it. Kept apart from the paths
# because a reference is a matter of prose -- "see CLAUDE.md" -- while a path is
# a matter of the index.
INTERNAL_NAMES = ("CLAUDE.md", ".claude/")

# Other assistants' project files, mapped to what they are, so the error can
# name the tool rather than just the path. Patterns are matched exactly, as a
# directory prefix, and as a glob.
#
# Deliberately absent: CONVENTIONS.md (aider reads it, but it is a name a human
# would plausibly choose on purpose) and Zed's .rules (same problem, worse). A
# false positive there lands on a real file someone wrote deliberately, and
# ALLOWED_ROOT catches both anyway -- just without naming them.
AGENT_TOOLING = {
    "AGENTS.md": "cross-tool agent instructions",
    ".codex": "OpenAI Codex CLI",
    ".mcp.json": "MCP server definitions -- these carry API tokens",
    ".cursorrules": "Cursor rules (legacy single-file form)",
    ".cursor": "Cursor project rules and indexing config",
    ".github/copilot-instructions.md": "GitHub Copilot instructions",
    ".github/instructions": "GitHub Copilot scoped instructions",
    ".github/prompts": "GitHub Copilot prompt files",
    "GEMINI.md": "Gemini CLI project instructions",
    ".gemini": "Gemini CLI configuration",
    ".windsurfrules": "Windsurf rules (legacy single-file form)",
    ".windsurf": "Windsurf rules directory",
    ".clinerules": "Cline rules",
    ".roo": "Roo Code configuration",
    ".roorules": "Roo Code rules",
    ".roomodes": "Roo Code modes",
    ".continue": "Continue config -- config.json can hold API keys",
    ".continuerc.json": "Continue configuration",
    ".aider*": "aider config, chat transcripts and caches",
    ".amazonq": "Amazon Q rules",
    ".junie": "JetBrains Junie guidelines",
    ".augment": "Augment configuration",
    ".augment-guidelines": "Augment guidelines",
    ".kilocode": "Kilo Code configuration",
    ".openhands": "OpenHands configuration",
    ".sourcegraph": "Sourcegraph Cody configuration",
    ".ai": "generic assistant context directory",
}

# Every top-level entry this repository publishes. Adding a root file is meant
# to take a decision rather than a `git add -A`.
ALLOWED_ROOT = frozenset(
    {
        ".github",
        ".gitignore",
        "AUTHORS.md",
        "build_events.py",
        "contrib",
        "INSTALL.md",
        "leafdump",
        "lefthook.yml",
        "LICENSE",
        "Makefile",
        "MANIFEST.in",
        "man",
        "pyproject.toml",
        "README.md",
        "ROADMAP.md",
        "scripts",
        "SECURITY.md",
        "tests",
        "VERSIONING.md",
    }
)

# Files whose job is to name the paths above, and which would otherwise report
# themselves. Both are machinery rather than documentation: .gitignore has to
# spell out what it excludes, and this script has to spell out what it hunts.
ALLOWED_TO_NAME_THEM = frozenset({".gitignore", "scripts/check_docs.py"})


def tracked_files() -> list[str] | None:
    """Every path in the index, or None when there is no index to read.

    Every question below is a question about the index -- is this path tracked,
    is that one -- so a tree without an index has no answer rather than a wrong
    one, and None says exactly that.  Raising would instead fail this gate for a
    tree that is doing nothing wrong: the tarball GitHub generates for a release
    tag is the whole checkout minus `.git`, Makefile and scripts/ included, and
    it is what a packager building from a tag unpacks.  Resolving git to an
    absolute path up front makes "git is not installed" -- a minimal build
    container, usually -- the same quiet no-op as "this is not a checkout".

    Not the PyPI sdist, which carries neither the Makefile nor this file and so
    has nothing here to run.  `make sdist-check` is what covers that tarball.
    """
    git = shutil.which("git")
    if git is None:
        return None
    try:
        out = subprocess.run(
            [git, "-C", str(ROOT), "ls-files", "-z"],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in out.stdout.decode().split("\0") if p]


def matches(path: str, pattern: str) -> bool:
    """Exact hit, anything beneath a directory, or a glob on either."""
    if path == pattern or path.startswith(pattern + "/"):
        return True
    if fnmatch.fnmatch(path, pattern):
        return True
    # A glob naming a directory has to cover that directory's contents too:
    # `.aider*` must match `.aider.tags.cache.v4/index`.
    head, sep, _ = path.partition("/")
    return bool(sep) and fnmatch.fnmatch(head, pattern)


def tracked_internal(tracked: list[str]) -> list[str]:
    return sorted(p for p in tracked if any(matches(p, i) for i in INTERNAL_PATHS))


def tracked_agent_tooling(tracked: list[str]) -> list[tuple[str, str]]:
    hits = []
    for path in sorted(tracked):
        for pattern, what in AGENT_TOOLING.items():
            if matches(path, pattern):
                hits.append((path, what))
                break
    return hits


def unexpected_root(tracked: list[str]) -> list[str]:
    return sorted({p.partition("/")[0] for p in tracked} - ALLOWED_ROOT)


def references(tracked: list[str]) -> list[tuple[str, int, str]]:
    hits = []
    for path in sorted(tracked):
        if path in ALLOWED_TO_NAME_THEM:
            continue
        try:
            text = (ROOT / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Absent (a stale index entry) or binary. Neither can contain prose
            # that points a reader anywhere.
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(name in line for name in INTERNAL_NAMES):
                hits.append((path, lineno, line.strip()))
    return hits


def main() -> int:
    tracked = tracked_files()
    if tracked is None:
        print("ok: not a git checkout, nothing to check")
        return 0

    internal = tracked_internal(tracked)
    if internal:
        print("error: internal documentation is tracked by git:")
        for path in internal:
            print(f"    {path}")
        print()
        print("This repository is published. Untrack it before it is pushed:")
        print(f"    git rm --cached {' '.join(internal)}")
        print()
        print("If it has already been committed, .gitignore will not help --")
        print("the history keeps a copy. Rewrite the affected commits instead.")
        return 1

    tooling = tracked_agent_tooling(tracked)
    if tooling:
        print("error: another tool's project files are tracked by git:")
        for path, what in tooling:
            print(f"    {path:42} {what}")
        print()
        print("Agent tooling is not published from this repository. Some of")
        print("these carry API tokens or verbatim chat transcripts, so check")
        print("what was in them before deciding this was harmless:")
        print(f"    git rm --cached {' '.join(p for p, _ in tooling)}")
        return 1

    strays = unexpected_root(tracked)
    if strays:
        print("error: top-level entries nobody decided to publish:")
        for name in strays:
            print(f"    {name}")
        print()
        print("Every root entry is enumerated, because the root is where")
        print("assistant config lands and a named list is always behind the")
        print("tools. If this is meant to ship, add it to ALLOWED_ROOT here.")
        return 1

    pointers = references(tracked)
    if pointers:
        print("error: published files reference the internal documentation:")
        for path, lineno, line in pointers:
            print(f"    {path}:{lineno}: {line}")
        print()
        print("A reader with a package installed and no checkout cannot follow")
        print("these. State the fact instead of citing where it is written.")
        return 1

    print(
        f"ok: {len(tracked)} tracked files, "
        f"{len(ALLOWED_ROOT)} known root entries, no agent tooling"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
