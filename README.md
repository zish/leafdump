# json-dump

Make nested data greppable. Every line is one value and the complete path to
it, so a service configuration, an API response or a wall of log records
becomes something `grep`, `less`, `cut` and `awk` already know what to do with.

```console
$ json-dump config.json
ROOT.{version}."3"
ROOT.{configurePresets}.0.{name}."default"
ROOT.{configurePresets}.0.{hidden}.true
ROOT.{configurePresets}.0.{cacheVariables}.{CMAKE_BUILD_TYPE}."Release"
```

That matters most in the case those tools otherwise handle worst: a document
that arrives as one enormous line with no newlines in it, where `grep` matches
the whole file and a pager shows you a wall.

```console
$ grep name minified.json | wc -l          # 1 — the whole document matched
$ json-dump minified.json | grep name      # one line per hit, with its path
```

It is built for the question that comes up in front of a machine that is
misbehaving: what is actually in this file, where does that setting live, and
which of these hosts disagrees with the others. Answering it needs no query
language, no editor and no scripting — only the filters already in your
fingers — and the program itself needs nothing but Python.

Two further modes share the same reader. One converts between serialisation
formats, so a file in something nothing local can open becomes one that
everything can. The other merges several documents into a single structure,
which is how a base configuration and its per-host overrides get compared or
combined.

This is a different job from [jq](https://jqlang.github.io/jq/), and the two
get along. jq is the tool for real queries, joins and transformations, at the
price of a filter language you have to know well enough to write under time
pressure. json-dump is for the other half: finding where a value lives, reading
the shape of a document you have never opened, or grepping a directory of them.

## Install

Python 3.11 or newer, and nothing else:

```console
pipx install 'json-dump[all]'  # + YAML, JSON5, TOON, TOML output, MessagePack, CBOR, NestedText
pip install json-dump          # core: every pseudocode dump, JSON, JSONL, repr, TOML input
pip install 'json-dump[yaml]'  # or pick individual formats
```

For a machine with no Python on it, `make binary` compiles the whole thing —
interpreter, package and codecs — into one self-contained executable:

```console
$ make binary && ./bin/json-dump --version
json-dump 0.2.0
formats enabled: json, jsonl, repr, pseudocode, toml, yaml, json5, toon, msgpack, cbor, nestedtext
notations: perl, python, javascript, cpp, go, rust, ruby, php, lua, r, jq, jsonpath, jsonpointer, dotted, shell
```

Nuitka is not a dependency and is not downloaded until that target runs.

[INSTALL.md][install] has the rest: every extra, installing from source,
running straight from a checkout, the single-file binary, shell completion and
the manpage.

The core has **no dependencies**. Every other format is optional and is hidden
from `--help` until its package is installed:

```console
$ json-dump --to cbor data.json
json-dump: error: support for writing 'cbor' is not installed.
  pip install cbor2  (or: pip install 'json-dump[cbor]')
```

## Pseudocode notations

The path dump is rendered from a **template**: a table of literal strings
saying how one leaf is spelled as a line of some language. Fifteen are built
in, and `--template` (`-T`) picks one:

```console
$ json-dump --template go hosts.json
ROOT["hosts"][0]["name"] = "web-01"
ROOT["hosts"][0]["tls"] = true
ROOT["hosts"][0]["tags"] = []any{}
```

| Template | One leaf, rendered |
| --- | --- |
| `perl` (default) | `ROOT.{hosts}.0.{name}."web-01"` |
| `python` | `ROOT['hosts'][0]['name'] = 'web-01'` |
| `javascript` | `ROOT.hosts[0].name = "web-01";` |
| `cpp` | `ROOT["hosts"][0]["name"] = "web-01";` |
| `go` | `ROOT["hosts"][0]["name"] = "web-01"` |
| `rust` | `ROOT["hosts"][0]["name"] = json!("web-01");` |
| `ruby` | `ROOT["hosts"][0]["name"] = "web-01"` |
| `php` | `$ROOT["hosts"][0]["name"] = "web-01";` |
| `lua` | `ROOT["hosts"][1]["name"] = "web-01"` |
| `r` | `ROOT[["hosts"]][[1]][["name"]] <- "web-01"` |
| `jq` | `.["hosts"][0]["name"] = "web-01"` |
| `jsonpath` | `$.hosts[0].name = "web-01"` |
| `jsonpointer` | `/hosts/0/name = "web-01"` |
| `dotted` | `ROOT.hosts.0.name=web-01` |
| `shell` | `ROOT[hosts.0.name]='web-01'` |

`json-dump --list-templates` prints that table from the live catalogue, custom
templates included. Every built-in is also an output format of its own name,
so `-t go` and `-T go` are the same thing.

Lua and R count from **1**, and the templates know it — the first array
element really is `[1]` there. The languages that quote differently escape
differently too: each template names a quoting style, and the style does the
escaping.

Three notations are worth calling out:

**`perl`** is the default, and the one this tool was written to keep (see
[History](#history)). Values are quoted but untyped, so `1` and `"1"` are
indistinguishable; `--perl-compat` restores the original's quirks exactly.

**`python`** escapes with `repr()`, so quoting is exact, types survive, empty
containers are representable, and the lines replay to rebuild the structure.
It is the better default for anything but grep.

**`dotted`** quotes nothing at all, which makes it the friendliest thing to
hand to `cut` and `awk` — and the most ambiguous. Pair it with `-e`.

### Writing your own

A template is a JSON (or TOML) file of literal strings. Nothing in it is
imported, evaluated or executed — the loader reads data and rejects any field
or placeholder it does not recognise.

```console
$ json-dump --help-template          # every field, placeholder and quoting style
$ json-dump --help-template go       # one notation, field by field
$ json-dump --dump-template go > kotlin.json   # a starting point to edit
```

Usually only a few lines differ from something built in, so `base` inherits
the rest:

```json
{
  "base": "javascript",
  "name": "kotlin",
  "summary": "Kotlin map/list assignments",
  "line": "%p = %v",
  "bare": "",
  "empty_map": "mapOf<String, Any?>()",
  "empty_seq": "listOf<Any?>()"
}
```

```console
$ json-dump --template ./kotlin.json hosts.json
ROOT["hosts"][0]["name"] = "web-01"
```

The placeholders are `%r` (root or a raw key), `%c` (the path chain), `%s` (a
segment as a literal), `%p` (the finished path) and `%v` (the finished
value). Drop the file into `~/.config/json-dump/templates/` — or any
directory named in `$JSON_DUMP_TEMPLATES` — and it becomes `--template
kotlin`. Built-in names always resolve first, so nothing can quietly redefine
`perl` for a script that expected it.

[contrib/templates/][templates] has three worked examples: inheriting
with `base`, a flat TSV notation written from scratch, and one that emits SQL
`INSERT` statements with a header.

## Converting

```console
$ json-dump --to yaml   config.json
$ json-dump --to json   --compact data.msgpack
$ producer | json-dump --from msgpack --to jsonl
```

`json-dump --list-formats` shows what is available; `--help-format NAME`
explains one format's dependencies and round-trip caveats, including for
formats that are not installed.

## Merging

Multiple inputs merge into one structure:

```console
$ json-dump --to json base.json site.json local.json
$ json-dump --to json --dedup --list-merge union a.json b.json
$ json-dump --to json --merge-strategy collect a.json b.json   # keep conflicts
$ json-dump --to json --wrap-key stem */settings.json          # key by filename
```

Deduplication compares by value **and type**, so `1`, `1.0` and `true` stay
distinct even though Python considers them equal.

## History

json-dump is a direct descendant of `json_dump.pl`, a Perl script I wrote and
used constantly for several years — and would probably still be using, if Perl
were still as ubiquitous on a fresh machine as it once was. The rewrite exists
because that assumption stopped holding, not because the notation needed
fixing: the default `perl` output is the same notation, for the same reason.

The Perl script itself is retired, but the comparison is not: its output for
every document in `contrib/` is frozen in the repository, and every change is
checked against it line for line. That check used to skip wherever `perl` was
absent; now it runs everywhere. `--perl-compat` reproduces the original byte for
byte, quirks and all: `null` renders as `""`
rather than `undef`, empty maps and arrays produce no line at all, and `-e`
escapes only CR and LF. The flags `-r`, `-m` and `-e` kept their original
meanings.

## Documentation

- `man json-dump`
- `json-dump --help`
- [INSTALL.md][install] — extras, source installs, completion, manpage
- [VERSIONING.md][versioning] — what a major bump means, and what is promised
- [ROADMAP.md][roadmap] — planned work: SQL-style filtering, an
  interactive shell, out-of-core storage for structures larger than RAM,
  Ibis integration, and publication to document stores.
- [AUTHORS.md][authors] — who wrote it

## Shell completion

Completion for bash, fish and zsh lives in [contrib/completions/][completions].
Each queries `json-dump -L --porcelain` and `--list-templates --porcelain`, so
the candidates offered always match the optional packages actually installed and
the templates actually on your search path — no hard-coded lists to drift.
See [INSTALL.md][install-completion] for where to put them.

## Tests

```console
python3 -m unittest discover -s tests -v
```

The suite runs the original Perl script alongside the rewrite and compares the
output line for line when `perl` is available, and round-trips every installed
format.

The source distribution on PyPI carries the suite *and* everything it reads, so
the same command works from an unpacked tarball with no clone — see
[INSTALL.md][install-sdist] if you are packaging this for a distribution.

## Development

`make` is the front end. The git hooks and the GitHub workflows call the same
targets, so a gate means one thing everywhere.

```console
$ make                   # every target, with examples
$ make tools hooks       # one-time setup in a fresh clone
$ make precommit         # format, lint, the dependency-free check, tests
$ make check             # everything CI runs, in CI's order
```

Those targets build throwaway virtualenvs: one for the pinned linters, and one
per run of `make test-isolated`, which is what proves the package still works
installed with no extras at all. Building them needs a `python3` whose `venv`
module can bootstrap pip. Several distributions ship that separately
(`python3-venv` on Debian and Ubuntu, `python3-pip` on Fedora), so a minimal
container image usually has to install it before `make check` will run.
[uv](https://docs.astral.sh/uv/) covers the same need without it, and `make`
prefers uv whenever it is on `PATH`.

CI runs the tests on Python 3.11 through 3.14, in two installations each:
with every extra, and with **none** — the second being the one that catches an
optional import escaping to module scope, which works fine on every machine
that has the package. Security coverage is ruff's bandit rules and CodeQL over
the source, `pip-audit` over the optional dependency set, and `zizmor` over the
workflows themselves. See [SECURITY.md][security] for the threat model.

## License

Copyright 2026 Jeremy Melanson.

Licensed under the [Apache License, Version 2.0][license]. You may not use this
software except in compliance with the License. Unless required by applicable
law or agreed to in writing, it is distributed on an "AS IS" BASIS, WITHOUT
WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

<!-- Link targets are absolute because this README is also the PyPI project
     page, where a relative link resolves against pypi.org and 404s. Defined
     once here so a move or a branch rename is one edit rather than ten. -->

[install]: https://github.com/zish/json-dump/blob/master/INSTALL.md
[install-sdist]: https://github.com/zish/json-dump/blob/master/INSTALL.md#building-from-the-source-distribution
[install-completion]: https://github.com/zish/json-dump/blob/master/INSTALL.md#shell-completion
[roadmap]: https://github.com/zish/json-dump/blob/master/ROADMAP.md
[authors]: https://github.com/zish/json-dump/blob/master/AUTHORS.md
[versioning]: https://github.com/zish/json-dump/blob/master/VERSIONING.md
[security]: https://github.com/zish/json-dump/blob/master/SECURITY.md
[license]: https://github.com/zish/json-dump/blob/master/LICENSE
[templates]: https://github.com/zish/json-dump/tree/master/contrib/templates
[completions]: https://github.com/zish/json-dump/tree/master/contrib/completions
