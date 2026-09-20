# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

"""Test suite for leafdump.

Runs with plain `python3 -m unittest` -- no pytest required, so it works in
the same dependency-free environment the tool itself targets.  Tests for
optional formats skip themselves when the backing package is absent.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leafdump import cli, merge, registry, templates
from leafdump.codecs import Context, codec_for
from leafdump.flatten import (
    RenderOptions,
    render_perl,
    render_python,
    render_template,
)
from leafdump.templates import TemplateError

SAMPLE = {
    "n": 42,
    "f": 1.5,
    "s": "hi",
    "t": True,
    "f2": False,
    "nul": None,
    "e": {},
    "ea": [],
    "nest": [1, [2, {"a": "b"}]],
    "esc": "l1\nl2\ttab",
}


def run(argv, stdin=b""):
    """Invoke the CLI the way a shell would, capturing stdout bytes."""
    buf = io.BytesIO()

    class _Out:
        """Minimal stdout stand-in: print() writes text, codecs write bytes."""

        buffer = buf

        def isatty(self):
            return False

        def write(self, text):
            buf.write(text.encode())
            return len(text)

        def flush(self):
            pass

    old_out, old_in = sys.stdout, sys.stdin
    sys.stdout = _Out()
    sys.stdin = type("I", (), {"buffer": io.BytesIO(stdin)})()
    try:
        code = cli.main(argv)
    finally:
        sys.stdout, sys.stdin = old_out, old_in
    return code, buf.getvalue().decode()


class TestPerlRenderer(unittest.TestCase):
    def test_original_notation(self):
        lines = list(render_perl(SAMPLE, RenderOptions()))
        self.assertIn('ROOT.{s}."hi"', lines)
        self.assertIn("ROOT.{t}.true", lines)
        self.assertIn("ROOT.{f2}.false", lines)
        self.assertIn('ROOT.{n}."42"', lines)  # numbers are quoted too
        self.assertIn('ROOT.{nest}.1.1.{a}."b"', lines)

    def test_compat_matches_perl_quirks(self):
        lines = list(render_perl(SAMPLE, RenderOptions(perl_compat=True)))
        self.assertIn('ROOT.{nul}.""', lines)  # undef stringified to ""
        self.assertFalse([ln for ln in lines if ln.endswith(".{}")])
        self.assertFalse([ln for ln in lines if ln.endswith(".[]")])

    def test_modern_mode_keeps_empties_and_null(self):
        lines = list(render_perl(SAMPLE, RenderOptions()))
        self.assertIn("ROOT.{nul}.undef", lines)
        self.assertIn("ROOT.{e}.{}", lines)
        self.assertIn("ROOT.{ea}.[]", lines)

    def test_escape_special(self):
        lines = list(
            render_perl({"x": "a\rb\nc\td"}, RenderOptions(escape_special=True))
        )
        self.assertEqual(lines, ['ROOT.{x}."a\\rb\\nc\\td"'])

    def test_escape_special_compat_leaves_tab(self):
        lines = list(
            render_perl(
                {"x": "a\nb\tc"}, RenderOptions(escape_special=True, perl_compat=True)
            )
        )
        self.assertEqual(lines, ['ROOT.{x}."a\\nb\tc"'])

    def test_custom_root(self):
        lines = list(render_perl({"a": 1}, RenderOptions(root="PKT")))
        self.assertEqual(lines, ['PKT.{a}."1"'])

    def test_float_that_is_integral_prints_bare(self):
        lines = list(render_perl({"a": 1.0}, RenderOptions()))
        self.assertEqual(lines, ['ROOT.{a}."1"'])


class TestPythonRenderer(unittest.TestCase):
    def test_lines_are_valid_python(self):
        lines = list(render_python(SAMPLE, RenderOptions()))
        env: dict = {}
        # Replay the assignments to prove the notation actually rebuilds
        # the structure -- the point of the format.
        exec("ROOT = {}", env)
        for line in lines:
            path = line.split(" = ")[0]
            _autovivify(env, path)
            exec(line, env)
        self.assertEqual(env["ROOT"], SAMPLE)

    def test_types_survive(self):
        lines = list(
            render_python({"n": 1, "f": 1.0, "s": "1", "b": True}, RenderOptions())
        )
        self.assertIn("ROOT['n'] = 1", lines)
        self.assertIn("ROOT['f'] = 1.0", lines)
        self.assertIn("ROOT['s'] = '1'", lines)
        self.assertIn("ROOT['b'] = True", lines)


def _autovivify(env, path):
    """Create intermediate containers so an assignment line can execute."""
    import re

    keys = [eval(p) for p in re.findall(r"\[([^\]]+)\]", path)]
    cur = env["ROOT"]
    for i, key in enumerate(keys):
        last = i == len(keys) - 1
        if isinstance(cur, list):
            while len(cur) <= key:
                cur.append(None)
            if not last and cur[key] is None:
                cur[key] = [] if isinstance(keys[i + 1], int) else {}
        elif not last and key not in cur:
            cur[key] = [] if isinstance(keys[i + 1], int) else {}
        if not last:
            cur = cur[key]


class TestDeepStructures(unittest.TestCase):
    def test_no_recursion_limit(self):
        """10k-deep nesting must not raise RecursionError."""
        obj: dict = {}
        cur = obj
        for _ in range(10_000):
            cur["k"] = {}
            cur = cur["k"]
        cur["leaf"] = 1
        lines = list(render_perl(obj, RenderOptions()))
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith('.{leaf}."1"'))


class TestMerge(unittest.TestCase):
    A: ClassVar[dict] = {"x": {"a": 1}, "l": [1, 2], "s": "one"}
    B: ClassVar[dict] = {"x": {"b": 2}, "l": [2, 3], "s": "two"}

    def test_deep_concat(self):
        r = merge.merge_all([self.A, self.B])
        self.assertEqual(r, {"x": {"a": 1, "b": 2}, "l": [1, 2, 2, 3], "s": "two"})

    def test_union(self):
        r = merge.merge_all([self.A, self.B], list_strategy="union")
        self.assertEqual(r["l"], [1, 2, 3])

    def test_index(self):
        r = merge.merge_all(
            [{"l": [{"a": 1}]}, {"l": [{"b": 2}]}], list_strategy="index"
        )
        self.assertEqual(r["l"], [{"a": 1, "b": 2}])

    def test_collect_keeps_both(self):
        r = merge.merge_all([self.A, self.B], map_strategy="collect")
        self.assertEqual(r["s"], ["one", "two"])

    def test_shallow_replaces_whole_value(self):
        r = merge.merge_all([self.A, self.B], map_strategy="shallow")
        self.assertEqual(r["x"], {"b": 2})

    def test_dedup_respects_type(self):
        # 1 == 1.0 == True in Python; data formats disagree, so we must too.
        self.assertEqual(merge.dedup([1, 1.0, True, "1", 1]), [1, 1.0, True, "1"])

    def test_dedup_is_order_insensitive_for_maps(self):
        self.assertEqual(
            merge.dedup([{"a": 1, "b": 2}, {"b": 2, "a": 1}]), [{"a": 1, "b": 2}]
        )

    def test_dedup_recurses(self):
        self.assertEqual(merge.dedup({"k": [[1, 1], [1, 1]]}), {"k": [[1]]})

    def test_wrap(self):
        self.assertEqual(merge.wrap([1, 2], ["a", "a"]), {"a": 1, "a#2": 2})


class TestRegistry(unittest.TestCase):
    def test_every_format_has_a_codec(self):
        for fmt in registry.FORMATS:
            self.assertIsNotNone(codec_for(fmt), fmt.name)

    def test_aliases_resolve(self):
        self.assertIs(registry.lookup("yml"), registry.lookup("yaml"))
        self.assertIs(registry.lookup("NDJSON"), registry.lookup("jsonl"))

    def test_toml_is_direction_asymmetric(self):
        toml = registry.lookup("toml")
        self.assertTrue(toml.available_for("in"))  # stdlib tomllib

    def test_unavailable_formats_report_an_install_hint(self):
        for fmt in registry.FORMATS:
            if not fmt.available_for("any"):
                self.assertTrue(fmt.install_hint("any"), fmt.name)

    def test_render_formats_are_write_only(self):
        for name in ("perl", "python"):
            self.assertFalse(registry.lookup(name).reads)


@contextlib.contextmanager
def no_custom_templates():
    """Run with a template search path that is guaranteed to be empty."""
    with tempfile.TemporaryDirectory() as empty:
        old = {k: os.environ.get(k) for k in (templates.ENV_PATH, "XDG_CONFIG_HOME")}
        os.environ[templates.ENV_PATH] = empty
        os.environ["XDG_CONFIG_HOME"] = empty
        try:
            yield Path(empty)
        finally:
            for key, value in old.items():
                if value is None:
                    del os.environ[key]
                else:
                    os.environ[key] = value


class TestTemplates(unittest.TestCase):
    """The catalogue itself, and the promises every entry in it makes."""

    NESTED: ClassVar[dict] = {
        "hosts": [
            {"name": "web-01", "tls": True, "port": 443, "tags": [], "note": None}
        ]
    }

    def render(self, name, obj=None, **opts):
        return list(
            render_template(
                obj if obj is not None else self.NESTED,
                RenderOptions(**opts),
                templates.BY_NAME[name],
            )
        )

    def test_every_builtin_validates(self):
        for tmpl in templates.TEMPLATES:
            with self.subTest(template=tmpl.name):
                self.assertIs(tmpl.validate(), tmpl)

    def test_every_builtin_renders_every_leaf(self):
        """A template that drops leaves is broken however pretty it looks."""
        for tmpl in templates.TEMPLATES:
            with self.subTest(template=tmpl.name):
                lines = list(render_template(self.NESTED, RenderOptions(), tmpl))
                self.assertEqual(len(lines) - len(tmpl.header), 5)

    def test_names_and_aliases_are_unique_across_the_registry(self):
        """A template alias that collides with a format name is invisible.

        registry builds its alias map with setdefault, so a clash does not
        raise -- the second one simply never resolves. `js` belongs to json,
        which is why the javascript template does not claim it.
        """
        seen: dict = {}
        for fmt in registry.FORMATS:
            for key in (fmt.name, *fmt.aliases):
                self.assertNotIn(key, seen, f"{key} claimed twice")
                seen[key] = fmt.name
        for tmpl in templates.TEMPLATES:
            for key in (tmpl.name, *tmpl.aliases):
                self.assertEqual(registry.lookup(key).name, tmpl.name)

    def test_one_indexed_languages(self):
        for name, expected in (
            ("lua", 'ROOT["hosts"][1]["name"] = "web-01"'),
            ("r", 'ROOT[["hosts"]][[1]][["name"]] <- "web-01"'),
        ):
            with self.subTest(template=name):
                self.assertIn(expected, self.render(name))

    def test_javascript_uses_bare_keys_only_when_safe(self):
        lines = self.render("javascript", {"ok": 1, "not ok": 2, "3rd": 3})
        self.assertIn("ROOT.ok = 1;", lines)
        self.assertIn('ROOT["not ok"] = 2;', lines)
        self.assertIn('ROOT["3rd"] = 3;', lines)

    def test_jsonpointer_escapes_reserved_characters(self):
        # RFC 6901: ~ is ~0 and / is ~1, or the pointer no longer parses.
        self.assertEqual(self.render("jsonpointer", {"a/b~c": 1}), ["/a~1b~0c = 1"])

    def test_shell_emits_its_header_once(self):
        lines = self.render("shell", {"a": 1, "b": 2})
        self.assertEqual(lines[0], "declare -A ROOT")
        self.assertEqual(lines.count("declare -A ROOT"), 1)

    def test_shell_quoting_survives_a_quote(self):
        self.assertEqual(self.render("shell", {"k": "it's"})[1], "ROOT[k]='it'\\''s'")

    def test_root_override_reaches_every_template(self):
        for tmpl in templates.TEMPLATES:
            with self.subTest(template=tmpl.name):
                line = list(render_template({"a": 1}, RenderOptions(root="PKT"), tmpl))[
                    -1
                ]
                self.assertIn("PKT", line)

    def test_percent_in_data_is_not_a_placeholder(self):
        """Expansion runs on the template, never on the data it produced."""
        self.assertEqual(
            self.render("python", {"%v": "%p %%"}), ["ROOT['%v'] = '%p %%'"]
        )

    def test_perl_compat_is_ignored_by_other_notations(self):
        """The quirks are json_dump.pl's, not a general rendering mode."""
        lines = self.render("go", {"nul": None, "e": {}}, perl_compat=True)
        self.assertIn('ROOT["nul"] = nil', lines)
        self.assertIn('ROOT["e"] = map[string]any{}', lines)


class TestCustomTemplates(unittest.TestCase):
    """Loading a hand-written template: the parts a user can get wrong."""

    def test_base_inherits_behaviour_but_not_identity(self):
        tmpl = templates.from_dict(
            {"base": "javascript", "name": "kotlin", "line": "%p = %v"}
        )
        self.assertEqual(tmpl.quote, "json")  # inherited
        self.assertEqual(tmpl.bare, ".%r")  # inherited
        self.assertEqual(tmpl.aliases, ())  # not inherited
        self.assertEqual(tmpl.notes, ())  # not inherited
        self.assertEqual(
            list(render_template({"a": [1]}, RenderOptions(), tmpl)), ["ROOT.a[0] = 1"]
        )

    def test_unknown_field_is_rejected_by_name(self):
        with self.assertRaises(TemplateError) as caught:
            templates.from_dict({"lien": "%p"})
        self.assertIn("lien", str(caught.exception))

    def test_unknown_placeholder_is_rejected(self):
        with self.assertRaises(TemplateError) as caught:
            templates.from_dict({"name": "x", "line": "%p -> %z"})
        self.assertIn("%z", str(caught.exception))

    def test_unknown_quoting_style_is_rejected(self):
        with self.assertRaises(TemplateError):
            templates.from_dict({"name": "x", "quote": "smart"})

    def test_wrong_type_is_rejected(self):
        with self.assertRaises(TemplateError):
            templates.from_dict({"name": "x", "index_base": "one"})
        with self.assertRaises(TemplateError):
            templates.from_dict({"name": "x", "aliases": "one"})

    def test_dump_and_reload_round_trips(self):
        """--dump-template NAME | edit | --template FILE has to be lossless."""
        for tmpl in templates.TEMPLATES:
            with self.subTest(template=tmpl.name):
                data = json.loads(json.dumps(tmpl.as_dict(full=True)))
                rebuilt = templates.from_dict(data, source="built-in")
                self.assertEqual(rebuilt, tmpl)

    def test_a_file_is_found_by_name_on_the_search_path(self):
        with no_custom_templates() as tmpdir:
            path = tmpdir / "kotlin.json"
            path.write_text(json.dumps({"base": "javascript", "line": "%p = %v"}))
            by_name = templates.resolve("kotlin")
            by_path = templates.resolve(str(path))
            self.assertEqual(by_name.name, "kotlin")
            self.assertEqual(by_name, by_path)
            # A built-in of the same name would have won.
            (tmpdir / "perl.json").write_text('{"line": "%p!%v"}')
            self.assertEqual(templates.resolve("perl").source, "built-in")

    def test_missing_template_names_the_alternatives(self):
        with no_custom_templates(), self.assertRaises(TemplateError) as caught:
            templates.resolve("kotlin-not-installed")
        self.assertIn("javascript", str(caught.exception))

    def test_shipped_examples_load_and_render(self):
        examples = sorted(
            (Path(__file__).resolve().parent.parent / "contrib" / "templates").glob(
                "*.json"
            )
        )
        self.assertTrue(examples, "contrib/templates has no examples")
        for path in examples:
            with self.subTest(example=path.name):
                tmpl = templates.load_file(path)
                lines = list(render_template({"a": [1, None]}, RenderOptions(), tmpl))
                self.assertEqual(len(lines) - len(tmpl.header), 2)


class TestSniff(unittest.TestCase):
    def test_json_object(self):
        self.assertEqual(cli.sniff(b'  {"a": 1}'), "json")

    def test_jsonl(self):
        self.assertEqual(cli.sniff(b'{"a":1}\n{"a":2}\n{"a":3}\n'), "jsonl")

    def test_yaml_document_marker(self):
        self.assertEqual(cli.sniff(b"---\na: 1\n"), "yaml")

    def test_extension_wins(self):
        self.assertEqual(cli.sniff(b'{"a":1}', Path("x.yaml")), "yaml")

    def test_bson_length_prefix(self):
        payload = b"\x05\x00\x00\x00\x00"  # empty BSON document
        self.assertEqual(cli.sniff(payload), "bson")

    def test_avro_magic(self):
        self.assertEqual(cli.sniff(b"Obj\x01rest"), "avro")


def _stringify(o):
    """Every scalar as its NestedText spelling, containers preserved."""
    if isinstance(o, dict):
        return {k: _stringify(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_stringify(v) for v in o]
    if o is None:
        return ""
    if isinstance(o, bool):
        return "true" if o else "false"
    return str(o)


class TestRoundTrips(unittest.TestCase):
    """Every installed format must survive a data -> bytes -> data cycle.

    "Survive" is not the same claim for every format, which is why this is a
    table rather than one assertEqual.  Three of them carry less than JSON
    does, and each does so by design and says so in its registry notes:
    NestedText has no scalar types at all, protobuf's Value has one numeric
    type, and TOML 1.0 has no null.  Asserting exact equality against those
    would not be a stricter test, it would be a test of a promise the format
    never made -- so what is asserted instead is the documented behaviour.
    """

    PAYLOAD: ClassVar[dict] = {
        "name": "a",
        "vals": [1, 2, {"deep": True}],
        "nul": None,
        "uni": "café",
    }

    def _expected(self, name):
        """What a faithful round-trip looks like for *name*.

        Returns (payload_to_write, expected_result).
        """
        if name == "nestedtext":
            # No scalar types: every leaf comes back as the text it was
            # written as. The structure survives; the types do not.
            return self.PAYLOAD, _stringify(self.PAYLOAD)
        if name == "toml":
            # TOML 1.0 has no null. --null-policy is how a real invocation
            # resolves that; here the key is simply left out.
            payload = {k: v for k, v in self.PAYLOAD.items() if v is not None}
            return payload, payload
        return self.PAYLOAD, self.PAYLOAD

    def test_round_trip(self):
        ctx = Context()
        for fmt in registry.FORMATS:
            if not (fmt.can_read and fmt.can_write):
                continue
            with self.subTest(fmt=fmt.name):
                codec = codec_for(fmt)
                payload, expected = self._expected(fmt.name)
                try:
                    raw = codec.dump(payload, ctx)
                except NotImplementedError as exc:
                    # The codec is installed and wired up correctly; the
                    # library behind it has not implemented this direction.
                    # toon-format 0.1.0 ships exactly such an encode(). This
                    # is upstream's gap, and it turns into a real failure the
                    # moment upstream fills it and something here is wrong.
                    self.skipTest(f"{fmt.name}: upstream cannot encode yet ({exc})")
                back = codec.load_all(raw, ctx)[0]
                if fmt.name == "protobuf-struct":
                    # Value has one numeric type (double) by design.
                    back = json.loads(json.dumps(back))
                    self.assertEqual(back["vals"][0], 1)
                    self.assertEqual(back["name"], "a")
                else:
                    self.assertEqual(back, expected)

    def test_toml_rejects_null_with_a_useful_message(self):
        """The null case TOML cannot represent points at the flag that fixes it."""
        fmt = registry.lookup("toml")
        if not fmt.can_write:
            self.skipTest("tomli-w not installed")
        with self.assertRaises(Exception) as caught:
            codec_for(fmt).dump({"nul": None}, Context())
        self.assertIn("--null-policy", str(caught.exception))


class TestCli(unittest.TestCase):
    def test_default_is_perl_dump(self):
        code, out = run(["-"], b'{"a": {"b": 1}}')
        self.assertEqual(code, 0)
        self.assertEqual(out, 'ROOT.{a}.{b}."1"\n')

    def test_python_output(self):
        _, out = run(["--to", "python", "-"], b'{"a": [1]}')
        self.assertEqual(out, "ROOT['a'][0] = 1\n")

    def test_reverse_implies_multipacket(self):
        _, out = run(["-r", "--no-merge", "-"], b'{"i":1}\n{"i":2}\n')
        self.assertEqual(out, 'ROOT.{i}."2"\nROOT.{i}."1"\n')

    def test_merge_two_stdin_documents(self):
        _, out = run(["-m", "--to", "json", "--compact", "-"], b'{"a":1}\n{"b":2}\n')
        self.assertEqual(json.loads(out), {"a": 1, "b": 2})

    def test_unknown_format_exits_2(self):
        with self.assertRaises(SystemExit) as ctx:
            run(["--to", "nope", "-"], b"{}")
        self.assertEqual(ctx.exception.code, 2)

    def test_output_only_format_rejected_as_input(self):
        with self.assertRaises(SystemExit) as ctx:
            run(["--from", "perl", "-"], b"{}")
        self.assertEqual(ctx.exception.code, 2)

    def test_list_formats_porcelain_is_tabular(self):
        _, out = run(["-L", "--porcelain"])
        rows = [r for r in out.splitlines() if r]
        self.assertEqual(len(rows), len(registry.FORMATS))
        for row in rows:
            self.assertEqual(len(row.split("\t")), 6)

    def test_help_format_works_for_uninstalled(self):
        _, out = run(["--help-format", "avro"])
        self.assertIn("avro", out)
        self.assertIn("schema", out.lower())

    def test_sort_keys(self):
        _, out = run(["-s", "-"], b'{"b":1,"a":2}')
        self.assertEqual(out, 'ROOT.{a}."2"\nROOT.{b}."1"\n')

    def test_root_rename(self):
        _, out = run(["--root", "PKT", "-"], b'{"a":1}')
        self.assertEqual(out, 'PKT.{a}."1"\n')

    def test_template_by_flag_and_by_format_name_agree(self):
        _, by_flag = run(["-T", "go", "-"], b'{"a":[1]}')
        _, by_format = run(["--to", "go", "-"], b'{"a":[1]}')
        self.assertEqual(by_flag, by_format)
        self.assertEqual(by_flag, 'ROOT["a"][0] = 1\n')

    def test_template_and_data_format_together_is_an_error(self):
        """One of the two flags was going to be ignored; say so instead."""
        with self.assertRaises(SystemExit) as ctx:
            run(["--to", "json", "--template", "go", "-"], b"{}")
        self.assertEqual(ctx.exception.code, 2)

    def test_template_wins_over_a_render_format(self):
        _, out = run(["--to", "perl", "--template", "go", "-"], b'{"a":1}')
        self.assertEqual(out, 'ROOT["a"] = 1\n')

    def test_unknown_template_exits_2(self):
        with self.assertRaises(SystemExit) as ctx:
            run(["-T", "cobol", "-"], b"{}")
        self.assertEqual(ctx.exception.code, 2)

    def test_list_templates_porcelain_is_tabular(self):
        # Pointed at empty directories: the developer's own ~/.config
        # templates are real output, but they are not this test's business.
        with no_custom_templates():
            _, out = run(["--list-templates", "--porcelain"])
        rows = [r for r in out.splitlines() if r]
        self.assertEqual(len(rows), len(templates.TEMPLATES))
        for row in rows:
            self.assertEqual(len(row.split("\t")), 5)

    def test_help_template_without_a_name_explains_authoring(self):
        _, out = run(["--help-template"])
        self.assertIn("WRITING A TEMPLATE", out)
        self.assertIn("%v", out)
        for style in templates.QUOTES:
            self.assertIn(style, out)

    def test_dump_template_is_loadable_json(self):
        _, out = run(["--dump-template", "r"])
        self.assertEqual(
            templates.from_dict(json.loads(out), source="built-in"),
            templates.BY_NAME["r"],
        )

    def test_ascii_flag_reaches_the_renderer(self):
        _, out = run(["--ascii", "-t", "python", "-"], '{"k":"café"}'.encode())
        self.assertIn("caf\\xe9", out)


class TestPerlParity(unittest.TestCase):
    """--perl-compat against output frozen from the original json_dump.pl.

    The Perl script is no longer in the tree; what it printed is.  Each file in
    tests/golden/ was captured from json_dump.pl before it was removed, one per
    document in contrib/, and together they *are* the compatibility promise --
    every line --perl-compat emits for these documents, and no others.

    Frozen rather than live, and the trade is worth stating.  A live comparison
    proved agreement with whatever json_dump.pl did that day; a frozen one
    proves agreement with what it did on the day it was retired, which is the
    thing the promise was ever about -- a script that is gone will not be
    changing.  What is given up is noticing if the original were edited, and
    there is no original left to edit.

    What is gained is that this now runs.  The live version skipped itself
    wherever perl was absent, which is most minimal containers and was every
    environment that did not happen to have it; the promise went unchecked
    exactly where nobody was looking.  It also covers all three documents
    rather than the alphabetically first one, which is all the old comparison
    ever reached.

    Perl randomises hash iteration order, so the comparison was always
    line-set based and stays that way.  Both sides are sorted here rather than
    trusting the file to have been sorted the same way it will be read.
    """

    GOLDEN: ClassVar[Path] = Path(__file__).resolve().parent / "golden"
    CONTRIB: ClassVar[Path] = Path(__file__).resolve().parent.parent / "contrib"

    def test_matches_frozen_perl_output(self):
        frozen = sorted(self.GOLDEN.glob("*.perl-compat"))
        self.assertTrue(frozen, "tests/golden/ has no frozen perl output")
        for golden in frozen:
            document = self.CONTRIB / f"{golden.stem}.json"
            with self.subTest(document=document.name):
                self.assertTrue(document.exists(), f"{document} is missing")
                _, ours = run(["--perl-compat", "-e", str(document)])
                self.assertEqual(
                    sorted(golden.read_text(encoding="utf-8").splitlines()),
                    sorted(ours.splitlines()),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
