# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

"""leafdump -- make nested data greppable, one value per line.

Every line is one value and the full path to it, so a config file, an API
response or a log record becomes something grep, less, cut and awk already
know how to read -- including a minified one with no newlines in it at all.  The
default notation and ``--perl-compat`` come from the Perl script this package
grew out of; the rest of the catalogue (python, javascript, go, r, jq, and any
a user writes) lives in :mod:`leafdump.templates`, and a pluggable set of
optional serialisation formats turns the same walk into a converter.
"""

__version__ = "1.0.0"
__author__ = "Jeremy Melanson"

__all__ = ["__author__", "__version__"]
