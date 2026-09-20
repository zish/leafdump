# Installing leafdump

`leafdump` is a pure-Python command-line tool. There is nothing to compile,
and the core has **no dependencies at all** — every format beyond the built-in
set is an optional extra you add only if you need it.

- [Requirements](#requirements)
- [Install](#install)
- [Choosing extras](#choosing-extras)
- [Install from source](#install-from-source)
- [Run without installing](#run-without-installing)
- [Verify](#verify)
- [Shell completion](#shell-completion)
- [Manpage](#manpage)
- [Upgrading and uninstalling](#upgrading-and-uninstalling)
- [Troubleshooting](#troubleshooting)

## Requirements

| | |
| --- | --- |
| Python | **3.11 or newer** |
| OS | Linux, macOS, BSD, Windows |
| Compiler | none — no extension modules in the core |

Python 3.11 is the floor because TOML input uses the standard library's
`tomllib`, which landed in 3.11. Check what you have:

```console
$ python3 --version
Python 3.13.5
```

## Install

`leafdump` is an application rather than a library, so the tidiest options
install it into an environment of its own and put a single `leafdump` command
on your `PATH`.

### pipx (recommended)

```console
$ pipx install 'leafdump[all]'
```

Add a format later without reinstalling:

```console
$ pipx inject leafdump cbor2
```

### uv

```console
$ uv tool install 'leafdump[all]'
```

### pip, into a virtualenv

```console
$ python3 -m venv ~/.venvs/leafdump
$ ~/.venvs/leafdump/bin/pip install 'leafdump[all]'
$ ln -s ~/.venvs/leafdump/bin/leafdump ~/.local/bin/leafdump
```

### pip, into the current environment

```console
$ pip install leafdump            # core only
$ pip install 'leafdump[all]'     # the usual choice
```

On Debian, Fedora and other distributions that mark the system Python as
externally managed, a bare `pip install` will refuse to run — see
[Troubleshooting](#troubleshooting).

## Choosing extras

The quoting matters: `'leafdump[all]'` needs the quotes in bash and zsh,
which would otherwise try to glob the brackets.

| Install | Formats you get |
| --- | --- |
| `leafdump` | `perl`, `python`, `json`, `jsonl`, `repr`, and TOML **input** |
| `leafdump[all]` | the above **+** YAML, JSON5, TOON, TOML **output**, MessagePack, CBOR, NestedText |
| `leafdump[extras]` | BSON, Avro, protobuf-struct — the formats with sharper edges |
| `leafdump[all,extras]` | everything |

`[all]` is the sensible default: everything stable and schemaless. `[extras]`
is separate because those three carry caveats — BSON pulls in the whole of
`pymongo`, Avro is schema-first, and protobuf-struct coerces every number to a
double.

Individual formats, if you would rather be precise:

| Extra | Unlocks | Package |
| --- | --- | --- |
| `yaml` | YAML in/out | `PyYAML` |
| `json5` | JSON5 in/out | `json5` (or `pyjson5`, a faster C extension) |
| `toon` | TOON in/out | `toon-format` |
| `toml` | TOML **output** (input is stdlib) | `tomli-w` |
| `msgpack` | MessagePack in/out | `msgpack` |
| `cbor` | CBOR in/out | `cbor2` |
| `nestedtext` | NestedText in/out | `nestedtext` |
| `bson` | BSON in/out | `pymongo` |
| `avro` | Avro in/out | `fastavro` |
| `protobuf` | protobuf-struct in/out | `protobuf` |

```console
$ pip install 'leafdump[yaml,cbor]'
```

You never have to memorise this table. Ask for a format you do not have and
the error tells you what to install:

```console
$ leafdump --to cbor data.json
leafdump: error: support for writing 'cbor' is not installed.
  pip install cbor2  (or: pip install 'leafdump[cbor]')
```

`leafdump --help-format cbor` explains any format's dependencies and
round-trip caveats, whether or not it is installed.

## Install from source

```console
$ git clone https://github.com/zish/leafdump.git
$ cd leafdump
$ pip install '.[all]'
```

For development, install in editable mode so your edits take effect
immediately, and run the suite:

```console
$ pip install -e '.[all,extras]'
$ python3 -m unittest discover -s tests -v
```

The suite round-trips every format you have installed, so a fuller install
means a stricter test run. It also compares output against the Perl script this
tool grew out of, byte for byte, whenever `perl` is on your `PATH`; that test
skips itself otherwise. The Perl script needs no CPAN modules — `JSON::PP`,
`Data::Dumper` and `Getopt::Long` have all been core Perl for years.

### Building from the source distribution

Distribution packagers build from the `.tar.gz` on PyPI rather than from a
clone, and it carries everything the suite reads: the worked template examples,
the sample documents, and the Perl script the parity test compares against. No
checkout and nothing installed:

```console
$ tar xf leafdump-*.tar.gz
$ cd leafdump-*/
$ python3 -m unittest discover -s tests -v
```

The tests import the package from the tree beside them, which works because the
core needs nothing outside the standard library. Any optional format whose
package is absent is skipped, not failed, so a bare build environment gives a
clean run.

## Run without installing

Because the core is pure standard library, a checkout is already runnable:

```console
$ git clone https://github.com/zish/leafdump.git
$ cd leafdump
$ python3 -m leafdump config.json
```

That is a genuine installation strategy, not just a smoke test. To get a
command on your `PATH` without a package manager:

```console
$ cat > ~/.local/bin/leafdump <<'SH'
#!/bin/sh
exec python3 -m leafdump "$@"
SH
$ chmod +x ~/.local/bin/leafdump
```

Set `PYTHONPATH` to the checkout, or move the `leafdump/` package somewhere
already on `sys.path`. Optional formats still work if their packages are
importable by that interpreter.

## Single-file binary

For a machine with no Python at all — a scratch container, a jump host, a CI
image you cannot install packages into — leafdump can be compiled into one
self-contained executable with [Nuitka](https://nuitka.net/):

```console
$ make binary
/home/you/leafdump/bin/leafdump  (14M)
leafdump 0.2.0
python 3.13.15
formats enabled: json, jsonl, repr, perl, python, toml, yaml, json5, toon, msgpack, cbor, nestedtext
```

Nuitka is **not** a dependency of this project and is not installed until this
target is run. `make tools`, `make test` and `make check` never fetch it; the
first `make binary` creates `.venv-build/`, installs Nuitka there, and leaves
the rest of the checkout alone. `make distclean` removes it again.

### Choosing what goes in

The binary can only offer formats that were importable when it was compiled,
because a compiled program cannot `pip install` anything later. `EXTRAS`
selects the set, using the same names as the pip extras:

```console
$ make binary                     # every stable format (the default)
$ make binary EXTRAS=             # core only — no third-party code at all
$ make binary EXTRAS=all,extras   # ...plus bson, avro and protobuf
$ make binary EXTRAS=yaml,cbor    # ...or name them individually
```

`make binary-report` lists what the current build environment would compile in
before spending the minutes to compile it, and `make binary-check` runs the
finished binary, compares its `--list-formats` against that same list, and
round-trips a document through every format it claims. Compiling successfully
proves less here than it looks: leafdump reaches every optional codec through
a module name in a *string*, which a compiler cannot follow, so a binary that
bundled none of them still builds and still runs.

### What it costs

A single file is a deployment win, not a speed win. Measured on this tree, one
`leafdump --version`:

| | startup |
| --- | --- |
| single-file binary | 150 ms |
| `--standalone` directory build | 42 ms |
| `python -m leafdump` | 46 ms |

The onefile bootstrap unpacks its payload and execs a second process, and that
costs about 110 ms every time the program runs. For a filter used inside a tight
shell loop, running the source is faster. For deployment onto a machine that has
no interpreter, the single file is the only one of the three that works.

Two other trade-offs worth knowing before shipping one:

- **Security updates cannot reach it.** The codec libraries are compiled in at
  the version they were when it was built. When one of them ships a fix, the
  binary has to be rebuilt — `pip install --upgrade` cannot help. See
  [SECURITY.md](SECURITY.md).
- **It is platform-specific.** The build produces a binary for the OS and
  architecture it ran on. The release workflow builds Linux and macOS; see
  `.github/workflows/release.yml`.

### Licensing

Nuitka is AGPLv3, which surprises people, so it is worth stating plainly: the
compiled program is **not** encumbered by it. Nuitka carries a runtime-library
exception in the manner of GCC's, granting permission to "propagate a work of
Target Code ... under terms of your choice". A binary built from this
Apache-2.0-licensed tree stays Apache 2.0. What the AGPL does cover is Nuitka
itself — modifying the compiler and distributing that is the part with
obligations.

### If the build fails

`make binary` compiles C, so it needs the things that implies:

- **`Python.h` not found** — the interpreter's development headers are missing.
  Install your distro's `python3-devel` / `python3-dev`, or point the build at
  an interpreter that has them: `make binary PY=python3.13`. A `uv`-managed
  interpreter (`uv python install 3.13`) ships its own headers and works.
- **`patchelf` not found** — this should not happen; the Makefile installs the
  PyPI wheel into the build environment and puts it on `PATH` for exactly this
  reason. If it does, `dnf install patchelf` (or `apt install patchelf`) is the
  system-package equivalent.
- **`ensurepip` is not available** — this interpreter cannot create
  virtualenvs. Install `python3-venv` / `python3-pip`, or install
  [uv](https://docs.astral.sh/uv/), which the Makefile prefers when present and
  which does not need it.

## Verify

```console
$ leafdump --version
leafdump 0.2.0
python 3.13.5
formats enabled: json, jsonl, repr, perl, python, toml, yaml, msgpack
```

`--version` reports the interpreter actually running the tool and the formats
it can see — the fastest way to confirm you installed the extras into the same
environment as the command.

For the full picture, including what is missing and how to get it:

```console
$ leafdump --list-formats
  json             in/out  text    available
    JSON (RFC 8259) -- the reference format.
    aliases: js
...
! cbor             --      binary  NOT INSTALLED
    CBOR (RFC 8949) -- IETF-standard binary object representation.
    enable with: pip install cbor2  (or: pip install 'leafdump[cbor]')
```

A format marked `in` but not `in/out` is available in one direction only —
`toml` on a core install is the usual example.

## Shell completion

Completion scripts live in [contrib/completions/](contrib/completions/). Each
asks the installed binary for its format list (`leafdump -L --porcelain`), so
the candidates offered always match the optional packages you actually have.
No completion script ever needs updating when you add an extra.

A packaged install already places these files, but under the *environment's*
prefix. That is `/usr/share/...` for a system install and
`~/.local/share/...` for `pip install --user`, both of which the shells scan
by default; for a venv or pipx install it is `<env>/share/...`, which they do
not. Copy them where your shell will find them:

```console
# bash — system-wide
$ sudo cp contrib/completions/leafdump.bash \
       /usr/share/bash-completion/completions/leafdump

# bash — just me
$ mkdir -p ~/.local/share/bash-completion/completions
$ cp contrib/completions/leafdump.bash \
     ~/.local/share/bash-completion/completions/leafdump

# fish
$ cp contrib/completions/leafdump.fish ~/.config/fish/completions/

# zsh — any directory on $fpath, keeping the leading underscore
$ sudo cp contrib/completions/_leafdump /usr/share/zsh/site-functions/
```

Start a new shell afterwards. For zsh in a directory of your own, add it to
`$fpath` before `compinit` runs:

```zsh
fpath=(~/.local/share/zsh/site-functions $fpath)
autoload -Uz compinit && compinit
```

To try the bash completion without installing anything, `source
contrib/completions/leafdump.bash`.

## Pseudocode templates

The path-dump notations (`perl`, `python`, `javascript`, `go`, `r`, `jq`, ...)
are built in and need no installation:

```console
$ leafdump --list-templates       # every notation, with an example of each
$ leafdump --template go data.json
```

A template of your own is a JSON (or TOML) file. Drop it in the per-user
template directory and it becomes selectable by name:

```console
$ mkdir -p ~/.config/leafdump/templates
$ leafdump --dump-template javascript > ~/.config/leafdump/templates/kotlin.json
$ $EDITOR ~/.config/leafdump/templates/kotlin.json
$ leafdump --template kotlin data.json
```

`$XDG_CONFIG_HOME` moves that directory. `$JSON_DUMP_TEMPLATES` adds more,
separated like `$PATH`, searched first — which is how a template travels with
a project rather than with a person:

```console
$ JSON_DUMP_TEMPLATES=$PWD/.leafdump/templates leafdump --template house-style data.json
```

Built-in names always resolve first, so no file can quietly redefine `perl`.
`leafdump --help-template` documents every field, and
[contrib/templates/](contrib/templates/) has three worked examples.

## Manpage

A system or `--user` install puts `leafdump.1` somewhere `man` already looks:

```console
$ man leafdump
```

Inside a venv or pipx environment it lands in `<env>/share/man`, which is off
the default search path. Point `MANPATH` at it or read the file directly:

```console
$ MANPATH="$VIRTUAL_ENV/share/man:$MANPATH" man leafdump
$ man ./man/leafdump.1        # straight from a checkout
```

## Upgrading and uninstalling

```console
$ pipx upgrade leafdump
$ pip install --upgrade 'leafdump[all]'
```

```console
$ pipx uninstall leafdump
$ pip uninstall leafdump
```

`pip uninstall` removes the package and the `leafdump` command but leaves the
optional format packages behind; uninstall those separately if you want them
gone. Completion scripts and the manpage you copied by hand are yours to
remove.

## Troubleshooting

**`error: externally-managed-environment`** — your distribution protects the
system Python from `pip` (PEP 668). Use `pipx install 'leafdump[all]'`, `uv
tool install`, or a virtualenv. `pip install --break-system-packages` works but
is the option to reach for last.

**`leafdump: command not found` after a successful install** — the script
directory is not on your `PATH`. It is `~/.local/bin` for `pip install --user`
and for pipx (`pipx ensurepath` fixes it permanently), or `<venv>/bin` for a
virtualenv you have not activated. Meanwhile `python3 -m leafdump` always
works if the package is importable, no `PATH` entry required.

**A format you installed still reports NOT INSTALLED** — you almost certainly
installed it into a different environment than the one running `leafdump`.
Compare the interpreter in `leafdump --version` against the `pip` you used.
With pipx, use `pipx inject leafdump <package>` rather than a bare `pip
install`, which cannot reach inside the isolated environment.

**BSON fails to import** — the `bson` module has to be the one bundled with
`pymongo`. The unrelated PyPI distribution literally named `bson` installs a
conflicting module of the same name; if both are present, uninstall the
standalone `bson`.

**TOON picks up the wrong package** — prefer `toon-format`. The alternative
distribution `python-toon` installs a module named `toon`, which collides with
an unrelated neuroscience package.

**`SyntaxError` on install or first run** — you are on Python 3.10 or older.
Install under a newer interpreter: `python3.11 -m pip install leafdump`, or
`pipx install --python python3.13 'leafdump[all]'`.
