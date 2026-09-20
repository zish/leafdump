# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

"""Merging and deduplication of multiple input documents.

Two independent knobs:

*mapping* strategy -- what happens when two documents both define a key
*list* strategy    -- what happens when two documents both define an array

plus an orthogonal ``--dedup`` pass that collapses structurally identical
elements inside every array.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

MAP_STRATEGIES = ("deep", "shallow", "last", "first", "collect")
LIST_STRATEGIES = ("concat", "union", "replace", "keep", "index")


class MergeError(Exception):
    pass


# --------------------------------------------------------------------------
# Structural identity
# --------------------------------------------------------------------------


def freeze(o: Any) -> Any:
    """A hashable, order-insensitive fingerprint of an arbitrary structure.

    The type name is folded into scalar keys deliberately: in Python
    ``1 == 1.0 == True`` and all three hash alike, so a naive set() would
    silently collapse ``true`` and ``1`` into one array element.  Data
    interchange formats treat those as distinct, so we must too.
    """
    if isinstance(o, dict):
        # Mappings compare equal regardless of key order, so sort by the
        # frozen key's repr -- stable and never raises on mixed key types.
        items = sorted(
            ((freeze(k), freeze(v)) for k, v in o.items()),
            key=lambda kv: repr(kv[0]),
        )
        return ("map", tuple(items))
    if isinstance(o, (list, tuple)):
        return ("seq", tuple(freeze(v) for v in o))
    if isinstance(o, (set, frozenset)):
        return ("set", frozenset(freeze(v) for v in o))
    if isinstance(o, (bytes, bytearray)):
        return ("bin", bytes(o))
    if o is None:
        return ("null",)
    try:
        hash(o)
    except TypeError:
        return ("opaque", type(o).__name__, repr(o))
    return ("scalar", type(o).__name__, o)


# --------------------------------------------------------------------------
# Deduplication
# --------------------------------------------------------------------------


def dedup(obj: Any) -> Any:
    """Remove structurally duplicate elements from every array, first wins."""
    if isinstance(obj, dict):
        return {k: dedup(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        seen: set = set()
        out = []
        for item in obj:
            item = dedup(item)
            key = freeze(item)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out
    return obj


# --------------------------------------------------------------------------
# Merging
# --------------------------------------------------------------------------


def merge_two(
    a: Any, b: Any, *, map_strategy: str = "deep", list_strategy: str = "concat"
) -> Any:
    """Merge *b* into *a*, returning a new structure. *b* is the later value."""
    if map_strategy == "last":
        return b
    if map_strategy == "first":
        return a

    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            if k not in out or map_strategy == "shallow":
                out[k] = v
            elif map_strategy == "collect":
                out[k] = _collect(out[k], v)
            else:  # deep
                out[k] = merge_two(
                    out[k], v, map_strategy=map_strategy, list_strategy=list_strategy
                )
        return out

    if isinstance(a, list) and isinstance(b, list):
        return _merge_lists(a, b, map_strategy, list_strategy)

    # Type mismatch or plain scalars: later value wins, except under
    # `collect`, which keeps both rather than silently discarding one.
    if map_strategy == "collect":
        return _collect(a, b)
    return b


def _merge_lists(a: list, b: list, map_strategy: str, list_strategy: str) -> list:
    if list_strategy == "replace":
        return list(b)
    if list_strategy == "keep":
        return list(a)
    if list_strategy == "concat":
        return list(a) + list(b)
    if list_strategy == "union":
        return dedup(list(a) + list(b))
    if list_strategy == "index":
        # Element-wise merge -- treat arrays as fixed-shape tuples.
        out = []
        for i in range(max(len(a), len(b))):
            if i >= len(a):
                out.append(b[i])
            elif i >= len(b):
                out.append(a[i])
            else:
                out.append(
                    merge_two(
                        a[i],
                        b[i],
                        map_strategy=map_strategy,
                        list_strategy=list_strategy,
                    )
                )
        return out
    raise MergeError(f"unknown list strategy {list_strategy!r}")


def _collect(a: Any, b: Any) -> list:
    """Accumulate conflicting values into a deduplicated list."""
    bucket = list(a) if isinstance(a, list) else [a]
    additions = list(b) if isinstance(b, list) else [b]
    return dedup(bucket + additions)


def merge_all(
    docs: Iterable[Any],
    *,
    map_strategy: str = "deep",
    list_strategy: str = "concat",
    deduplicate: bool = False,
) -> Any:
    """Fold every document into one, left to right."""
    docs = list(docs)
    if not docs:
        return None
    result = docs[0]
    for doc in docs[1:]:
        result = merge_two(
            result, doc, map_strategy=map_strategy, list_strategy=list_strategy
        )
    if deduplicate:
        result = dedup(result)
    return result


def wrap(docs: list[Any], keys: list[str]) -> dict:
    """Key each document by its source instead of merging them together."""
    out: dict[str, Any] = {}
    for key, doc in zip(keys, docs, strict=True):
        if key in out:
            n = 2
            while f"{key}#{n}" in out:
                n += 1
            key = f"{key}#{n}"
        out[key] = doc
    return out
