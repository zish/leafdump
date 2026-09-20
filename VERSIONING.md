# Versioning

leafdump follows [Semantic Versioning 2.0.0](https://semver.org/). This
document says what that means in practice: which parts of the tool are a
promise, which are still moving, and which were never an interface at all.

The distinction matters more here than for most tools. leafdump's output is
meant to be piped into `grep`, `cut` and `awk`, so a change to what a line
looks like breaks scripts whatever it does to the code.

## The first published release is 1.0.0

leafdump is not yet on PyPI. The release that puts it there will be **1.0.0**,
not a 0.x.

That is a considered choice rather than bravado. The default notation and
`--perl-compat` are compared byte-for-byte against the original Perl script by
the test suite, on every change, using real documents — so the compatibility
question this tool is most often asked is already answered mechanically, and
has been for its whole life. Publishing that as 0.x would advertise "this may
break you" about the one thing that provably will not.

Two surfaces genuinely are young. They are named as provisional below rather
than holding the entire tool back to keep them fluid.

## What each part of the number means

| Bump | Means |
| --- | --- |
| **MAJOR** | Something a script could be relying on changed. See *Stable surfaces*. |
| **MINOR** | Something was added — a format, a notation, a flag, a template field. Existing invocations keep working. |
| **PATCH** | A fix that does not change documented output, or a packaging change. |

## Stable surfaces

These do not change without a major version.

- **The default pseudo-perl notation** — `ROOT.{key}.0."value"`, exactly as it
  renders today.
- **`--perl-compat` output**, which reproduces the original `json_dump.pl`
  byte for byte. That script's output is frozen in the repository and every
  change is compared against it, so this is checked rather than claimed.
- **Command-line flags** — their long names, their short forms, and what they
  do.
- **Format and template names, and their aliases.** `--to yaml` and `-t perl`
  will not start meaning something else.
- **The other built-in notations** — python, javascript, go, jq and the rest.
- **Exit codes.**

A new *value* appearing in a list is not a break. A format added to
`--list-formats` is a minor release, not a major one.

## Provisional surfaces

These may change in a minor release. They are written down here precisely so
that nobody builds on them believing otherwise.

- **`--porcelain` output.** The tab-separated columns from `--list-formats
  --porcelain` and `--list-templates --porcelain`. The shipped shell
  completions parse these, but they ship *with* the tool and change with it, so
  a column change is invisible to them. A script of yours is not so lucky.
- **The template file schema.** The fields `--template FILE` accepts. Fields
  get added over time, and an unknown field is rejected rather than ignored —
  so a template written for a newer leafdump will not load on an older one,
  and will say so rather than rendering subtly wrong output. Note the version a
  field arrived in if you share a template around.

## Not an interface at all

Everything importable from the `leafdump` package except `__version__` and
`__author__` is internal. The registry, the codecs, the tree walk and the
template loader may change in any release, including a patch.

leafdump is a command-line tool that happens to be written in Python. To use
it from Python, run it as a subprocess — or pin an exact version and read the
source you pinned.

## Supported versions

Fixes, security fixes included, go to the **latest minor of the current
major**. There are no long-term support branches. This is a small project with
one maintainer, and a backport policy it could not honour would be worse than
none at all.

If you package leafdump for a distribution and need a fix on an older line,
open an issue. A patch on top of an old tag is usually easy; it is just not
promised in advance.

## Pinning

For a script whose output format matters, pin the major version:

```
leafdump >=1,<2
```

That is the range over which everything under *Stable surfaces* holds. An exact
pin is only needed if you depend on something under *Provisional surfaces*.
