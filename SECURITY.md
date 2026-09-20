# Security

## Reporting a vulnerability

Please report security issues privately, through GitHub's **Report a
vulnerability** button on the repository's Security tab, rather than as a public
issue.

Useful in a report: the input that triggers it, what it makes leafdump do, and
whether the reporter needed anything beyond the ability to hand it a file.
leafdump is a small project with no security team and no bounty; expect a
human, not an SLA.

## Scope

leafdump is a **filter you run yourself**. There is no service, no network
listener and no persistent state — it reads a file or a pipe, writes to another,
and exits. The security question is therefore a narrow and specific one: *what
can a malicious input document do?*

The surfaces, roughly in order of consequence:

**Parsing untrusted documents.** This is the entire job, so it is the entire
attack surface. Two deliberate choices bound it:

- YAML is read with `yaml.safe_load_all` and written with `yaml.safe_dump`. The
  unsafe loaders, which construct arbitrary Python objects named in the
  document, are never reachable — not through a flag, not through a config.
- The `repr` codec uses `ast.literal_eval`, not `eval`. It parses a literal and
  refuses anything else.

There is no pickle or marshal codec, and adding one would be a security change
rather than a feature.

**Resource exhaustion.** Every codec reads the whole document into memory, and
the merge and dedup passes hold it there. A large or pathologically nested input
can exhaust memory or take a very long time — this is a real limitation and is
documented as one, not treated as a vulnerability. The one nesting failure that
*is* handled is stack depth: the tree walk is iterative
(`flatten.walk_iter`) precisely so that deep input cannot raise `RecursionError`
and truncate a dump partway through.

**The optional binary codecs.** msgpack, cbor2, fastavro, pymongo's bson and
protobuf are C extensions that parse untrusted bytes. Memory-safety bugs in them
are theirs, but leafdump is what feeds them, so please report anything found
that way here as well as upstream. `make vuln` audits this set against the
advisory database on every push and weekly on a schedule.

**The single-file binary.** A binary built by `make binary` has its optional
codec libraries compiled *into* it. That is the point of it, and it is also its
one security disadvantage over a pip install: when one of those libraries ships
a fix, `pip install --upgrade` cannot reach it. The binary has to be rebuilt and
redistributed. Prefer the package where you have a Python to install it into;
choose the binary when you do not.

**The path dumps are not code.** The pseudocode templates produce lines that
*look* like Perl, Python, JavaScript, Go, R, SQL or shell. They are formatted
for reading and grepping. Each notation escapes strings the way its language
does — that is what makes the output unambiguous, not what makes it safe.
Feeding a dump of an untrusted document to an interpreter, a shell or a
database is executing a transformation of that document, and nothing in the
renderers is written with that in mind. The `dotted` notation quotes nothing at
all and is the clearest case: a value can contain the separator.

**Template files are data.** `--template FILE` reads JSON or TOML into a fixed
set of string fields. There is no import, no eval, and no field whose value
becomes a callable; an unknown field, quoting style or `%` placeholder is an
error rather than something ignored. A template can still *say* anything — it
decides how values are quoted — so a template from an untrusted source is a
choice about output, in the same way the paragraph above is. Templates are
found only where the user points: `$JSON_DUMP_TEMPLATES`, the XDG config
directory, or an explicit path. The current directory is not searched, and
built-in names cannot be shadowed by a file.

## Not in scope

- Vulnerabilities in PyYAML, msgpack, cbor2, fastavro, pymongo or protobuf
  themselves. Report those upstream; they will reach here through `make vuln`.
- Installing the wrong `bson`. The `bson` needed is the module bundled with
  **pymongo**; the unrelated PyPI distribution of the same name is not
  compatible, and `leafdump --help-format bson` says so. Installing the wrong
  one is a footgun, and a documented one.
- Memory or time consumed by an input the operator chose to pass in. See
  *Resource exhaustion* above.
- Anything requiring an attacker who can already run commands as you.
