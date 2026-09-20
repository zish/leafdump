# Roadmap

Nothing in this document is implemented. It records intent, and the reasoning
behind the design choices that are still open.

---

## 1. Filtering and transformation

**Goal.** Select and reshape nested data with SQL-style clauses, with
pseudo-python expressions usable wherever an expression is expected.

```
leafdump big.json --select 'ROOT["hosts"][*]["ifaces"][*]' \
                   --where  'ROOT["state"] == "up" and len(ROOT["addrs"]) > 1' \
                   --project 'name = ROOT["name"], n = len(ROOT["addrs"])'
```

The two syntaxes fit together because the path notation this tool already
emits *is* a Python subscript expression. A `--where` clause is a Python
expression over the current node; a `SELECT` path is the same notation with
`[*]` wildcards. Nothing new has to be invented for the expression language —
it is the output format read backwards.

**Design constraints.**

- Evaluate expressions in a restricted environment. `eval` on user input is
  a footgun; compile to an AST and walk it with an allowlist of node types
  and builtins (`len`, `str`, `int`, `any`, `all`, comparisons, arithmetic,
  membership), rejecting attribute access, imports and calls to anything
  else. Reuse the same validator for `--project`.
- Wildcards must stream. `[*]` over a 40 GB array cannot materialise.
  Compile the selector to a state machine driven by the existing
  `walk_iter` path stream rather than by recursion over loaded objects.
- Decide early whether `SELECT` returns paths or values, because that choice
  determines whether the result is still a tree or already a relation. Both
  are useful; likely `--select` yields a tree and `--select-rows` yields a
  relation.

---

## 2. Interactive shell

A REPL over a loaded (or memory-mapped) structure:

```
leafdump --shell capture.jsonl
> .schema hosts                    # inferred shape and cardinality
> hosts[*].ifaces[*] | where state == "up" | count
1284
> .save selection.json
```

Wants: readline history, completion of paths from the actual data (the
storage backend in §3 makes this cheap — a prefix scan *is* a completion
query), `.schema` shape inference, paging, and the ability to bind an
intermediate result to a name for further querying.

This only becomes worth building on top of §3; a REPL that re-parses a large
file per command is not useful.

---

## 3. Out-of-core storage — structures larger than RAM

This is the item most likely to become the primary feature, and the one whose
design matters most.

### Assessment of LMDB + FlexBuffers

The pairing is a reasonable instinct, and each half is good at what it does:

- **LMDB** is a memory-mapped copy-on-write B+tree. Reads are zero-copy
  pointers into the map, so a working set larger than RAM degrades to page
  faults rather than to deserialisation. Keys are byte-ordered, so a prefix
  scan over a subtree is native and free. Single-writer/multi-reader MVCC
  means a long-running query never blocks ingest.
- **FlexBuffers** is schemaless and *randomly accessible without parsing*:
  reaching `root["a"][3]["b"]` seeks rather than deserialises. That is
  exactly the right property for large values.

**But the naive combination — one FlexBuffer per document — does not work,**
for one decisive reason: **FlexBuffers are immutable and built bottom-up.**
There is no in-place edit. Any transformation of a 10 GB document means
rebuilding a 10 GB buffer, which defeats the purpose. FlexBuffers also have
no streaming builder that avoids holding the built buffer in memory.

### The design that does work: shred the tree

Store the structure *decomposed*, one row per leaf, keyed by path:

```
key                                     value
ROOT / {hosts} / #0000000000 / {name}   str  "web-01"
ROOT / {hosts} / #0000000000 / {port}   i64  443
ROOT / {hosts} / #0000000001 / {name}   str  "web-02"
```

This is not a new model — **it is precisely what the existing flatten pass
already produces.** The `perl` and `python` output formats are serialisations
of it. Adding a storage backend is therefore incremental work, not a rewrite,
and the whole tool converges on a single internal representation.

What it buys:

| Operation | How |
| --- | --- |
| Read a subtree | Prefix range scan (LMDB/SQLite/RocksDB all do this natively) |
| Mutate one leaf | Rewrite one key — no whole-document rebuild |
| Path completion in the shell | Prefix scan, bounded by the next separator |
| Merge N documents | Ordered merge-join of N sorted key streams — no document ever fully resident |
| Deduplicate | Compare subtree hashes stored alongside each internal node |
| Count/aggregate | Scan without materialising |

FlexBuffers still earn a place, just not at the top: below a size threshold
(say 4 KiB), collapse a whole subtree into a single FlexBuffer value instead
of shredding it. Small objects stop paying per-leaf key overhead while large
ones stay decomposed and mutable. The threshold is a tunable, not a
guess-once decision.

**Key encoding is the part that is easy to get wrong.** Two traps:

1. A `.`-joined path is ambiguous the moment a mapping key contains a dot.
2. Lexicographic ordering puts array index `10` before `2`, so a prefix scan
   returns array elements out of order.

Fix both with a typed, length-prefixed component encoding: a one-byte tag
(`0x01` map key, `0x02` array index), then either the UTF-8 key with its
length varint-prefixed, or a fixed-width big-endian index. Big-endian is what
makes numeric order equal byte order.

### Two scaling regimes

"Larger than RAM" is two different problems, and they need different answers:

**Regime A — many records, huge total.** 500M JSON lines, 400 GB. Every
mainstream engine handles this, and DuckDB handles it very well.

**Regime B — one record, huge.** A single 40 GB JSON object with deep
nesting. Almost nothing handles this, DuckDB included.

DuckDB's position is worth stating precisely, because it is asymmetric:

*Where it scales.* Execution is push-based and vectorised with morsel-driven
parallelism. Streaming operators — scan, filter, project, `COPY ... TO` —
run in bounded memory regardless of input size, so a
`read_json -> transform -> COPY TO` pipeline over 400 GB holds only vectors in
flight. The pipeline-breakers (sort, hash-join build side, hash aggregate,
window) spill to disk: external merge sort, partitioned external hash join,
external hash aggregate. `memory_limit` (default ~80% of RAM) and
`temp_directory` / `max_temp_directory_size` bound it.

*Where it hard-stops.* The JSON reader is **record-oriented**: it scales to a
huge *number* of records, not to one huge record. `read_json` caps individual
records at `maximum_object_size` (16 MB by default — confirm against the
target version), and more fundamentally an individual value must fit in
memory to exist at all, because DuckDB has no partial-value representation.
Raising the cap just means buffering the whole record.

*The resolution.* **Shredding converts Regime B into Regime A.** Flattening
one 40 GB document into path/leaf rows replaces a single oversized record
with a billion tiny ones — exactly the shape DuckDB is best at. The flatten
pass is the adapter between the two regimes, which is what makes the
architecture collapse to:

```
streaming parser  ->  shredder   ->  DuckDB  ->  SQL / Ibis / output formats
    (ijson)          (walk_iter)      (§3)          (§1, §4)
```

So DuckDB *plus a streaming shredder* is sufficient; DuckDB alone is not.
This demotes LMDB and RocksDB from "backends to build" to "optimisations if
DuckDB's point-lookup latency disappoints" — do not build them until
something measured says to.

*Operational gotchas, for when it does get used at scale:*

- Memory scales with `threads`; each thread carries its own operator state.
  Lowering `threads` is the first move on OOM and costs less throughput than
  expected, since this workload is I/O-bound.
- ART index creation is memory-hungry and does not spill well. Keep indexes
  off the ingest path.
- Window functions spill later and worse than sort/join/aggregate. Treat
  them as the risky operator.
- High-cardinality `GROUP BY` on long strings is the classic blow-up, and
  shredded paths are *precisely* long high-cardinality strings. Dictionary-
  encode paths to integer IDs rather than grouping on the text. This is a
  design requirement of the shredded layout, not a tuning afterthought.

### Backends worth supporting

Storage should be an interface with several implementations, because the
right answer depends on the workload:

- **SQLite** (default). Ships with Python, no install step, no map-size
  ceiling to pre-declare, `WITHOUT ROWID` tables give the same
  sorted-key/prefix-scan behaviour, and `JSONB` (3.45+) covers the
  collapsed-subtree case. Slower than the alternatives, available everywhere.
- **LMDB + FlexBuffers**. Lowest read latency and true zero-copy. Best when
  the access pattern is random point lookups into a mostly-static structure.
  Costs: `map_size` must be declared and grown by hand, and random writes to
  a copy-on-write B+tree amplify badly during bulk ingest.
- **RocksDB**. An LSM-tree, so bulk ingest is far faster than LMDB, and
  prefix compression on sorted keys is a large win here specifically —
  shredded paths are enormously repetitive, and neighbouring keys share long
  prefixes. Costs: compaction CPU, no zero-copy reads, weaker Python bindings.
- **DuckDB**. The primary target (see *Two scaling regimes* above). It
  collapses three roadmap items into one: streaming execution in bounded
  memory with spill-to-disk for the blocking operators, SQL — which *is*
  §1 — and Ibis's strongest backend, which is §4. Paired with the shredder
  it covers both scaling regimes. Its weakness is heterogeneous nesting,
  since it wants a consistent struct schema per column; the shredded
  three-column layout (`path`, `type`, `value`) sidesteps that entirely and
  keeps the SQL.

### The prerequisite nobody skips

None of this helps while input is read with `json.loads`, which materialises
the entire document before the first leaf is seen. This is the *only*
genuinely missing piece of the pipeline above — `walk_iter` is already a
non-recursive generator emitting exactly the right rows. Streaming parsers
are step zero:

- JSON — `ijson` (yajl-backed) emits SAX-style events; feeds `walk_iter`
  directly.
- MessagePack, CBOR, BSON, Avro — already record-streamable, and the codecs
  in this tool already use their incremental readers.
- YAML — `yaml.parse()` gives an event stream, though PyYAML is slow enough
  that libyaml bindings matter at scale.

Order of work: streaming input → shredded model → DuckDB backend → SQLite
for the zero-dependency case → nothing else until measurement demands it.

---

## 4. Ibis integration

**Yes, Ibis does input as well as output.** It is a dataframe/expression API
that compiles to roughly twenty execution backends (DuckDB, PostgreSQL,
ClickHouse, Polars, PySpark, BigQuery, Snowflake…). It reads via
`ibis.read_json`, `read_parquet`, `read_csv`, and `.table()` on any connected
backend; it writes via `to_parquet`, `to_csv`, `create_table` and friends.
Nested types (`struct`, `array`, `map`) are first-class in the type system,
though how well each is supported varies by backend.

The natural fit: `leafdump --to ibis` materialises into a backend table and
hands back an Ibis expression, so the same data can then be queried as SQL or
as a dataframe. This is also the least-effort route to §1 — rather than
inventing a query planner, compile the SQL-style clauses to Ibis expressions
and let it emit backend SQL.

---

## 5. Publishing to document stores

Direct publication (`--to mongodb --uri ... --collection ...`), plus the
question of what else belongs alongside it.

A note on the framing first: **MongoDB is under the SSPL**, which the OSI does
not recognise as an open-source licence. If "open source" is a real
requirement and not shorthand for "self-hostable", that rules out MongoDB
itself and Elasticsearch, and narrows the field considerably.

Genuinely OSI-licensed stores that handle arbitrary nested data:

- **PostgreSQL + `JSONB`** — the strongest all-round answer. GIN indexes over
  whole documents, standard SQL/JSON `jsonpath`, partial and expression
  indexes, real transactions, and a permissive licence. Caveats: `JSONB`
  normalises objects (key order is not preserved, duplicate keys collapse),
  and very large individual values hit TOAST limits. Should be the default
  publication target.
- **CouchDB** (Apache 2.0) — HTTP/JSON to the core, MVCC, and the best
  replication story of anything here, including offline/edge. Query is the
  weak point (Mango selectors and map/reduce views, not general SQL).
- **ClickHouse** (Apache 2.0) — its newer dynamic `JSON` type extracts
  subcolumns on the fly and it is extraordinarily fast over semi-structured
  data at volume. The right choice specifically for the "very large inputs"
  case that motivates §3.
- **OpenSearch** (Apache 2.0) — the actually-open fork of Elasticsearch.
  Excellent full-text and nested-document search. Watch out for dynamic
  mapping explosion: heterogeneous key spaces blow through field-count
  limits, so publication must be able to emit an explicit mapping.
- **DuckDB** (MIT) — embedded rather than a server, but for local analysis of
  a large nested corpus it beats the client/server options and needs no
  infrastructure.

Worth knowing but licence-encumbered: ArangoDB (multi-model, BUSL),
SurrealDB (BUSL), RavenDB (AGPL). Wrong shape for arbitrary nesting:
Cassandra/ScyllaDB, Redis, HDF5, Zarr.

If only one is built first, PostgreSQL/`JSONB` covers the widest range of
uses; ClickHouse is the one to add second if scale is the driver.

---
## 6. Formatting the existing tree with `ruff format`

**Status.** Taken, in the initial commit. `make fmt` rewrote 9 files and about
900 lines. None of it was a defect: it was almost entirely the difference
between this codebase's hand-aligned continuations —

    self.assertEqual(sorted(perl.stdout.splitlines()),
                     sorted(ours.splitlines()))

— and the single line `ruff format` prefers.

**Why now, having deferred it.** The argument for keeping the alignment was
never that it was wrong; it was legible, consistent, and clearly chosen rather
than fallen into. The argument against reformatting was the price: a diff
across nearly every file that makes `git blame` useless on all of them.
Applying it in the *initial* commit is what removed that price — there was no
history to lose, and the cost will never be lower than it was here.

The example above is an indented block rather than a fenced ```python one on
purpose. `ruff format` reaches Python inside Markdown fences, and on its first
run it flattened this very illustration, leaving a before/after pair whose two
halves were identical.

**Enforcement.** Closed immediately after, as a separate decision. Applying a
formatter and enforcing one are not the same choice, and a tree that is
formatted but unenforced drifts back a file at a time -- each of those files
eventually being its own small version of the diff this section spent so long
avoiding. All three edits named in the original ratchet are now made:

- `.github/workflows/ci.yml` runs `make fmt-check` as an ordinary step, with no
  `continue-on-error`.
- The Makefile's `check` and `precommit` lists both begin with `fmt-check`.
- `lefthook.yml` already blocked on it. Being the lone outlier is what forced
  the decision; it is now simply consistent with everything else.

**If the answer to enforcement ever changes.** `ruff format` is not mandatory.
Backing it out means deleting the `fmt`/`fmt-check` targets, the CI step, the
lefthook job and the two list entries, and keeping `ruff check` -- which is
where the actual findings come from -- on its own. The formatter and the linter
are independent tools that happen to ship in one binary.
