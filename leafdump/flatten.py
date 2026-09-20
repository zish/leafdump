# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

"""Depth-first flattening of nested data into one line per leaf.

One traversal (:func:`walk_iter`) feeds one renderer
(:func:`render_template`), which spells each leaf according to a
:class:`~leafdump.templates.Template` -- a table of literal strings, not
code.  Two of those templates are the historical pair::

    perl     ROOT.{definitions}.{vendor}.{type}."object"
    python   ROOT["definitions"]["vendor"]["type"] = "object"

and the rest (javascript, cpp, go, rust, r, jq, ...) are the same loop with
different strings.  See :mod:`leafdump.templates` for the field list and for
how a user-supplied template is loaded.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .templates import (
    BY_NAME,
    DEFAULT,
    IDENT,
    QUOTES,
    TEMPLATES,
    QuoteOptions,
    Template,
    expand,
)

# --------------------------------------------------------------------------
# Traversal
# --------------------------------------------------------------------------

# Sentinels distinguishing "this leaf is an empty container" from a real value.
EMPTY_MAP = object()
EMPTY_SEQ = object()


@dataclass(frozen=True)
class Seg:
    """One step of a path: a mapping key or a sequence index."""

    value: Any
    is_index: bool


def walk(
    obj: Any, *, sort_keys: bool = False, _path: tuple[Seg, ...] = ()
) -> Iterator[tuple[tuple[Seg, ...], Any]]:
    """Yield ``(path, leaf)`` for every leaf, depth-first, in document order.

    Empty containers yield the EMPTY_MAP/EMPTY_SEQ sentinel rather than being
    skipped -- json_dump.pl silently dropped them, which loses information
    (``--perl-compat`` restores the old behaviour).

    Recursion is explicit rather than using Python's call stack for deep
    inputs; see :func:`walk_iter`.
    """
    yield from walk_iter(obj, sort_keys=sort_keys)


def walk_iter(
    obj: Any, *, sort_keys: bool = False
) -> Iterator[tuple[tuple[Seg, ...], Any]]:
    """Iterative depth-first walk -- no Python recursion limit to hit.

    Nesting depth in real-world data (deeply chained JSON schemas, packet
    captures) routinely exceeds the default 1000-frame limit, and a
    RecursionError mid-dump would truncate output silently.
    """
    # Stack of (path, node). Reversed pushes keep output in document order.
    stack: list[tuple[tuple[Seg, ...], Any]] = [((), obj)]
    while stack:
        path, node = stack.pop()

        if _is_map(node):
            if not node:
                yield path, EMPTY_MAP
                continue
            keys = list(node.keys())
            if sort_keys:
                keys.sort(key=_sort_key)
            for k in reversed(keys):
                stack.append(((*path, Seg(k, False)), node[k]))

        elif _is_seq(node):
            if not node:
                yield path, EMPTY_SEQ
                continue
            for i in range(len(node) - 1, -1, -1):
                stack.append(((*path, Seg(i, True)), node[i]))

        else:
            yield path, node


def _is_map(o: Any) -> bool:
    return isinstance(o, Mapping)


def _is_seq(o: Any) -> bool:
    # str/bytes are Sequences but are leaves, not containers.
    return isinstance(o, (list, tuple)) or (
        isinstance(o, Sequence) and not isinstance(o, (str, bytes, bytearray))
    )


def _sort_key(k: Any):
    """Sort mixed-type keys without raising: order by (type name, repr)."""
    if isinstance(k, str):
        return (0, k, "")
    if isinstance(k, bool):
        return (1, "", repr(k))
    if isinstance(k, (int, float)):
        return (2, "", f"{float(k):030.10f}")
    return (3, type(k).__name__, repr(k))


# --------------------------------------------------------------------------
# Renderer options
# --------------------------------------------------------------------------


@dataclass
class RenderOptions:
    """What the renderer needs beyond the data and the template itself."""

    # None means "whatever the template calls its root": ROOT for most, $ROOT
    # for php, "." for jq. An explicit --root always wins.
    root: str | None = None
    escape_special: bool = False
    perl_compat: bool = False
    sort_keys: bool = False
    ascii_only: bool = False
    template: Template | None = None

    def quoting(self) -> QuoteOptions:
        return QuoteOptions(
            escape_special=self.escape_special,
            ascii_only=self.ascii_only,
            perl_compat=self.perl_compat,
        )


# --------------------------------------------------------------------------
# Scalars
# --------------------------------------------------------------------------


def _text(leaf: Any) -> str:
    """A leaf as plain text, before any quoting."""
    if isinstance(leaf, str):
        return leaf
    if isinstance(leaf, (bytes, bytearray)):
        return leaf.decode("utf-8", "backslashreplace")
    return str(leaf)


def _number_text(leaf: Any, trim_floats: bool) -> str:
    if (
        trim_floats
        and isinstance(leaf, float)
        and leaf.is_integer()
        and abs(leaf) < 1e16
    ):
        # Match Perl/JSON number stringification: 1.0 prints as 1.
        return str(int(leaf))
    return str(leaf)


def literal(leaf: Any, tmpl: Template, opts: RenderOptions, style: str = "") -> str:
    """One scalar, spelled the way *tmpl*'s language spells it.

    Used for values and for mapping keys alike -- a key is just a scalar in a
    different position, and a template that quotes strings with repr() has to
    quote its keys the same way for the line to parse.
    """
    if tmpl.repr_scalars:
        # The python template delegates wholesale: repr() already knows how to
        # spell every scalar Python has, including the ones with no equivalent
        # anywhere else (Decimal, complex, a set that arrived from a codec).
        return ascii(leaf) if opts.ascii_only else repr(leaf)

    if leaf is True:
        return tmpl.true
    if leaf is False:
        return tmpl.false
    if leaf is None:
        return tmpl.null

    quote = QUOTES[style or tmpl.quote]
    if isinstance(leaf, (int, float)):
        text = _number_text(leaf, tmpl.trim_floats)
        return quote(text, opts.quoting()) if tmpl.quote_numbers else text
    return quote(_text(leaf), opts.quoting())


# --------------------------------------------------------------------------
# The renderer
# --------------------------------------------------------------------------


def render_template(
    obj: Any, opts: RenderOptions, tmpl: Template | None = None
) -> Iterator[str]:
    """Render every leaf of *obj* as one line of *tmpl*'s notation.

    This is the only renderer.  perl, python and every language added since
    are the same loop reading different data, which is what keeps a new
    notation to one `Template(...)` entry rather than a new code path with its
    own escaping bugs.
    """
    tmpl = tmpl or opts.template or DEFAULT
    root = tmpl.root if opts.root is None else opts.root
    key_style = tmpl.key_quote or tmpl.quote
    quirks = opts.perl_compat and tmpl.compat_quirks

    for line in tmpl.header:
        yield expand(line, {"r": root})

    for path, leaf in walk_iter(obj, sort_keys=opts.sort_keys):
        segments = []
        for seg in path:
            if seg.is_index:
                segments.append(
                    expand(tmpl.index, {"s": str(seg.value + tmpl.index_base)})
                )
            else:
                raw = _text(seg.value)
                spec = tmpl.bare if tmpl.bare and IDENT.match(raw) else tmpl.key
                segments.append(
                    expand(
                        spec,
                        {
                            "s": literal(seg.value, tmpl, opts, key_style),
                            "r": raw,
                        },
                    )
                )
        rendered_path = expand(
            tmpl.path,
            {
                "r": root,
                "c": tmpl.join.join(segments),
            },
        )

        if leaf is EMPTY_MAP:
            if quirks:
                continue  # json_dump.pl emitted nothing for empty containers
            value = tmpl.empty_map
        elif leaf is EMPTY_SEQ:
            if quirks:
                continue
            value = tmpl.empty_seq
        elif leaf is None and quirks:
            # Perl stringified undef to "", making null indistinguishable from
            # the empty string. Only --perl-compat asks for that bug back.
            value = '""'
        else:
            value = literal(leaf, tmpl, opts)

        yield expand(tmpl.line, {"p": rendered_path, "v": value})


def _renderer(name: str) -> Callable[[Any, RenderOptions], Iterator[str]]:
    tmpl = BY_NAME[name]

    def render(obj: Any, opts: RenderOptions) -> Iterator[str]:
        # opts.template wins: --template selects a notation the format name
        # cannot express, including one loaded from a file.
        return render_template(obj, opts, opts.template or tmpl)

    render.__name__ = f"render_{name}"
    render.__doc__ = tmpl.summary
    return render


# One entry per built-in template, so `--to go` reaches a renderer by the same
# name lookup that has always resolved `--to perl`.
RENDERERS: dict[str, Callable[[Any, RenderOptions], Iterator[str]]] = {
    t.name: _renderer(t.name) for t in TEMPLATES
}

# The two originals, importable under their historical names.
render_perl = RENDERERS["perl"]
render_python = RENDERERS["python"]
