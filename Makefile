# leafdump — build, lint, test and packaging entrypoints.
#
# Every gate is defined exactly once, here. The git hooks (lefthook.yml) and the
# CI workflows both invoke these targets rather than restating the commands, so
# "it passed locally" and "it passed in CI" cannot drift apart.
#
# Nothing in this file is needed to *use* leafdump. The package itself has no
# dependencies at all — `make core-check` is the target that proves it; everything
# below installs into throwaway virtualenvs under this directory, and `make clean`
# removes every trace.

SHELL := /usr/bin/env bash

PY   ?= python3
BIN   := $(CURDIR)/bin
BUILD := $(CURDIR)/build

# Pinned tool versions. Bump here; the hooks and CI follow automatically, since
# both go through these targets.
#
# These are deliberately *not* declared in pyproject.toml. Extras there are part
# of the published package metadata — a `dev` extra would show up in
# `pip show leafdump` and invite `pip install leafdump[dev]` from people who
# only want to run the thing. The developer toolchain is a property of this
# checkout, so it lives in this checkout's build file.
RUFF_VERSION      ?= 0.16.6
MYPY_VERSION      ?= 2.3.1
PIP_AUDIT_VERSION ?= 2.10.1
ZIZMOR_VERSION    ?= 1.30.1
LEFTHOOK_VERSION  ?= 2.1.12
BUILD_VERSION     ?= 1.6.0

# Nuitka is pinned here too, but note what is *absent*: no target below depends
# on it except `binary`. `make tools` does not install it, `make check` does not
# invoke it, and a clone that never runs `make binary` never downloads it.
NUITKA_VERSION    ?= 4.2.1
# patchelf is a hard requirement of Nuitka's standalone mode on Linux, and the
# error when it is missing is a FATAL at the very end of an otherwise successful
# compile. The PyPI wheel is used rather than the distro package so that a
# `make binary` on a fresh Linux clone needs no root and no system packages;
# the recipe puts the build venv's bin/ on PATH so Nuitka can find it there.
# (Nuitka needs >= 0.13 for --add-rpath; the wheel is well past that.)
PATCHELF_VERSION  ?= 0.19.1.0

# Which optional formats get compiled into the binary. Empty means core only —
# the stdlib formats that need no third-party package at all.
#
# `all` is the default for the same reason pyproject.toml calls it "the sensible
# default install": someone downloading a single-file binary cannot pip-install
# a missing codec into it afterwards, so the batteries have to be in the box.
EXTRAS ?= all

# The same knob for `test-isolated`, with the opposite default and a separate
# name on purpose. Sharing one variable made `make test-isolated` -- documented
# and exampled as the zero-dependency run -- quietly install everything, because
# the binary's default won. The two targets want opposite answers to the same
# question, so they get two variables.
TEST_EXTRAS ?=

# Read from the package rather than restated, so a release bumps one file.
VERSION := $(shell $(PY) -c 'import leafdump; print(leafdump.__version__)' 2>/dev/null)

# Branch that lint-new measures "new" against.
MAIN_BRANCH ?= master

PREFIX      ?= /usr/local
MANDIR      ?= $(PREFIX)/share/man
BASHCOMPDIR ?= $(PREFIX)/share/bash-completion/completions
FISHCOMPDIR ?= $(PREFIX)/share/fish/vendor_completions.d
ZSHCOMPDIR  ?= $(PREFIX)/share/zsh/site-functions

# --------------------------------------------------------------- environments

# Two virtualenvs, kept apart on purpose.
#
# .venv-tools holds the linters and never holds a runtime dependency of
# leafdump. .venv-build holds Nuitka *and* whichever optional format packages
# are being compiled in — Nuitka bundles what it can import, so the build env is
# what selects the binary's feature set. Merging the two would silently make the
# linters' transitive dependencies (pip-audit alone pulls in a dozen) candidates
# for inclusion in a shipped binary.
TOOLS_ENV := $(CURDIR)/.venv-tools
BUILD_ENV := $(CURDIR)/.venv-build

# A virtualenv is bound to the absolute path it was built at: every console
# script in it opens with a shebang naming its own bin/python. Move the
# directory and `ruff` keeps working, because it is a binary with no shebang,
# while `mypy` dies with "cannot execute: required file not found" -- which
# reads like a missing file rather than a moved one.
#
# Checked here, at parse time, and not inside mkvenv, because the install
# stamps live *inside* the venv. A venv that moves brings its stamps with it,
# so make sees every tool as already installed, never calls mkvenv, and runs
# the broken script. This is the only place early enough to matter. Renaming a
# checkout is the usual way in; CI hit it by renaming the repository, which
# moved the workspace under it.
define stale_venv
$(if $(wildcard $(1)/.),$(if $(filter $(1),$(shell cat $(1)/.built-for 2>/dev/null)),,$(1)))
endef
STALE_VENVS := $(strip $(call stale_venv,$(TOOLS_ENV)) $(call stale_venv,$(BUILD_ENV)))
ifneq ($(STALE_VENVS),)
$(info note: removing virtualenv(s) built for another path: $(STALE_VENVS))
$(info       each target reinstalls what it needs; run `make tools` to
$(info       restore the rest, including the lefthook the git hooks call.)
$(shell rm -rf $(STALE_VENVS))
endif

# uv is used when it is present because it is much faster and, more usefully,
# because it creates virtualenvs on systems where the stdlib `venv` cannot:
# a Python built without `ensurepip` (common in container and distro-split
# installs) makes `python3 -m venv` fail outright. Neither path is required —
# whichever runs, the result is the same layout at $(1)/bin/, so every target
# below is written against that and does not care which one made it.
UV ?= $(shell command -v uv 2>/dev/null)

define mkvenv
	@if [ ! -x "$(1)/bin/python" ]; then \
	  if [ -n "$(UV)" ]; then $(UV) venv -q --python $(PY) "$(1)"; \
	  else $(PY) -m venv "$(1)" || { \
	    echo "error: could not create $(1)."; \
	    echo "  $(PY) has no working 'venv' module (missing ensurepip?)."; \
	    echo "  Install uv, or your distro's python3-venv / python3-pip package."; \
	    exit 1; }; \
	  fi; \
	fi
	@echo "$(1)" > "$(1)/.built-for"
endef

# The pip branch checks for pip rather than assuming it: a virtualenv created
# by uv does not contain one, so a clone that installed uv, built its envs, and
# then lost uv from PATH would otherwise fail with a bare "No module named pip".
define venv_install
	@if [ -n "$(UV)" ]; then $(UV) pip install -q --python "$(1)/bin/python" $(2); \
	 elif [ -x "$(1)/bin/pip" ]; then "$(1)/bin/python" -m pip install -q $(2); \
	 else echo "error: $(1) has no pip, and uv is not on PATH to stand in for it."; \
	      echo "  The virtualenv was probably created by uv. Either put uv back on"; \
	      echo "  PATH, pass it explicitly (make UV=/path/to/uv ...), or discard the"; \
	      echo "  environments with 'make distclean' and build them again."; \
	      exit 1; fi
endef

RUFF      := $(TOOLS_ENV)/bin/ruff
MYPY      := $(TOOLS_ENV)/bin/mypy
PIP_AUDIT := $(TOOLS_ENV)/bin/pip-audit
ZIZMOR    := $(TOOLS_ENV)/bin/zizmor
LEFTHOOK  := $(TOOLS_ENV)/bin/lefthook

# One stamp file per pinned tool, with the version in the *name*. Bumping a pin
# above therefore names a stamp that does not exist, and the tool is reinstalled
# — the same effect as version-stamping a binary's filename, which is the only
# way to get it when the package manager installs to a fixed path.
define tool_rule
$(TOOLS_ENV)/.stamp-$(1)-$(2):
	$$(call mkvenv,$$(TOOLS_ENV))
	$$(call venv_install,$$(TOOLS_ENV),$(3)==$(2))
	@rm -f $$(TOOLS_ENV)/.stamp-$(1)-*
	@touch $$@
endef

$(eval $(call tool_rule,ruff,$(RUFF_VERSION),ruff))
$(eval $(call tool_rule,mypy,$(MYPY_VERSION),mypy))
$(eval $(call tool_rule,pip-audit,$(PIP_AUDIT_VERSION),pip-audit))
$(eval $(call tool_rule,zizmor,$(ZIZMOR_VERSION),zizmor))
$(eval $(call tool_rule,lefthook,$(LEFTHOOK_VERSION),lefthook))
$(eval $(call tool_rule,build,$(BUILD_VERSION),build))

NEED_RUFF      := $(TOOLS_ENV)/.stamp-ruff-$(RUFF_VERSION)
NEED_MYPY      := $(TOOLS_ENV)/.stamp-mypy-$(MYPY_VERSION)
NEED_PIP_AUDIT := $(TOOLS_ENV)/.stamp-pip-audit-$(PIP_AUDIT_VERSION)
NEED_ZIZMOR    := $(TOOLS_ENV)/.stamp-zizmor-$(ZIZMOR_VERSION)
NEED_LEFTHOOK  := $(TOOLS_ENV)/.stamp-lefthook-$(LEFTHOOK_VERSION)
NEED_BUILD     := $(TOOLS_ENV)/.stamp-build-$(BUILD_VERSION)

.DEFAULT_GOAL := help

# Renders "name<separator>description" lines as an aligned two-column list with
# the left column bold. Two comment markers feed it, both read straight out of
# this file: `## target: what it does`, and `#> make command  # what it does`
# for the Examples section. A target and any worked example of it are therefore
# written together, where the recipe is, and cannot drift from it.
#
# It splits on the *first* separator only, via match()/substr() rather than FS:
# a description is free text and contains colons of its own ("(default: none)"),
# and an FS-based split would silently truncate at the first of them.
#
# The column width is measured rather than fixed, because a hand-tuned %-12s
# fails silently — the first name longer than the pad pushes its own row out of
# line and nothing says so. It is pasted into the format string rather than
# passed as a %-*s argument: `*` is a printf(3) feature that POSIX awk does not
# promise, and this file gets read by more than one awk.
COLUMNIZE = awk -v sep=$(1) '{ i = match($$0, sep); n[NR] = substr($$0, 1, i - 1); \
	d[NR] = substr($$0, i + RLENGTH); if (length(n[NR]) > w) w = length(n[NR]) } \
	END { fmt = "  \033[1m%-" w "s\033[0m  %s\n"; for (j = 1; j <= NR; j++) printf fmt, n[j], d[j] }'

## help: list every target, with examples of the common invocations
.PHONY: help
help:
	@printf '\033[1mleafdump %s\033[0m\n\n\033[1mTargets\033[0m\n' '$(VERSION)'
	@grep -hE '^## ' $(MAKEFILE_LIST) | sed 's/^## //' | $(call COLUMNIZE,': *')
	@printf '\n\033[1mExamples\033[0m\n'
	@grep -hE '^#> ' $(MAKEFILE_LIST) | sed 's/^#> //' | $(call COLUMNIZE,' +# ')

#> make                             # the default goal: this help
#> make tools hooks                 # one-time setup, in a fresh clone
#> make check                       # everything CI runs, in CI's order

# ---------------------------------------------------------------------- lint

## fmt: apply formatting fixes in place
.PHONY: fmt
fmt: $(NEED_RUFF)
	$(RUFF) format .
	$(RUFF) check --select I --fix .

## fmt-check: fail on unformatted code without modifying anything
.PHONY: fmt-check
fmt-check: $(NEED_RUFF)
	$(RUFF) format --check --diff .

## lint: run the ruff rule set (correctness, bugbear, and the bandit checks)
.PHONY: lint
lint: $(NEED_RUFF)
	$(RUFF) check .

#> make lint-fix                    # apply the lint fixes ruff can make itself
## lint-fix: run the rule set and apply the fixes ruff can make itself
.PHONY: lint-fix
lint-fix: $(NEED_RUFF)
	$(RUFF) check --fix .

# `lint` above takes the whole tree, which is the right default because the tree
# is clean and keeping it that way is cheaper than paying a backlog down twice.
# This is the quick pass for a long-lived branch: the files it touched, nothing
# else. Ruff has no equivalent of golangci-lint's --new-from-merge-base -- it
# lints files, not line ranges -- so this is file-granular and will report a
# pre-existing finding in a file the branch happens to touch.
#> make lint-new MAIN_BRANCH=main   # lint just the files this branch touched
## lint-new: lint only the files this branch changed
.PHONY: lint-new
lint-new: $(NEED_RUFF)
	@files=$$(git diff --name-only --diff-filter=d $(MAIN_BRANCH)... -- '*.py' 2>/dev/null); \
	 if [ -z "$$files" ]; then \
	   echo "no Python files changed against $(MAIN_BRANCH)"; \
	 else \
	   echo "$$files" | tr '\n' ' '; echo; $(RUFF) check $$files; \
	 fi

## typecheck: mypy over the package
.PHONY: typecheck
typecheck: $(NEED_MYPY)
	$(MYPY) leafdump scripts

# ---------------------------------------------------------------------- test

# Plain `unittest` on whatever interpreter is in front of you. What that
# interpreter has installed decides how much of the suite is exercised, which is
# the point: the same command is the core gate and the full gate, and CI varies
# only the environment around it.
#
# TestPerlParity is part of this and self-skips when perl(1) is absent. CI has
# perl, so the byte-identical `--perl-compat` invariant is checked there on
# every push.
## test: run the unit tests against the current interpreter
.PHONY: test
test:
	$(PY) -m unittest discover -s tests -v

# The zero-dependency invariant is the project's headline promise and nothing
# in the tree enforced it until this target: a stray `import yaml` at module
# scope would keep passing on any developer machine that happens to have PyYAML,
# and only break for the person who ran `pip install leafdump` with no extras.
#
# EXTRAS= (empty) is that person's environment, reproduced exactly.
# The environment is built and thrown away on every run, so what it proves is
# what a `pip install leafdump` gets and nothing that happens to be lying
# around in this checkout. Building it needs either a `python3` whose `venv`
# can bootstrap pip -- Debian and Fedora split that into python3-venv, so a
# container image often lacks it -- or uv; mkvenv says which when neither is
# there.
#> make test-isolated                    # prove the core still runs with zero deps
#> make test-isolated TEST_EXTRAS=all    # ...and again with every optional format
## test-isolated: run the suite in a fresh venv holding only TEST_EXTRAS (default: none)
.PHONY: test-isolated
test-isolated:
	@rm -rf $(CURDIR)/.venv-test
	$(call mkvenv,$(CURDIR)/.venv-test)
	$(call venv_install,$(CURDIR)/.venv-test,$(if $(TEST_EXTRAS),'.[$(TEST_EXTRAS)]','.'))
	$(CURDIR)/.venv-test/bin/python -m unittest discover -s tests -v
	@rm -rf $(CURDIR)/.venv-test

# The zero-dependency invariant is checked by importing the package and looking
# at what came with it, so it is meaningful in any environment -- including this
# one, with every extra installed. That is the point: it fails on a module-scope
# `import yaml` even on the machine where PyYAML is present, which is the only
# machine the mistake is ever made on.
## core-check: assert importing leafdump pulls in nothing third-party
.PHONY: core-check
core-check:
	$(PY) scripts/check_core_isolation.py

# The committed completion scripts read their *format* lists from the binary at
# runtime, so those can never go stale. The flag lists are another matter: they
# are typed out in all three files, and a flag added to cli.py is invisible to
# them. That is what this compares.
## completions-check: fail if a CLI flag is missing from a shell completion
.PHONY: completions-check
completions-check:
	$(PY) scripts/check_completions.py

# This repository is public; the internal documentation is not, and neither is
# whatever assistant the next contributor brings with them. .gitignore is
# necessary and nowhere near sufficient -- it says nothing about a path that is
# already tracked, and `git add -f` overrides it silently. Both failures are
# invisible in a diff and permanent once pushed, because deleting a file does
# not remove it from the commits that carried it.
#
# The named tool list is a deny-list and will always be behind the tools, so the
# guarantee does not rest on it: every top-level entry is enumerated, and an
# unrecognised one fails. Assistant config lands at the root, which is how that
# catches tools nobody has heard of yet. The names only make the error better.
## docs-check: fail if internal docs or agent tooling reach the published tree
.PHONY: docs-check
docs-check:
	$(PY) scripts/check_docs.py

# One number, named in two files and sometimes a tag. pyproject.toml reads the
# version out of the package rather than restating it, so pip metadata and
# `--version` cannot drift; this checks that arrangement is still in place, that
# the manpage agrees, and -- the one that cannot be repaired -- that a `v*` tag
# on HEAD matches the tree it points at. release.yml builds whatever the tag
# points at, and a package index keeps version numbers forever: a wrong upload
# can only be yanked, which hides the number without freeing it.
#> make version-check                   # before tagging, and in every gate run
## version-check: fail if anything naming a version disagrees with the package
.PHONY: version-check
version-check:
	$(PY) scripts/check_version.py

# ------------------------------------------------------------------- security

# Two different questions, deliberately kept as two targets.
#
# `vuln` asks whether the packages leafdump *depends on* have known CVEs. With
# no required dependencies the interesting surface is the optional set, so this
# audits the project with every extra resolved — the maximal install, which is
# what `pip install 'leafdump[all]'` gives someone.
#
# `audit` asks whether the CI configuration itself is exploitable: script
# injection through untrusted interpolation, over-broad token permissions,
# unpinned third-party actions. A workflow is code with credentials, and it is
# the one part of this repository that a linter for Python will never read.
## vuln: known vulnerabilities in the optional dependency set (pip-audit)
.PHONY: vuln
vuln: $(NEED_PIP_AUDIT)
	$(PIP_AUDIT) --strict --progress-spinner=off .

# zizmor runs offline unless it has a token, and offline it skips every audit
# that has to ask GitHub something. One of those is ref-version-mismatch, which
# fires when an upstream action moves a floating tag out from under a SHA pin --
# the finding that broke a push after github/codeql-action moved `v4` off the
# commit our `# v4` comment claimed it was.
#
# That made this gate mean less here than in CI, where the job already has a
# GITHUB_TOKEN: it passed locally and failed on push, over a finding the local
# run never looked for. Borrowing gh(1)'s token closes the gap wherever one is
# available; where none is, zizmor says it is offline rather than pretending.
# Recipe silenced so no token can reach a log, however it was supplied.
## audit: static analysis of the GitHub Actions workflows (zizmor)
.PHONY: audit
audit: $(NEED_ZIZMOR)
	@GH_TOKEN="$${GH_TOKEN:-$$(gh auth token 2>/dev/null)}" \
	    $(ZIZMOR) --persona=regular .github/

# --------------------------------------------------------------------- build

# Built with the pinned `build` frontend from .venv-tools rather than whatever
# the ambient interpreter happens to have, so that a release artifact does not
# depend on the state of the machine that cut it.
#
# The egg-info directory goes with it, and that is not tidiness. setuptools
# caches the sdist's file list in leafdump.egg-info/SOURCES.txt and reads it
# back on the next build -- "reading manifest file" appears in the log *before*
# "reading manifest template". A path dropped from MANIFEST.in therefore keeps
# shipping, because the cache still names it, and the build reports success.
#
# What makes that worth a line of the recipe rather than a note somewhere: a
# fresh checkout has no cache, so CI and the release job never see it. The
# difference is local-only, which means the tarball a developer builds to
# inspect can disagree with the one CI builds from the same commit -- and the
# one being inspected is the one that looks right.
## dist: build the sdist and wheel into dist/
.PHONY: dist
dist: $(NEED_BUILD)
	@rm -rf $(CURDIR)/dist $(CURDIR)/*.egg-info
	$(TOOLS_ENV)/bin/python -m build --outdir $(CURDIR)/dist

# --- the source tarball -----------------------------------------------------
#
# MANIFEST.in is generated, not written. scripts/manifest_in.py holds the
# patterns with a reason beside each one, and reads the manpage and completion
# paths straight out of pyproject.toml so that those stay named in one place.
# Patterns rather than filenames, so a fourth worked example or a fifth parity
# sample is carried with no edit anywhere.
#> make manifest                        # after editing scripts/manifest_in.py
## manifest: regenerate MANIFEST.in from scripts/manifest_in.py
.PHONY: manifest
manifest:
	$(PY) scripts/manifest_in.py --write

## manifest-check: fail if MANIFEST.in and its generator disagree
.PHONY: manifest-check
manifest-check:
	$(PY) scripts/manifest_in.py --check

# The wheel is inspected constantly; the sdist is the artifact nobody looks at
# and distro packagers build from. setuptools sweeps *.py from the project root
# by default and data files not at all, so for two releases the tarball carried
# a test suite and none of the fixtures it reads -- failing during a packager's
# build, where it reads as a broken release rather than a packaging bug.
#
# Reading MANIFEST.in cannot catch that. What the tarball holds is that file's
# patterns layered over a default set nobody wrote down, evaluated by the build
# backend against the working tree. The only honest way to know is to build one
# and look, which is what this does: every promised file must be in the archive,
# and the suite must pass from inside the unpacked tree.
#> make sdist-check                     # prove the tarball passes its own suite
## sdist-check: unpack the built sdist and run its test suite inside it
.PHONY: sdist-check
sdist-check: manifest-check dist
	$(PY) scripts/check_sdist.py

# --- the single-file binary -------------------------------------------------
#
# Nuitka compiles the package to C and links it, with CPython and the selected
# third-party packages, into one executable. It is entirely opt-in: this is the
# only target that installs it, and it does so on first use.
#
# The AGPL is worth knowing about before shipping the output. Nuitka itself is
# AGPLv3, but it carries a runtime-library exception (LICENSE-RUNTIME.txt) in
# the manner of GCC's: the permission is explicitly to "propagate a work of
# Target Code ... under terms of your choice". The compiler's copyleft does not
# reach the compiled program, so a binary built from this Apache-2.0 tree can
# stay Apache-2.0. Modifying and redistributing Nuitka is the part that does
# not.

BUILD_STAMP := $(BUILD_ENV)/.stamp-$(NUITKA_VERSION)-$(if $(EXTRAS),$(EXTRAS),core)

# Nuitka[onefile] rather than plain Nuitka: the extra pulls in zstandard, and
# without it Nuitka emits a *warning* and silently ships the payload
# uncompressed — a ~38 MB binary where ~13 MB was available.
#
# The environment is destroyed and rebuilt rather than updated in place, because
# installing is not the inverse of installing. Going from EXTRAS=all to EXTRAS=
# installs '.' into an environment that still has PyYAML and msgpack sitting in
# it, nuitka_includes.py still finds them, and the "core only" binary quietly
# ships every optional library. This only fires when a pin or EXTRAS actually
# changed -- that is what the stamp name encodes -- so the cost is a reinstall
# exactly when one is warranted.
$(BUILD_STAMP):
	@rm -rf $(BUILD_ENV)
	$(call mkvenv,$(BUILD_ENV))
	$(call venv_install,$(BUILD_ENV),'nuitka[onefile]==$(NUITKA_VERSION)' 'patchelf==$(PATCHELF_VERSION); sys_platform == "linux"')
	$(call venv_install,$(BUILD_ENV),$(if $(EXTRAS),'.[$(EXTRAS)]','.'))
	@rm -f $(BUILD_ENV)/.stamp-*
	@touch $@

# Two flags here are load-bearing rather than cosmetic:
#
# --python-flag=-m compiles leafdump as a package, entered at its __main__.
# Handing Nuitka the path leafdump/__main__.py instead compiles that file as a
# top-level script, which promotes its siblings to top-level modules — and this
# package contains codecs.py. The stdlib `codecs` gets shadowed by ours, the
# interpreter cannot import `encodings` during its own startup, and the binary
# dies before reaching any leafdump code with "No module named 'codecs'". The
# compile itself succeeds and says nothing.
#
# --deployment turns off the compatibility diagnostics Nuitka builds in to help
# during development; they are dead weight in a shipped artifact.
#
# Not used here, after measuring: --onefile-tempdir-spec. It pins the unpack
# directory so the payload survives between runs, which sounds like the obvious
# win for a CLI. On this tree it saves 12 ms out of a 150 ms startup (8%) and
# leaves 51 MB sitting in ~/.cache for every version ever run. The startup cost
# of onefile is the bootstrap process itself, not the unpacking, so caching the
# unpack barely moves it. The default -- extract, run, clean up -- is the better
# trade.
#
# Worth knowing before choosing this format at all: onefile is a *deployment*
# win, not a speed one. Measured on this tree, one `--version` invocation:
#
#     onefile binary        150 ms
#     --standalone dist/     42 ms
#     python -m leafdump    46 ms
#
# The single file costs ~110 ms per invocation against just running the source,
# because the bootstrap unpacks and then execs a second process. That is the
# price of "one file, no Python needed anywhere". For a filter in a tight shell
# loop, `--standalone` (a directory, not a file) is the faster shape.
NUITKA_FLAGS = \
	--onefile \
	--python-flag=-m \
	--python-flag=no_site \
	--output-dir=$(BUILD)/nuitka \
	--output-filename=leafdump \
	--deployment \
	--assume-yes-for-downloads \
	--company-name='Jeremy Melanson' \
	--product-name=leafdump \
	--product-version=$(VERSION) \
	--file-description='Make nested data greppable, one value per line'

#> make binary                      # single file with every stable format in it
#> make binary EXTRAS=              # ...core formats only, no third-party code
#> make binary EXTRAS=all,extras    # ...including bson, avro and protobuf
## binary: compile a single-file executable into bin/ (installs Nuitka on first use)
.PHONY: binary
binary: $(BUILD_STAMP)
	@mkdir -p $(BIN)
	@rm -rf $(BUILD)/nuitka
	PATH="$(BUILD_ENV)/bin:$$PATH" $(BUILD_ENV)/bin/python -m nuitka \
	  $(NUITKA_FLAGS) \
	  $$($(BUILD_ENV)/bin/python scripts/nuitka_includes.py) \
	  leafdump
	@mv $(BUILD)/nuitka/leafdump $(BIN)/leafdump
	@printf '\n\033[1m%s\033[0m  (%s)\n' '$(BIN)/leafdump' "$$(du -h $(BIN)/leafdump | cut -f1)"
	@$(BIN)/leafdump --version

## binary-report: show which formats the current build environment would compile in
.PHONY: binary-report
binary-report: $(BUILD_STAMP)
	@$(BUILD_ENV)/bin/python scripts/nuitka_includes.py --report

# Compiling in a format and then finding it unavailable at runtime is the exact
# failure the include flags exist to prevent, so the check is that the binary
# agrees with the build environment about what it can do.
# Deliberately run with the *build* interpreter rather than $(PY): the check is
# "does the binary do what the environment that produced it could do", and the
# ambient interpreter has its own, unrelated set of packages installed.
## binary-check: assert the built binary offers the formats it was built with
.PHONY: binary-check
binary-check: $(BUILD_STAMP)
	@test -x $(BIN)/leafdump || { echo "no $(BIN)/leafdump — run: make binary"; exit 1; }
	$(BUILD_ENV)/bin/python scripts/check_binary.py $(BIN)/leafdump

# ------------------------------------------------------------------- install

## install: the compiled binary, its manpage and the shell completions
.PHONY: install
install:
	@test -x $(BIN)/leafdump || { echo "no $(BIN)/leafdump — run: make binary"; exit 1; }
	install -d $(DESTDIR)$(PREFIX)/bin $(DESTDIR)$(MANDIR)/man1
	install -m0755 $(BIN)/leafdump $(DESTDIR)$(PREFIX)/bin/leafdump
	install -m0644 man/leafdump.1 $(DESTDIR)$(MANDIR)/man1/leafdump.1
	install -d $(DESTDIR)$(BASHCOMPDIR) $(DESTDIR)$(FISHCOMPDIR) $(DESTDIR)$(ZSHCOMPDIR)
	install -m0644 contrib/completions/leafdump.bash $(DESTDIR)$(BASHCOMPDIR)/leafdump
	install -m0644 contrib/completions/leafdump.fish $(DESTDIR)$(FISHCOMPDIR)/leafdump.fish
	install -m0644 contrib/completions/_leafdump    $(DESTDIR)$(ZSHCOMPDIR)/_leafdump

## uninstall: remove what install placed
.PHONY: uninstall
uninstall:
	rm -f $(DESTDIR)$(PREFIX)/bin/leafdump $(DESTDIR)$(MANDIR)/man1/leafdump.1
	rm -f $(DESTDIR)$(BASHCOMPDIR)/leafdump $(DESTDIR)$(FISHCOMPDIR)/leafdump.fish
	rm -f $(DESTDIR)$(ZSHCOMPDIR)/_leafdump

# ----------------------------------------------------------------- aggregates

# Every gate in both lists is enforced. fmt-check used to be the one exception;
# the tree was formatted in the initial commit and the exception closed with it,
# which ROADMAP.md §6 records in full.
#
# test-isolated is in `check` but not in `precommit`, and that split is the
# point of having two lists: it builds a virtualenv and installs the package
# into it, which is seconds rather than the milliseconds every other fast-gate
# entry costs. A pre-commit hook that is slow enough to resent is a hook that
# gets bypassed with --no-verify, and then none of these run.
#> make precommit                   # the fast gate, before committing
#> make check                       # every gate CI enforces
## check: everything CI enforces, in CI's order
.PHONY: check
check: fmt-check lint typecheck core-check completions-check docs-check version-check manifest-check test test-isolated sdist-check vuln audit

## precommit: the fast gate the pre-commit hook runs
.PHONY: precommit
precommit: fmt-check lint core-check docs-check version-check manifest-check test

# --------------------------------------------------------------------- tools

## tools: install the pinned linters into .venv-tools (never Nuitka)
.PHONY: tools
tools: $(NEED_RUFF) $(NEED_MYPY) $(NEED_PIP_AUDIT) $(NEED_ZIZMOR) $(NEED_LEFTHOOK)

## hooks: install the git hooks (once per clone, and after a version bump)
.PHONY: hooks
hooks: $(NEED_LEFTHOOK)
	@printf 'export LEFTHOOK_BIN=%s\n' '$(LEFTHOOK)' > .lefthook-rc.sh
	$(LEFTHOOK) install

## clean: remove build output and the compiled binary
.PHONY: clean
clean:
	rm -rf $(BUILD)/nuitka $(BIN) $(CURDIR)/dist $(CURDIR)/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .mypy_cache .ruff_cache

## distclean: clean, plus every virtualenv this Makefile created
.PHONY: distclean
distclean: clean
	rm -rf $(TOOLS_ENV) $(BUILD_ENV) $(CURDIR)/.venv-test $(CURDIR)/build
