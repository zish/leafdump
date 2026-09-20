# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

"""Command-line interface for leafdump."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__, registry, templates
from .codecs import CodecError, Context, FormatUnavailable, codec_for
from .flatten import RenderOptions, render_template
from .merge import LIST_STRATEGIES, MAP_STRATEGIES, dedup, merge_all, wrap
from .registry import RENDER, Format, UnknownFormat, lookup
from .templates import Template, TemplateError

PROG = "leafdump"


class Fail(SystemExit):
    def __init__(self, message: str, code: int = 2):
        print(f"{PROG}: error: {message}", file=sys.stderr)
        super().__init__(code)


# --------------------------------------------------------------------------
# Format selection
# --------------------------------------------------------------------------


def resolve_format(name: str, direction: str) -> Format:
    """Look up a format and confirm it can be used in *direction*."""
    try:
        fmt = lookup(name)
    except UnknownFormat:
        raise Fail(
            f"unknown format {name!r}\n"
            f"  known formats: {', '.join(registry.names())}\n"
            f"  run `{PROG} --list-formats` for details"
        ) from None

    if direction == "in" and not fmt.reads:
        raise Fail(f"format {fmt.name!r} is output-only and cannot be read")
    if direction == "out" and not fmt.writes:
        raise Fail(f"format {fmt.name!r} is input-only and cannot be written")

    if not fmt.available_for(direction):
        verb = "reading" if direction == "in" else "writing"
        raise Fail(
            f"support for {verb} {fmt.name!r} is not installed.\n"
            f"  {fmt.install_hint(direction)}"
        )
    return fmt


def choose_output_format(requested: str | None, tmpl: Template | None) -> str:
    """Reconcile --output-format with --template.

    The two overlap: every built-in template is also an output format, so
    `-t go` and `--template go` mean the same thing.  They can also disagree,
    and the disagreement worth catching is `--to json --template go`, where
    one of the two flags was going to be silently ignored.
    """
    if tmpl is None:
        return requested or "perl"
    if requested is not None:
        fmt = resolve_format(requested, "out")
        if fmt.kind != RENDER:
            raise Fail(
                f"--template {tmpl.name} renders a pseudocode path dump, but "
                f"--to {fmt.name} asks for {fmt.name} output.\n"
                f"  drop one of the two: --template selects a notation, "
                f"--to selects a format"
            )
    # `pseudocode` is the format whose notation comes from --template, which
    # is the only way a template loaded from a file can be reached at all.
    return "pseudocode"


# Magic-byte / shape sniffing, tried in order. Deliberately conservative:
# guessing wrong on a binary format produces confusing garbage, so ambiguous
# input falls through to JSON and the user is told to pass --input-format.
def sniff(raw: bytes, hint_path: Path | None = None) -> str:
    if hint_path is not None:
        for suffix in reversed(hint_path.suffixes[-2:]):
            fmt = registry.by_extension(suffix)
            if fmt is not None and fmt.reads:
                return fmt.name

    if not raw:
        return "json"

    # BSON: little-endian int32 document length covering the whole buffer,
    # terminated by NUL. A strong, cheap signal.
    if len(raw) >= 5 and raw[-1] == 0:
        size = int.from_bytes(raw[:4], "little", signed=True)
        if size == len(raw):
            return "bson"

    if raw[:4] == b"Obj\x01":  # Avro object container file magic
        return "avro"

    head = raw.lstrip()[:1]
    if head in (b"{", b"["):
        text = raw.decode("utf-8", "replace")
        if text.count("\n{") > 1 or text.count("\n[") > 1:
            first = text.splitlines()[0].strip()
            if first.endswith(("}", "]")):
                return "jsonl"
        return "json"
    if raw.lstrip()[:3] == b"---":
        return "yaml"
    if raw[:1] in (b'"', b"'") or head in (b"t", b"f", b"n", b"-") or head.isdigit():
        return "json"

    # Printable-ASCII-dominant payloads are some text format; YAML is the most
    # forgiving reader for key: value shapes.
    sample = raw[:4096]
    printable = sum(1 for b in sample if 0x20 <= b < 0x7F or b in (9, 10, 13))
    if printable / max(len(sample), 1) > 0.95:
        return "yaml" if lookup("yaml").can_read else "json"
    return "msgpack"


# --------------------------------------------------------------------------
# Help rendering
# --------------------------------------------------------------------------


def format_table(direction: str = "any", porcelain: bool = False) -> str:
    """--list-formats.

    The fifteen pseudocode notations are formats in every sense -- each has a
    name, a codec and a --help-format entry -- but listing all fifteen in full
    would double the length of this table and bury the data formats it exists
    to describe. In prose they collapse to one block pointing at
    --list-templates; in porcelain every one is still its own row, because
    that is what the shell completions read.
    """
    rows = []
    template_names = {t.name for t in templates.TEMPLATES}
    for fmt in registry.FORMATS:
        if direction == "in" and not fmt.reads:
            continue
        if direction == "out" and not fmt.writes:
            continue
        if not porcelain and fmt.name in template_names:
            continue  # listed under `pseudocode`, below
        if porcelain:
            rows.append(
                "\t".join(
                    (
                        fmt.name,
                        fmt.live_direction,
                        fmt.kind,
                        "yes" if fmt.available_for(direction) else "no",
                        ",".join(fmt.aliases),
                        fmt.summary,
                    )
                )
            )
            continue

        status = "available" if fmt.available_for(direction) else "NOT INSTALLED"
        mark = " " if fmt.available_for(direction) else "!"
        rows.append(
            f"{mark} {fmt.name:<16} {fmt.live_direction:<7} {fmt.kind:<7} {status}"
        )
        rows.append(f"    {fmt.summary}")
        if fmt.aliases:
            rows.append(f"    aliases: {', '.join(fmt.aliases)}")
        if fmt.name == "pseudocode":
            rows.append(f"    notations: {', '.join(templates.names())}")
            rows.append(
                "    Each is also an output format of its own name. "
                f"`{PROG} --list-templates`"
            )
            rows.append(
                "    shows an example of each, and how to write one of your own."
            )
        if not fmt.available_for(direction):
            rows.append(f"    enable with: {fmt.install_hint(direction)}")
        rows.append("")
    return "\n".join(rows)


# A document small enough to show inline and shaped enough to show the parts
# that differ between notations: a key, an index, a nested key, a string.
SAMPLE: dict = {"hosts": [{"name": "web-01"}]}


def template_example(tmpl: Template) -> str:
    """One rendered line, so a notation can be recognised rather than read."""
    lines = list(render_template(SAMPLE, RenderOptions(), tmpl))
    return lines[-1] if lines else ""


def resolve_template(spec: str) -> Template:
    try:
        return templates.resolve(spec)
    except TemplateError as exc:
        raise Fail(str(exc)) from None


def template_table(porcelain: bool = False) -> str:
    """--list-templates: built-ins first, then whatever the search path holds."""
    rows = []
    for tmpl in [*templates.TEMPLATES, *templates.discovered()]:
        if porcelain:
            rows.append(
                "\t".join(
                    (
                        tmpl.name,
                        tmpl.source,
                        ",".join(tmpl.aliases),
                        template_example(tmpl),
                        tmpl.summary,
                    )
                )
            )
            continue
        rows.append(f"  {tmpl.name:<14} {tmpl.summary}")
        rows.append(f"  {'':<14} {template_example(tmpl)}")
        if tmpl.aliases:
            rows.append(f"  {'':<14} aliases: {', '.join(tmpl.aliases)}")
        if tmpl.source != "built-in":
            rows.append(f"  {'':<14} from: {tmpl.source}")
        rows.append("")
    if not porcelain:
        rows.append("Select one with --template NAME, or --to NAME for the built-ins.")
        rows.append(f"Write your own: `{PROG} --help-template` explains how.")
    return "\n".join(rows)


def template_detail(tmpl: Template) -> str:
    """--help-template NAME: what this notation does, field by field."""
    out = [f"{tmpl.name} -- {tmpl.summary}", ""]
    out.append(f"  source     : {tmpl.source}")
    if tmpl.aliases:
        out.append(f"  aliases    : {', '.join(tmpl.aliases)}")
    out.append("")
    out.append("  example:")
    for line in render_template(SAMPLE, RenderOptions(), tmpl):
        out.append(f"    {line}")
    out.append("")
    out.append("  fields that differ from the defaults:")
    for key, value in tmpl.as_dict().items():
        if key in ("name", "summary", "aliases", "notes"):
            continue
        out.append(f"    {key:<14} {value!r}")
    if tmpl.notes:
        out.append("")
        out.append("  notes:")
        for note in tmpl.notes:
            out.append(_wrap(note, "    - ", "      "))
    out.append("")
    out.append(
        f"  copy it as a starting point: {PROG} --dump-template "
        f"{tmpl.name} > my-notation.json"
    )
    return "\n".join(out)


def template_guide() -> str:
    """--help-template with no name: how to write one.

    Kept here rather than only in the manpage because the people most likely
    to want it are the ones who just ran --list-templates and found nothing
    that fits.
    """
    fields = [
        ("root", "name of the root node; --root overrides it"),
        ("path", "how root and the path chain combine (%r %c)"),
        ("key", "one mapping key (%s quoted, %r raw)"),
        ("bare", "alternative key form for identifier-safe keys"),
        ("index", "one sequence index (%s)"),
        ("join", "string inserted between segments, not before the first"),
        ("index_base", "0, or 1 for languages that count from one"),
        ("line", "the whole line (%p path, %v value)"),
        ("header", "list of lines emitted once, before the first leaf (%r)"),
        ("quote", "quoting style for string values (see below)"),
        ("key_quote", "quoting style for keys; defaults to quote"),
        ("quote_numbers", "true to quote numbers like strings, as perl did"),
        ("trim_floats", "true to print 1.0 as 1"),
        ("repr_scalars", "true to spell every scalar with Python's repr()"),
        ("true / false / null", "the three literals"),
        ("empty_map / empty_seq", "literals for containers with nothing in them"),
        ("compat_quirks", "true to honour --perl-compat in this notation"),
        ("name, summary, aliases", "identity: what --list-templates shows"),
        ("notes", "caveats, printed by --help-template NAME"),
    ]
    out = [
        "WRITING A TEMPLATE",
        "",
        "  A template is a JSON (or TOML) file of literal strings -- no code, nothing",
        "  is imported or evaluated. Start from the closest built-in:",
        "",
        f"    $ {PROG} --dump-template javascript > kotlin.json",
        "",
        '  or write the differences and inherit the rest with "base":',
        "",
        "    {",
        '      "base": "javascript",',
        '      "name": "kotlin",',
        '      "summary": "Kotlin map/list assignments",',
        '      "null": "null",',
        '      "line": "%p = %v"',
        "    }",
        "",
        "  Then use it by path, or by name once it is on the search path:",
        "",
        f"    $ {PROG} --template ./kotlin.json data.json",
        f"    $ {PROG} --template kotlin data.json",
        "",
        "FIELDS",
        "",
    ]
    for name, help_text in fields:
        out.append(f"  {name:<24} {help_text}")
    out += [
        "",
        "PLACEHOLDERS",
        "",
        "  %r   root name (in path, header) or a key's raw text (in key, bare)",
        "  %c   the chain of rendered path segments (in path)",
        "  %s   the segment as a literal in the target language (in key, index)",
        "  %p   the finished path (in line)",
        "  %v   the finished value (in line)",
        "  %%   a literal percent sign",
        "",
        "QUOTING STYLES",
        "",
    ]
    for name, help_text in templates.QUOTE_HELP.items():
        out.append(f"  {name:<10} {help_text}")
    out += [
        "",
        "SEARCH PATH",
        "",
        "  A bare --template NAME is looked up as NAME.json then NAME.toml in:",
    ]
    for directory in templates.search_path():
        out.append(f"    {directory}")
    out += [
        "",
        f"  Set {templates.ENV_PATH} to add directories (os.pathsep-separated).",
        "  Built-in names are always found first and cannot be shadowed.",
        "",
        f"  See also: {PROG} --list-templates, {PROG} --help-template NAME",
    ]
    return "\n".join(out)


def format_detail(name: str) -> str:
    try:
        fmt = lookup(name)
    except UnknownFormat:
        raise Fail(f"unknown format {name!r}") from None

    out = [f"{fmt.name} -- {fmt.summary}", ""]
    out.append(f"  declared directions : {fmt.direction}")
    out.append(f"  usable right now    : {fmt.live_direction}")
    out.append(f"  encoding            : {fmt.kind}")
    if fmt.aliases:
        out.append(f"  aliases             : {', '.join(fmt.aliases)}")
    if fmt.extensions:
        out.append(f"  auto-detected on    : {', '.join(fmt.extensions)}")
    out.append(f"  native multi-doc    : {'yes' if fmt.streams else 'no'}")

    if fmt.groups or fmt.read_groups or fmt.write_groups:
        out.append("")
        out.append("  dependencies:")
        for label, groups in (
            ("any use", fmt.groups),
            ("reading", fmt.read_groups),
            ("writing", fmt.write_groups),
        ):
            for g in groups:
                state = "installed" if g.satisfied() else "MISSING"
                alts = " or ".join(d.pip for d in g.alternatives)
                out.append(f"    {label:<8} {alts:<28} [{state}]")
    if not fmt.available_for("any"):
        out.append("")
        out.append(f"  enable with: {fmt.install_hint('any')}")

    if fmt.notes:
        out.append("")
        out.append("  notes:")
        for n in fmt.notes:
            out.append(_wrap(n, "    - ", "      "))
    return "\n".join(out)


def _wrap(text: str, first: str, rest: str, width: int = 76) -> str:
    import textwrap

    return textwrap.fill(
        text, width=width, initial_indent=first, subsequent_indent=rest
    )


class _Formatter(argparse.RawDescriptionHelpFormatter):
    """Keep epilog layout intact but still wrap option help sensibly."""

    def __init__(self, prog):
        super().__init__(
            prog,
            max_help_position=32,
            width=min(
                100, (os.environ.get("COLUMNS") and int(os.environ["COLUMNS"])) or 100
            ),
        )


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    readable = ", ".join(registry.names(reads=True, available_only=True))
    # The notations get their own section below; repeating fifteen names here
    # would bury the four or five data formats this line exists to report.
    writable = ", ".join(
        n
        for n in registry.names(writes=True, available_only=True)
        if n not in {t.name for t in templates.TEMPLATES}
    )
    disabled = [f for f in registry.FORMATS if not f.available]

    epilog = [
        "FORMATS AVAILABLE NOW",
        f"  input : {readable}",
        f"  output: {writable}",
    ]
    if disabled:
        epilog += [
            "",
            "FORMATS DISABLED (missing Python packages)",
        ]
        for f in disabled:
            epilog.append(f"  {f.name:<16} enable with: {f.install_hint()}")
        epilog.append("")
        epilog.append(
            "  Help text for a disabled format is withheld because its "
            "behaviour depends"
        )
        epilog.append(
            "  on the library that is not installed. Run "
            f"`{PROG} --help-format NAME` for details."
        )
    epilog += [
        "",
        "PSEUDOCODE TEMPLATES (the path dump notations)",
        "  " + ", ".join(templates.names()),
        "  Select with --template NAME (or -t NAME); --list-templates shows an",
        "  example of each, and --help-template explains how to write your own.",
        "",
        "EXAMPLES",
        "  leafdump data.json                       # pseudo-perl path dump",
        "  leafdump -t python data.json             # pseudo-python assignments",
        "  leafdump -T go data.json                 # ...or Go, R, jq, cpp, ...",
        "  leafdump -T ./mine.json data.json        # a template of your own",
        "  leafdump -t yaml a.json b.json --dedup   # merge, dedupe, emit YAML",
        "  cat packets.jsonl | leafdump -m -r       # newest packet first",
        "  leafdump --list-formats",
    ]

    p = argparse.ArgumentParser(
        prog=PROG,
        formatter_class=_Formatter,
        description=(
            "Make nested data greppable: every value on its own line, with "
            "the full path\n"
            "to it -- so grep, less, cut and awk can read a config file, an "
            "API response\n"
            "or a wall of log records, including one that arrives as a single "
            "enormous\n"
            "line with no newlines of its own."
        ),
        epilog="\n".join(epilog),
        add_help=False,
    )

    pos = p.add_argument_group("positional arguments")
    pos.add_argument(
        "files",
        nargs="*",
        metavar="FILE",
        help="Input files. Use - for standard input; with no FILE, reads "
        "standard input. Multiple files are merged into a single "
        "structure (see MERGING), unless --no-merge is given.",
    )

    inp = p.add_argument_group("input")
    inp.add_argument(
        "-i",
        "-f",
        "--input-format",
        "--from",
        dest="input_format",
        metavar="FMT",
        default="auto",
        help="Format of the input. Default: auto, which detects from the file "
        "extension and then from the leading bytes. Pass an explicit "
        "format when reading binary data from a pipe.",
    )
    inp.add_argument(
        "-m",
        "--multipacket",
        action="store_true",
        help="Treat each line of the input as a separate document rather than "
        "reading the whole stream as one. Formats with native "
        "multi-document support (YAML, JSONL, MessagePack, CBOR, BSON, "
        "Avro) always split on their own record boundaries.",
    )
    inp.add_argument(
        "-r",
        "--reverse",
        action="store_true",
        help="Process documents in reverse order, newest first -- the usual "
        "want when the input is an append-only log. Implies "
        "--multipacket.",
    )
    inp.add_argument(
        "--input-encoding",
        metavar="ENC",
        default="utf-8",
        help="Character encoding for text input formats. Default: utf-8.",
    )

    out = p.add_argument_group("output")
    out.add_argument(
        "-o",
        "-t",
        "--output-format",
        "--to",
        dest="output_format",
        metavar="FMT",
        default=None,
        help="Format to emit. Default: perl (the original path dump). Any "
        "pseudocode template name works here too (python, javascript, "
        "go, r, ...), as does any writable data format, which converts "
        "instead of flattening.",
    )
    out.add_argument(
        "-O",
        "--output",
        metavar="FILE",
        default="-",
        help="Write to FILE instead of standard output. Binary formats "
        "refuse to write to a terminal.",
    )
    out.add_argument(
        "--indent",
        type=int,
        metavar="N",
        default=2,
        help="Indentation width for structured text output. Default: 2.",
    )
    out.add_argument(
        "--compact",
        action="store_true",
        help="Emit structured output on a single line with no indentation. "
        "Overrides --indent.",
    )
    out.add_argument(
        "-s",
        "--sort-keys",
        action="store_true",
        help="Sort mapping keys. Python preserves insertion order, so output "
        "is already deterministic; this makes it comparable across "
        "inputs whose keys were written in different orders.",
    )
    out.add_argument(
        "--ascii",
        action="store_true",
        help="Escape all non-ASCII characters in text output.",
    )
    out.add_argument(
        "--null-policy",
        choices=("keep", "drop", "empty"),
        default="keep",
        metavar="POLICY",
        help="What to do with null values when the target format cannot "
        "represent them (TOML, NestedText): keep (fail or coerce), "
        "drop the key entirely, or write an empty string. "
        "Default: keep.",
    )
    out.add_argument(
        "--avro-schema",
        metavar="FILE",
        help="Read an Avro writer schema (JSON) from FILE instead of "
        "inferring one from the data. Inference is approximate; supply "
        "a schema for anything load-bearing.",
    )

    ren = p.add_argument_group("path dump (pseudocode output)")
    ren.add_argument(
        "-T",
        "--template",
        metavar="NAME|FILE",
        help="Pseudocode notation to render paths in: a built-in name "
        f"({', '.join(templates.names())}), or a JSON/TOML template file "
        "of your own. See --list-templates and --help-template. "
        "Default: perl.",
    )
    ren.add_argument(
        "-e",
        "--escape-special",
        action="store_true",
        help="Escape carriage returns, line feeds and tabs in leaf values so "
        "every leaf stays on one line. Only affects templates that quote "
        "minimally (perl, dotted, shell); the rest always escape.",
    )
    ren.add_argument(
        "--perl-compat",
        action="store_true",
        help="Restore the quirks of the original Perl script this tool grew "
        "out of, reproducing its output byte for byte: null renders as "
        '"" rather than undef, empty maps and arrays emit no line at '
        "all, and only CR and LF are escaped by -e. Only the perl "
        "notation claims that compatibility; other templates ignore "
        "this flag.",
    )
    ren.add_argument(
        "--root",
        metavar="NAME",
        default=None,
        help="Name of the root node in dumped paths. Default: whatever the "
        "template calls it (ROOT for most, $ROOT for php, . for jq).",
    )

    mrg = p.add_argument_group("merging")
    mrg.add_argument(
        "--no-merge",
        action="store_true",
        help="Emit each input document separately instead of merging them "
        "into one structure.",
    )
    mrg.add_argument(
        "--merge-strategy",
        choices=MAP_STRATEGIES,
        default="deep",
        metavar="STRATEGY",
        help="How to combine mappings that define the same key: "
        "deep (recurse, later wins at the leaves), shallow (later "
        "document replaces the whole value), last, first, or collect "
        "(gather conflicting values into a deduplicated list). "
        "Default: deep.",
    )
    mrg.add_argument(
        "--list-merge",
        choices=LIST_STRATEGIES,
        default="concat",
        metavar="STRATEGY",
        help="How to combine arrays at the same path: concat (append), "
        "union (append, dropping structural duplicates), replace (later "
        "wins), keep (earlier wins), or index (merge element-wise by "
        "position). Default: concat.",
    )
    mrg.add_argument(
        "-d",
        "--dedup",
        action="store_true",
        help="After merging, remove structurally identical elements from "
        "every array, keeping the first occurrence. Comparison is by "
        "value and type, so 1, 1.0 and true stay distinct.",
    )
    mrg.add_argument(
        "--wrap-key",
        choices=("none", "basename", "stem", "path", "index"),
        default="none",
        metavar="MODE",
        help="Instead of merging, key each document by its source: basename, "
        "stem (basename without extension), path, or index. Also useful "
        "to give a top-level mapping to formats that require one "
        "(TOML, BSON, Avro). Default: none.",
    )

    info = p.add_argument_group("information")
    info.add_argument(
        "-h", "--help", action="store_true", help="Show this help and exit."
    )
    info.add_argument(
        "-L",
        "--list-formats",
        action="store_true",
        help="List every known format with its availability, then exit.",
    )
    info.add_argument(
        "--help-format",
        metavar="FMT",
        help="Explain one format in detail -- dependencies, caveats and "
        "round-trip limitations -- then exit. Works for formats that "
        "are not installed.",
    )
    info.add_argument(
        "--list-templates",
        action="store_true",
        help="List every pseudocode template, built-in and installed, with an "
        "example line of each, then exit.",
    )
    info.add_argument(
        "--help-template",
        metavar="NAME",
        nargs="?",
        const="",
        default=None,
        help="Explain one template field by field, then exit. With no NAME, "
        "explain how to write a template of your own.",
    )
    info.add_argument(
        "--dump-template",
        metavar="NAME",
        help="Print a template as JSON -- the starting point for editing one "
        "into a notation of your own -- then exit.",
    )
    info.add_argument(
        "--porcelain",
        action="store_true",
        help="Make --list-formats and --list-templates emit tab-separated "
        "fields for scripts and shell completion instead of prose.",
    )
    info.add_argument(
        "--direction",
        choices=("any", "in", "out"),
        default="any",
        metavar="DIR",
        help="Restrict --list-formats to readable (in) or writable (out) "
        "formats. Default: any.",
    )
    info.add_argument(
        "-V",
        "--version",
        action="store_true",
        help="Show version information and exit.",
    )
    return p


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


def read_source(path: str, args, ctx: Context) -> tuple[list[Any], str]:
    """Return (documents, display-name) for one input source."""
    if path == "-":
        raw = sys.stdin.buffer.read()
        hint = None
        label = "<stdin>"
    else:
        p = Path(path)
        if not p.exists():
            raise Fail(f"no such file: {path}")
        try:
            raw = p.read_bytes()
        except OSError as exc:
            raise Fail(f"cannot read {path}: {exc}") from None
        hint = p
        label = path

    name = args.input_format
    if name == "auto":
        name = sniff(raw, hint)
    fmt = resolve_format(name, "in")

    try:
        docs = codec_for(fmt).load_all(raw, ctx)
    except FormatUnavailable as exc:
        raise Fail(str(exc)) from None
    except CodecError as exc:
        raise Fail(f"{label}: {exc}") from None
    except Exception as exc:
        raise Fail(f"{label}: failed to parse as {fmt.name}: {exc}") from None

    return docs, label


def wrap_label(path: str, mode: str, index: int) -> str:
    if mode == "index":
        return str(index)
    if mode == "path":
        return path
    base = "stdin" if path == "-" else os.path.basename(path)
    if mode == "stem":
        return base.rsplit(".", 1)[0] if "." in base else base
    return base


def emit(docs: list[Any], args, ctx: Context, out_fmt: Format) -> bytes:
    codec = codec_for(out_fmt)
    chunks = []
    for doc in docs:
        try:
            chunks.append(codec.dump(doc, ctx))
        except FormatUnavailable as exc:
            raise Fail(str(exc)) from None
        except CodecError as exc:
            raise Fail(str(exc)) from None
        except Exception as exc:
            raise Fail(f"failed to write {out_fmt.name}: {exc}") from None
    return b"".join(chunks)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.help:
        parser.print_help()
        return 0
    if args.version:
        print(f"{PROG} {__version__}")
        print(f"python {sys.version.split()[0]}")
        notations = {t.name for t in templates.TEMPLATES}
        live = [n for n in registry.names(available_only=True) if n not in notations]
        print(f"formats enabled: {', '.join(live)}")
        print(f"notations: {', '.join(templates.names())}")
        return 0
    if args.help_format:
        print(format_detail(args.help_format))
        return 0
    if args.list_formats:
        print(format_table(args.direction, args.porcelain))
        return 0
    if args.list_templates:
        print(template_table(args.porcelain))
        return 0
    if args.help_template is not None:
        print(
            template_guide()
            if not args.help_template
            else template_detail(resolve_template(args.help_template))
        )
        return 0
    if args.dump_template:
        import json as _json

        print(
            _json.dumps(
                resolve_template(args.dump_template).as_dict(full=True), indent=2
            )
        )
        return 0

    if args.reverse:
        args.multipacket = True

    tmpl = resolve_template(args.template) if args.template else None
    args.output_format = choose_output_format(args.output_format, tmpl)

    render_opts = RenderOptions(
        root=args.root,
        escape_special=args.escape_special,
        perl_compat=args.perl_compat,
        sort_keys=args.sort_keys,
        template=tmpl,
    )
    ctx = Context(
        encoding=args.input_encoding,
        indent=None if args.compact else args.indent,
        sort_keys=args.sort_keys,
        multipacket=args.multipacket,
        ensure_ascii=args.ascii,
        null_policy=args.null_policy,
        extra={"render_options": render_opts},
    )
    if args.avro_schema:
        import json as _json

        try:
            ctx.avro_schema = _json.loads(Path(args.avro_schema).read_text())
        except (OSError, ValueError) as exc:
            raise Fail(f"--avro-schema: {exc}") from None

    out_fmt = resolve_format(args.output_format, "out")

    sources = args.files or ["-"]
    all_docs: list[Any] = []
    labels: list[str] = []
    for idx, src in enumerate(sources):
        docs, _label = read_source(src, args, ctx)
        all_docs.extend(docs)
        labels.extend([wrap_label(src, args.wrap_key, idx)] * len(docs))

    if args.reverse:
        all_docs.reverse()
        labels.reverse()

    if args.wrap_key != "none":
        result = [wrap(all_docs, labels)]
        if args.dedup:
            result = [dedup(result[0])]
    elif args.no_merge:
        result = [dedup(d) for d in all_docs] if args.dedup else all_docs
    else:
        result = [
            merge_all(
                all_docs,
                map_strategy=args.merge_strategy,
                list_strategy=args.list_merge,
                deduplicate=args.dedup,
            )
        ]

    payload = emit(result, args, ctx, out_fmt)

    if args.output == "-":
        stream = sys.stdout.buffer
        if out_fmt.kind == "binary" and stream.isatty():
            raise Fail(
                f"refusing to write binary {out_fmt.name} to a terminal; "
                "redirect it or use --output FILE"
            )
        stream.write(payload)
        stream.flush()
    else:
        try:
            Path(args.output).write_bytes(payload)
        except OSError as exc:
            raise Fail(f"cannot write {args.output}: {exc}") from None
    return 0


def entrypoint() -> None:
    try:
        sys.exit(main())
    except BrokenPipeError:
        # `leafdump big.json | head` is a normal thing to do.
        try:
            sys.stdout.close()
        finally:
            os._exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
