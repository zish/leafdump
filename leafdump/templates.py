# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

"""Pseudocode templates -- the language-shaped notations the path dump can emit.

A template is *data*, not code: a dozen small strings describing how one leaf
of a nested structure is spelled as a line of some language.  Rendering is one
generic loop (:func:`leafdump.flatten.render_template`) driven by whichever
template is selected, so adding a language means adding a `Template(...)` entry
here and nothing else -- no new renderer, no new codec, no completion edits.

The notation is deliberately tiny.  Each field is a literal string with ``%``
placeholders, expanded by :func:`expand`:

======  =============================================================
``%r``  in ``path``/``header``: the root name (``--root``)
``%c``  in ``path``: the chain of path segments, joined by ``join``
``%s``  in ``key``/``index``: the segment as a literal in the target
        language -- quoted for strings, bare for numbers
``%r``  in ``key``: the segment's raw text, unquoted and unescaped
``%p``  in ``line``: the finished path
``%v``  in ``line``: the finished value
``%%``  a literal percent sign
======  =============================================================

So the original pseudo-perl notation is four strings and two switches::

    key=".{%r}"  index=".%s"  line="%p.%v"  quote="perl"
    quote_numbers=True  null="undef"

and Python assignments are::

    key="[%s]"   index="[%s]"  line="%p = %v"  quote="python"
    repr_scalars=True

Everything in this module is standard library only, and loading a template
file parses data -- it never imports or evaluates anything.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

# Keys spelled bare (`ROOT.name`) rather than subscripted (`ROOT["name"]`) by
# templates that set `bare`. Conservative on purpose: the identifier rules of
# JavaScript, jq and JSONPath differ in the corners, and this is the subset all
# of them accept.
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

ENV_PATH = "JSON_DUMP_TEMPLATES"


class TemplateError(Exception):
    """A template could not be resolved, parsed or validated."""


# --------------------------------------------------------------------------
# Quoting styles
# --------------------------------------------------------------------------

# json_dump.pl escaped only CR and LF; we add TAB, but --perl-compat pins the
# escape set to the original two so output stays byte-identical.
_MIN_ESCAPES = (("\\", "\\\\"), ("\r", "\\r"), ("\n", "\\n"), ("\t", "\\t"))
_MIN_ESCAPES_COMPAT = (("\r", "\\r"), ("\n", "\\n"))


@dataclass(frozen=True)
class QuoteOptions:
    """The three switches a quoting style is allowed to care about.

    Passed in rather than read from RenderOptions so that this module stays
    independent of the renderer -- and so a style is a pure function of its
    inputs, which is what makes the round-trip claims testable.
    """

    escape_special: bool = False
    ascii_only: bool = False
    perl_compat: bool = False


def _q_json(text: str, opts: QuoteOptions) -> str:
    """Double-quoted with JSON escapes: correct for JS, C++, Go, Rust, R, jq."""
    return json.dumps(text, ensure_ascii=opts.ascii_only)


def _q_python(text: str, opts: QuoteOptions) -> str:
    return ascii(text) if opts.ascii_only else repr(text)


def _q_perl(text: str, opts: QuoteOptions) -> str:
    """The original's quoting: double quotes, and escapes only if asked.

    json_dump.pl left control characters in the output unless -e was given,
    which is why this style honours escape_special rather than always
    escaping. Everything the other styles do is unconditional.
    """
    if opts.escape_special:
        for raw, esc in _MIN_ESCAPES_COMPAT if opts.perl_compat else _MIN_ESCAPES:
            text = text.replace(raw, esc)
    return f'"{text}"'


def _q_shell(text: str, opts: QuoteOptions) -> str:
    """Single-quoted for POSIX shells.

    Inside single quotes the only character needing work is the quote itself.
    A newline is legal there and would still split the leaf across two lines,
    so -e is honoured here for the same reason it exists at all.
    """
    if opts.escape_special:
        for raw, esc in _MIN_ESCAPES:
            text = text.replace(raw, esc)
    return "'" + text.replace("'", "'\\''") + "'"


def _q_sql(text: str, _opts: QuoteOptions) -> str:
    """SQL string literal: single quotes, and a quote is doubled, not escaped."""
    return "'" + text.replace("'", "''") + "'"


def _q_raw(text: str, opts: QuoteOptions) -> str:
    """No quoting at all -- for grep/awk-shaped output.

    -e matters more here than anywhere else: nothing else keeps one leaf on
    one line.
    """
    if opts.escape_special:
        for raw, esc in _MIN_ESCAPES:
            text = text.replace(raw, esc)
    return text


def _q_pointer(text: str, _opts: QuoteOptions) -> str:
    """RFC 6901 reference-token escaping: ~ becomes ~0, / becomes ~1."""
    return text.replace("~", "~0").replace("/", "~1")


QUOTES = {
    "json": _q_json,
    "python": _q_python,
    "perl": _q_perl,
    "shell": _q_shell,
    "sql": _q_sql,
    "raw": _q_raw,
    "pointer": _q_pointer,
}

QUOTE_HELP = {
    "json": 'double quotes, JSON escapes -- "a\\nb"',
    "python": "Python repr() -- 'a\\nb'",
    "perl": "double quotes, escapes only under -e -- the original behaviour",
    "shell": "POSIX single quotes -- 'a'\\''b'",
    "sql": "SQL single quotes, doubled inside -- 'it''s'",
    "raw": "unquoted; -e still escapes CR, LF and TAB",
    "pointer": "RFC 6901 token escaping (~0, ~1); for keys",
}


# --------------------------------------------------------------------------
# Placeholder expansion
# --------------------------------------------------------------------------


def expand(spec: str, values: dict[str, str]) -> str:
    """Substitute ``%x`` placeholders in *spec*.

    Hand-rolled rather than using %-formatting or str.format because the
    substituted text is arbitrary user data: a value containing ``%s`` or a
    brace would otherwise be re-interpreted as markup.  Unknown placeholders
    pass through untouched here and are rejected by :meth:`Template.validate`,
    which is the only place a typo in a hand-written template can be caught.
    """
    out: list[str] = []
    i, n = 0, len(spec)
    while i < n:
        ch = spec[i]
        if ch == "%" and i + 1 < n:
            nxt = spec[i + 1]
            if nxt == "%":
                out.append("%")
                i += 2
                continue
            if nxt in values:
                out.append(values[nxt])
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def placeholders(spec: str) -> set[str]:
    """The ``%x`` placeholder letters used by *spec*, ignoring ``%%``."""
    found = set()
    i, n = 0, len(spec)
    while i < n:
        if spec[i] == "%" and i + 1 < n:
            if spec[i + 1] != "%":
                found.add(spec[i + 1])
            i += 2
            continue
        i += 1
    return found


# --------------------------------------------------------------------------
# The template model
# --------------------------------------------------------------------------

# Which placeholders each field is allowed to use.
_ALLOWED = {
    "path": set("rc"),
    "key": set("sr"),
    "bare": set("sr"),
    "index": set("s"),
    "line": set("pv"),
    "header": set("r"),
}


@dataclass(frozen=True)
class Template:
    """One pseudocode notation. Every field is a literal, nothing is code."""

    name: str
    summary: str = ""
    aliases: tuple[str, ...] = ()

    # -- the path ----------------------------------------------------------
    root: str = "ROOT"
    path: str = "%r%c"  # how root and the segment chain combine
    key: str = "[%s]"  # one mapping key
    bare: str = ""  # ...when the key is an identifier (optional)
    index: str = "[%s]"  # one sequence index
    join: str = ""  # inserted *between* segments, not before
    index_base: int = 0  # 1 for Lua and R

    # -- the line ----------------------------------------------------------
    line: str = "%p = %v"
    header: tuple[str, ...] = ()  # emitted once, before the first line

    # -- values ------------------------------------------------------------
    quote: str = "json"  # quoting style for string values
    key_quote: str = ""  # ...for keys; defaults to `quote`
    quote_numbers: bool = False  # perl quoted numbers like strings
    trim_floats: bool = False  # True: 1.0 prints as 1, as Perl did
    repr_scalars: bool = False  # let Python's repr() render every scalar
    true: str = "true"
    false: str = "false"
    null: str = "null"
    empty_map: str = "{}"
    empty_seq: str = "[]"

    # -- behaviour ---------------------------------------------------------
    compat_quirks: bool = False  # honour --perl-compat (json_dump.pl bug parity)
    notes: tuple[str, ...] = ()
    source: str = "built-in"  # or the path a custom template was read from

    # -- validation --------------------------------------------------------
    @property
    def label(self) -> str:
        """How to refer to this template in an error: a path if it has one."""
        return self.name if self.source in ("built-in", "custom") else self.source

    def validate(self) -> Template:
        """Reject a template that cannot render, with a message that says why.

        Built-ins go through this in the test suite; hand-written ones go
        through it on every load, because a silently ignored typo would show
        up as subtly wrong output rather than as an error.
        """
        for style_field in ("quote", "key_quote"):
            style = getattr(self, style_field)
            if style and style not in QUOTES:
                raise TemplateError(
                    f"{self.label}: unknown {style_field} style {style!r}\n"
                    f"  choose from: {', '.join(sorted(QUOTES))}"
                )
        for field_name, allowed in _ALLOWED.items():
            specs = getattr(self, field_name)
            for spec in (specs,) if isinstance(specs, str) else specs:
                bad = placeholders(spec) - allowed
                if bad:
                    listed = ", ".join(f"%{b}" for b in sorted(bad))
                    ok = ", ".join(f"%{a}" for a in sorted(allowed)) or "none"
                    raise TemplateError(
                        f"{self.label}: {field_name} uses {listed}, which it "
                        f"cannot expand\n  allowed here: {ok} (and %% for a "
                        f"literal percent)"
                    )
        if self.index_base not in (0, 1):
            raise TemplateError(
                f"{self.label}: index_base must be 0 or 1, not {self.index_base!r}"
            )
        return self

    # -- serialisation -----------------------------------------------------
    def as_dict(self, *, full: bool = False) -> dict[str, Any]:
        """The template as plain data -- what --dump-template prints.

        Defaulted fields are omitted unless *full*, so the output doubles as a
        starting point for editing: what is listed is what this notation
        actually had to say.
        """
        out: dict[str, Any] = {}
        for f in fields(self):
            if f.name == "source":
                continue
            value = getattr(self, f.name)
            if isinstance(value, tuple):
                value = list(value)
            if (
                not full
                and f.name not in ("name", "summary")
                and value
                == (list(f.default) if isinstance(f.default, tuple) else f.default)
            ):
                continue
            out[f.name] = value
        return out


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------

TEMPLATES: tuple[Template, ...] = (
    Template(
        name="perl",
        summary='Pseudo-perl path dump: ROOT.{key}.0."value" (the original format).',
        aliases=("dump", "pseudo-perl", "pperl", "paths"),
        key=".{%r}",
        index=".%s",
        line="%p.%v",
        quote="perl",
        key_quote="raw",
        quote_numbers=True,
        trim_floats=True,
        null="undef",
        compat_quirks=True,
        notes=(
            "One line per leaf. --perl-compat reproduces the original Perl "
            "script's output byte for byte, quirks included.",
            'Numbers are quoted like strings, so 1 and "1" are '
            "indistinguishable; use the python template when types matter.",
        ),
    ),
    Template(
        name="python",
        summary='Pseudo-python path dump: ROOT["key"][0] = "value".',
        aliases=("pseudo-python", "ppython", "py"),
        quote="python",
        key_quote="python",
        repr_scalars=True,
        true="True",
        false="False",
        null="None",
        notes=(
            "Every line is a valid Python assignment; values use repr(), so "
            "quoting and escaping are exact and unambiguous.",
        ),
    ),
    Template(
        name="javascript",
        summary='JavaScript assignments: ROOT.key[0] = "value";',
        aliases=("node", "ecmascript"),
        bare=".%r",
        line="%p = %v;",
        notes=(
            "Identifier-safe keys are spelled with a dot, others subscripted.",
            "With --root json this is gron(1) output.",
        ),
    ),
    Template(
        name="cpp",
        summary='C++ nlohmann::json assignments: ROOT["key"][0] = "value";',
        aliases=("c++", "cxx", "nlohmann"),
        line="%p = %v;",
        null="nullptr",
        empty_map="json::object()",
        empty_seq="json::array()",
        notes=(
            "Written for nlohmann::json, whose operator[] chains and accepts "
            "both string keys and integer indices.",
        ),
    ),
    Template(
        name="go",
        summary='Go map/slice assignments: ROOT["key"][0] = "value"',
        aliases=("golang",),
        null="nil",
        empty_map="map[string]any{}",
        empty_seq="[]any{}",
        notes=(
            "Go's static types make a literal transcript impossible: real "
            "code needs a type assertion at every step. This is the shape, "
            "not something that compiles.",
        ),
    ),
    Template(
        name="rust",
        summary='Rust serde_json assignments: ROOT["key"][0] = json!("value");',
        aliases=("rs", "serde"),
        line="%p = json!(%v);",
        notes=(
            "serde_json::Value indexes by &str and usize, and json!() lifts a "
            "literal into a Value.",
        ),
    ),
    Template(
        name="ruby",
        summary='Ruby Hash/Array assignments: ROOT["key"][0] = "value"',
        aliases=("rb",),
        null="nil",
        notes=("Valid Ruby once the intermediate containers exist.",),
    ),
    Template(
        name="php",
        summary='PHP array assignments: $ROOT["key"][0] = "value";',
        root="$ROOT",
        line="%p = %v;",
        empty_map="[]",
        notes=(
            "PHP has one array type, so an empty map and an empty list are both [].",
            "--root replaces the whole name, sigil included: --root '$cfg'.",
        ),
    ),
    Template(
        name="lua",
        summary='Lua table assignments: ROOT["key"][1] = "value"',
        index_base=1,
        null="nil",
        empty_seq="{}",
        notes=(
            "Lua indexes from 1: the first element of an array is [1], not [0].",
            "Lua has one table type, so both empty containers are {}.",
        ),
    ),
    Template(
        name="r",
        summary='R nested-list assignments: ROOT[["key"]][[1]] <- "value"',
        aliases=("rlang", "rstats"),
        key="[[%s]]",
        index="[[%s]]",
        index_base=1,
        line="%p <- %v",
        true="TRUE",
        false="FALSE",
        null="NULL",
        empty_map="list()",
        empty_seq="list()",
        notes=(
            "R indexes from 1, and [[ ]] extracts the element rather than a "
            "one-element sublist -- which is what makes the chain work.",
            "NULL assigned into a list deletes the element in real R; here it "
            "means the input held a null.",
        ),
    ),
    Template(
        name="jq",
        summary='jq paths: .["key"][0] = "value"',
        root=".",
        notes=(
            "Each line is a jq assignment expression; jq creates the "
            "intermediate containers itself, so piping these into jq -n "
            "rebuilds the document.",
        ),
    ),
    Template(
        name="jsonpath",
        summary='JSONPath expressions: $.key[0] = "value"',
        aliases=("jpath",),
        root="$",
        bare=".%r",
        notes=("The path half of each line is a valid JSONPath query.",),
    ),
    Template(
        name="jsonpointer",
        summary='RFC 6901 pointers: /key/0 = "value"',
        aliases=("pointer", "rfc6901"),
        root="",
        key="/%s",
        index="/%s",
        key_quote="pointer",
        notes=(
            "The pointer to the whole document is the empty string, so a "
            "top-level scalar renders with an empty path.",
            "Pointers are what JSON Patch and JSON Schema errors speak, which "
            "makes this the format to grep when correlating with either.",
        ),
    ),
    Template(
        name="dotted",
        summary="Flat dotted keys, unquoted: key.0=value",
        aliases=("flat", "dots", "kv"),
        key=".%r",
        index=".%s",
        line="%p=%v",
        quote="raw",
        key_quote="raw",
        notes=(
            "Nothing is quoted, so this is the friendliest target for cut, "
            "awk and grep -- and the most ambiguous: a value containing = or "
            "a newline is indistinguishable from structure. Pair it with -e.",
        ),
    ),
    Template(
        name="shell",
        summary="A bash associative array: ROOT[key.0]='value'",
        aliases=("bash", "sh"),
        path="%r[%c]",
        key="%r",
        index="%s",
        join=".",
        line="%p=%v",
        header=("declare -A %r",),
        quote="shell",
        key_quote="raw",
        notes=(
            "Sourceable into bash 4+: the header declares the array, and "
            "single quoting makes every value literal.",
            "The nesting is flattened into the key, because shell arrays have "
            "no nesting to preserve it with.",
        ),
    ),
)

BY_NAME: dict[str, Template] = {}
for _t in TEMPLATES:
    BY_NAME[_t.name] = _t
    for _a in _t.aliases:
        BY_NAME.setdefault(_a, _t)

DEFAULT = BY_NAME["perl"]


def names() -> list[str]:
    return [t.name for t in TEMPLATES]


# --------------------------------------------------------------------------
# Custom templates
# --------------------------------------------------------------------------


def search_path() -> list[Path]:
    """Directories searched for a template named on the command line.

    In order: $JSON_DUMP_TEMPLATES (os.pathsep-separated), then the XDG
    config directory.  Built-ins always win, so a file cannot quietly
    redefine `perl` for a script that expected it.
    """
    out: list[Path] = []
    env = os.environ.get(ENV_PATH, "")
    out += [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    xdg = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    out.append(Path(xdg) / "leafdump" / "templates")
    return out


def _parse(raw: bytes, path: Path) -> dict[str, Any]:
    """Read a template file as JSON, or as TOML if it is named .toml."""
    if path.suffix.lower() == ".toml":
        try:
            import tomllib
        except ModuleNotFoundError as exc:  # pragma: no cover - 3.10 and older
            raise TemplateError(
                f"{path}: TOML templates need Python 3.11+ ({exc}); "
                "save it as JSON instead"
            ) from None
        try:
            return tomllib.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise TemplateError(f"{path}: not valid TOML: {exc}") from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise TemplateError(f"{path}: not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise TemplateError(
            f"{path}: a template must be an object, not {type(data).__name__}"
        )
    return data


_FIELDS = {f.name: f for f in fields(Template)}
_TUPLE_FIELDS = {"aliases", "header", "notes"}
_BOOL_FIELDS = {"quote_numbers", "trim_floats", "repr_scalars", "compat_quirks"}


def from_dict(
    data: dict[str, Any], *, name: str = "", source: str = "custom"
) -> Template:
    """Build a Template from parsed file data, checking every field.

    ``base`` names a built-in to start from, so a custom template is usually
    three lines: what to inherit and the two things to change.
    """
    data = dict(data)
    base_name = data.pop("base", None)
    if base_name is not None:
        if not isinstance(base_name, str) or base_name not in BY_NAME:
            raise TemplateError(
                f"{source}: base {base_name!r} is not a built-in template\n"
                f"  choose from: {', '.join(names())}"
            )
        # Inherit behaviour, never identity: a template built on javascript
        # is not also called "node", and the parent's notes describe the
        # parent. Anything the file states explicitly still wins below.
        base = replace(
            BY_NAME[base_name],
            aliases=(),
            notes=(),
            summary=f"custom template based on {base_name}",
        )
    else:
        base = Template(name=name or "custom")

    unknown = sorted(set(data) - set(_FIELDS) - {"source"})
    if unknown:
        known = ", ".join(k for k in _FIELDS if k != "source")
        raise TemplateError(
            f"{source}: unknown field{'s' if len(unknown) > 1 else ''}: "
            f"{', '.join(unknown)}\n  known fields: {known}"
        )
    data.pop("source", None)

    changes: dict[str, Any] = {}
    for key, value in data.items():
        if key in _TUPLE_FIELDS:
            if isinstance(value, str) or not isinstance(value, (list, tuple)):
                raise TemplateError(f"{source}: {key} must be a list of strings")
            changes[key] = tuple(str(v) for v in value)
        elif key in _BOOL_FIELDS:
            if not isinstance(value, bool):
                raise TemplateError(f"{source}: {key} must be true or false")
            changes[key] = value
        elif key == "index_base":
            if isinstance(value, bool) or not isinstance(value, int):
                raise TemplateError(f"{source}: index_base must be 0 or 1")
            changes[key] = value
        else:
            if not isinstance(value, str):
                raise TemplateError(
                    f"{source}: {key} must be a string, not {type(value).__name__}"
                )
            changes[key] = value

    if name and "name" not in changes:
        changes["name"] = name
    changes["source"] = source
    return replace(base, **changes).validate()


def load_file(path: Path) -> Template:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise TemplateError(f"cannot read template {path}: {exc}") from None
    return from_dict(_parse(raw, path), name=path.stem, source=str(path))


def _looks_like_path(spec: str) -> bool:
    """True if *spec* is meant as a filename rather than a template name."""
    seps = [os.sep, *([os.altsep] if os.altsep else [])]
    return (
        any(sep in spec for sep in seps)
        or spec.startswith((".", "~"))
        or spec.lower().endswith((".json", ".toml"))
    )


def resolve(spec: str) -> Template:
    """Resolve a --template argument: a built-in name, an alias, or a file.

    Built-ins are checked first and cannot be shadowed.  A bare name that is
    not built in is looked for as NAME.json and then NAME.toml in every
    directory of :func:`search_path`; anything that looks like a path is read
    directly, so an unreadable file reports that rather than "unknown
    template".
    """
    key = spec.strip()
    if not _looks_like_path(key):
        found = BY_NAME.get(key.lower())
        if found is not None:
            return found

    candidate = Path(key).expanduser()
    if _looks_like_path(key) or candidate.exists():
        if not candidate.exists():
            raise TemplateError(f"no such template file: {key}")
        return load_file(candidate)

    for directory in search_path():
        for suffix in (".json", ".toml"):
            path = directory / f"{key}{suffix}"
            if path.exists():
                return load_file(path)

    raise TemplateError(
        f"unknown template {spec!r}\n"
        f"  built-in: {', '.join(names())}\n"
        f"  searched: {', '.join(str(d) for d in search_path())}\n"
        f"  see `leafdump --list-templates`"
    )


def discovered() -> list[Template]:
    """Every custom template found on the search path, name-sorted.

    Failures are skipped rather than raised: one broken file in a directory
    must not make --list-templates unusable, and the error is reported in
    full the moment that template is actually asked for.
    """
    out: dict[str, Template] = {}
    for directory in search_path():
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in (".json", ".toml"):
                continue
            if path.stem in BY_NAME or path.stem in out:
                continue
            try:
                out[path.stem] = load_file(path)
            except TemplateError:
                continue
    return [out[k] for k in sorted(out)]
