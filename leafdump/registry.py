# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

"""Declarative catalogue of every input/output format leafdump knows about.

Formats are *declared* here and *implemented* in :mod:`leafdump.codecs`.
Keeping the two apart means ``--help`` and the completion scripts can describe
every format -- including the unavailable ones -- without importing a single
third-party library.  Availability is probed with :func:`importlib.util.find_spec`,
which reads package metadata off disk but never executes module code.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterable
from dataclasses import dataclass

from .templates import TEMPLATES

# --------------------------------------------------------------------------
# Dependency model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Dep:
    """One importable module and the distribution that provides it."""

    module: str
    pip: str
    note: str = ""

    def present(self) -> bool:
        try:
            return importlib.util.find_spec(self.module) is not None
        except (ImportError, ValueError):
            return False


@dataclass(frozen=True)
class DepGroup:
    """A set of interchangeable dependencies; satisfied if *any* is present."""

    alternatives: tuple[Dep, ...]

    def satisfied(self) -> bool:
        return any(d.present() for d in self.alternatives)

    def install_hint(self) -> str:
        names = " or ".join(f"pip install {d.pip}" for d in self.alternatives)
        return names


def _deps(*specs: tuple[tuple[str, str], ...]) -> tuple[DepGroup, ...]:
    """Shorthand: each positional arg is one required group of alternatives.

    One argument is one group, and a group is a tuple of ``(module, pip)``
    pairs that satisfy it interchangeably -- hence the doubled parentheses at
    every call site, including the common single-alternative case:

        groups=_deps((("yaml", "PyYAML"),))
    """
    return tuple(DepGroup(tuple(Dep(m, p) for m, p in group)) for group in specs)


# --------------------------------------------------------------------------
# Format model
# --------------------------------------------------------------------------

_HAVE_TOMLLIB = importlib.util.find_spec("tomllib") is not None

TEXT = "text"
BINARY = "binary"
RENDER = "render"  # write-only, human/eyeball oriented


@dataclass(frozen=True)
class Format:
    name: str
    summary: str
    kind: str = TEXT
    aliases: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    reads: bool = True
    writes: bool = True
    streams: bool = False  # native multi-document support
    extra: str = ""  # pip extras group, e.g. `pip install leafdump[yaml]`
    groups: tuple[DepGroup, ...] = ()  # needed for either direction
    read_groups: tuple[DepGroup, ...] = ()  # needed only to read
    write_groups: tuple[DepGroup, ...] = ()  # needed only to write
    notes: tuple[str, ...] = ()

    # -- availability ------------------------------------------------------
    def _groups_for(self, direction: str) -> tuple[DepGroup, ...]:
        """Dependency groups relevant to *direction* ("in", "out" or "any").

        A few formats are asymmetric -- TOML reading is stdlib (``tomllib``)
        while writing needs ``tomli-w`` -- so availability is a question about
        a direction, not about the format as a whole.
        """
        extra = {
            "in": self.read_groups,
            "out": self.write_groups,
        }.get(direction, self.read_groups + self.write_groups)
        return self.groups + extra

    def missing_for(self, direction: str = "any") -> tuple[DepGroup, ...]:
        return tuple(g for g in self._groups_for(direction) if not g.satisfied())

    def available_for(self, direction: str = "any") -> bool:
        return not self.missing_for(direction)

    @property
    def missing(self) -> tuple[DepGroup, ...]:
        """Groups missing for *any* use at all (i.e. format is fully dead)."""
        return tuple(g for g in self.groups if not g.satisfied())

    @property
    def available(self) -> bool:
        """True if the format can do *something* -- read or write."""
        return self.can_read or self.can_write

    @property
    def can_read(self) -> bool:
        return self.reads and self.available_for("in")

    @property
    def can_write(self) -> bool:
        return self.writes and self.available_for("out")

    def install_hint(self, direction: str = "any") -> str:
        missing = self.missing_for(direction)
        if not missing:
            return ""
        primary = "; ".join(g.install_hint() for g in missing)
        if self.extra:
            primary += f"  (or: pip install 'leafdump[{self.extra}]')"
        return primary

    @property
    def direction(self) -> str:
        if self.reads and self.writes:
            return "in/out"
        return "in" if self.reads else "out"

    @property
    def live_direction(self) -> str:
        """Directions actually usable right now, given installed packages."""
        if self.can_read and self.can_write:
            return "in/out"
        if self.can_read:
            return "in"
        if self.can_write:
            return "out"
        return "--"


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------

# Every pseudocode template is also an output format under its own name, so
# `--to go` works the same way `--to perl` always has.  Deriving them from
# leafdump.templates rather than restating them keeps the rule that adding a
# notation is a one-file change -- and keeps --list-formats, --help-format and
# all three shell completions describing the same set.
_TEMPLATE_FORMATS: tuple[Format, ...] = tuple(
    Format(
        name=t.name,
        summary=t.summary,
        kind=RENDER,
        aliases=t.aliases,
        reads=False,
        notes=t.notes,
    )
    for t in TEMPLATES
)

FORMATS: tuple[Format, ...] = (
    # ---- always available (stdlib) ---------------------------------------
    Format(
        name="json",
        summary="JSON (RFC 8259) -- the reference format.",
        aliases=("js",),
        extensions=(".json",),
        streams=True,
        notes=(
            "Object keys are always strings; non-string keys from other "
            "formats are coerced to strings on write.",
        ),
    ),
    Format(
        name="jsonl",
        summary="Newline-delimited JSON (JSON Lines / NDJSON), one document per line.",
        aliases=("ndjson", "jsonlines"),
        extensions=(".jsonl", ".ndjson", ".jsonlines"),
        streams=True,
        notes=(
            "The natural companion to --multipacket: every line is an "
            "independent document.",
        ),
    ),
    Format(
        name="repr",
        summary="Python literal (repr) -- round-trips via ast.literal_eval.",
        aliases=("pyliteral", "literal"),
        extensions=(".pyl",),
        notes=("Reading uses ast.literal_eval, which never executes code.",),
    ),
    # ---- pseudocode path dumps (leafdump.templates) ---------------------
    *_TEMPLATE_FORMATS,
    Format(
        name="pseudocode",
        summary="Path dump in the notation named by --template, including a "
        "template loaded from a file.",
        kind=RENDER,
        aliases=("template", "tmpl"),
        reads=False,
        notes=(
            "This is what --template selects; every built-in template is also "
            "reachable directly as an output format of its own name.",
            "Custom templates are data files, not code: see "
            "`leafdump --help-template` and TEMPLATES in the manpage.",
        ),
    ),
    Format(
        name="toml",
        summary="TOML v1.0 configuration format.",
        extensions=(".toml",),
        extra="toml",
        # Reading is stdlib (tomllib, 3.11+); only writing needs a dependency.
        read_groups=_deps((("tomli", "tomli"),)) if not _HAVE_TOMLLIB else (),
        write_groups=_deps((("tomli_w", "tomli-w"),)),
        notes=(
            "TOML has no null: null values are dropped on write "
            "(use --null-policy to change).",
            "The document root must be a table (mapping); a top-level array "
            "or scalar cannot be written.",
        ),
    ),
    # ---- optional --------------------------------------------------------
    Format(
        name="yaml",
        summary="YAML 1.1 via PyYAML (safe_load / safe_dump only).",
        aliases=("yml",),
        extensions=(".yaml", ".yml"),
        streams=True,
        extra="yaml",
        groups=_deps((("yaml", "PyYAML"),)),
        notes=(
            "Only the safe subset is used; arbitrary Python object tags are "
            "never constructed.",
            "Multi-document streams (--- separators) are supported natively.",
        ),
    ),
    Format(
        name="json5",
        summary="JSON5 -- comments, trailing commas, unquoted keys.",
        aliases=("j5",),
        extensions=(".json5",),
        extra="json5",
        groups=_deps((("json5", "json5"), ("pyjson5", "pyjson5"))),
        notes=(
            "pyjson5 is a C extension and much faster; json5 is pure Python "
            "but preserves more JSON5-specific spellings on write.",
        ),
    ),
    Format(
        name="toon",
        summary="TOON -- Token-Oriented Object Notation, a compact JSON "
        "alternative for LLM prompts.",
        extensions=(".toon",),
        extra="toon",
        groups=_deps((("toon_format", "toon-format"), ("toon", "python-toon"))),
        notes=(
            "Uniform arrays of objects collapse to a tabular form, which is "
            "where the token savings come from.",
            "Prefer toon-format: the distribution `python-toon` installs a "
            "module named `toon`, which collides with an unrelated "
            "neuroscience package of the same name.",
        ),
    ),
    Format(
        name="msgpack",
        summary="MessagePack -- compact binary, JSON-equivalent data model.",
        kind=BINARY,
        aliases=("messagepack", "mpack"),
        extensions=(".msgpack", ".mpk", ".mp"),
        streams=True,
        extra="msgpack",
        groups=_deps((("msgpack", "msgpack"),)),
        notes=("Map keys may be of any type; binary strings survive round-trips.",),
    ),
    Format(
        name="cbor",
        summary="CBOR (RFC 8949) -- IETF-standard binary object representation.",
        kind=BINARY,
        aliases=("cbor2",),
        extensions=(".cbor",),
        streams=True,
        extra="cbor",
        groups=_deps((("cbor2", "cbor2"),)),
        notes=(
            "A strictly better-specified peer of MessagePack: same data model "
            "plus tagged extension types (dates, big integers, decimals).",
        ),
    ),
    Format(
        name="bson",
        summary="BSON -- MongoDB's binary document encoding.",
        kind=BINARY,
        extensions=(".bson",),
        streams=True,
        extra="bson",
        groups=_deps((("bson", "pymongo"),)),
        notes=(
            "Requires the `bson` module bundled with pymongo. The unrelated "
            "PyPI distribution literally named `bson` is NOT compatible.",
            "The document root must be a mapping with string keys.",
            "Integers are limited to 64 bits; larger values are rejected.",
        ),
    ),
    Format(
        name="avro",
        summary="Apache Avro object container file (schema-carrying).",
        kind=BINARY,
        extensions=(".avro",),
        streams=True,
        extra="avro",
        groups=_deps((("fastavro", "fastavro"),)),
        notes=(
            "Avro is schema-first. Reading is exact -- an .avro file carries "
            "its own schema. Writing infers a schema from the data.",
            "Inference produces union types for heterogeneous arrays and is "
            "necessarily approximate; supply --avro-schema for real control.",
        ),
    ),
    Format(
        name="nestedtext",
        summary="NestedText -- human-authored nested data, strings only.",
        aliases=("nt",),
        extensions=(".nt",),
        extra="nestedtext",
        groups=_deps((("nestedtext", "nestedtext"),)),
        notes=(
            "NestedText has NO scalar types: every leaf is a string. Numbers, "
            "booleans and null all become text on write and stay text on read.",
            "In exchange, there is nothing to escape and nothing to quote -- "
            "excellent for config humans edit, lossy as a dump target.",
        ),
    ),
    Format(
        name="protobuf-struct",
        summary="Protocol Buffers google.protobuf.Value (the schemaless "
        "well-known type).",
        kind=BINARY,
        aliases=("protobuf", "proto", "pb"),
        extensions=(".pb", ".protobuf"),
        extra="protobuf",
        groups=_deps((("google.protobuf", "protobuf"),)),
        notes=(
            "General protobuf is schema-first and has no schemaless encoding; "
            "this format uses google.protobuf.Value, the JSON-equivalent "
            "well-known type, which is the only schemaless option.",
            "Value has a single numeric type (double): every integer is "
            "returned as a float, and integers beyond 2**53 lose precision.",
        ),
    ),
)


# --------------------------------------------------------------------------
# Lookup helpers
# --------------------------------------------------------------------------

_BY_NAME: dict[str, Format] = {}
for _f in FORMATS:
    _BY_NAME[_f.name] = _f
    for _a in _f.aliases:
        _BY_NAME.setdefault(_a, _f)

_BY_EXT: dict[str, Format] = {}
for _f in FORMATS:
    for _e in _f.extensions:
        _BY_EXT.setdefault(_e, _f)


class UnknownFormat(KeyError):
    pass


def lookup(name: str) -> Format:
    """Resolve a format name or alias, case-insensitively."""
    key = name.strip().lower()
    try:
        return _BY_NAME[key]
    except KeyError:
        raise UnknownFormat(name) from None


def by_extension(ext: str) -> Format | None:
    return _BY_EXT.get(ext.lower())


def names(
    *,
    reads: bool | None = None,
    writes: bool | None = None,
    available_only: bool = False,
) -> list[str]:
    """Canonical format names, optionally filtered. Used by completions."""
    out = []
    for f in FORMATS:
        if reads is not None and f.reads != reads:
            continue
        if writes is not None and f.writes != writes:
            continue
        if available_only:
            if reads and not f.can_read:
                continue
            if writes and not f.can_write:
                continue
            if reads is None and writes is None and not f.available:
                continue
        out.append(f.name)
    return out


def iter_formats(direction: str = "any") -> Iterable[Format]:
    for f in FORMATS:
        if direction == "in" and not f.reads:
            continue
        if direction == "out" and not f.writes:
            continue
        yield f
