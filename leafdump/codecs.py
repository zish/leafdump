# Copyright 2026 Jeremy Melanson
# SPDX-License-Identifier: Apache-2.0

"""Concrete read/write implementations for every format in the registry.

Each codec is a small class with ``load_all(raw, ctx) -> list[Any]`` and
``dump(obj, ctx) -> bytes``.  Third-party modules are imported lazily inside
``_require``: nothing here is imported at CLI startup, so an installation with
zero optional dependencies still runs at full speed.
"""

from __future__ import annotations

import ast
import io
import json
from dataclasses import dataclass, field
from typing import Any

from .registry import Format, lookup
from .templates import TEMPLATES as _TEMPLATES


class CodecError(Exception):
    """A format could be selected but the data could not be handled."""


class FormatUnavailable(CodecError):
    def __init__(self, fmt: Format, direction: str):
        verb = "read" if direction == "in" else "write"
        super().__init__(
            f"cannot {verb} {fmt.name}: missing dependency.\n"
            f"  {fmt.install_hint(direction)}"
        )
        self.format = fmt


# --------------------------------------------------------------------------
# Shared context
# --------------------------------------------------------------------------


@dataclass
class Context:
    """Everything a codec might need to know, in one bag."""

    encoding: str = "utf-8"
    indent: int | None = 2
    sort_keys: bool = False
    multipacket: bool = False
    ensure_ascii: bool = False
    null_policy: str = "keep"  # keep | drop | empty  (for null-hostile formats)
    avro_schema: Any = None
    avro_record_name: str = "Root"
    extra: dict[str, Any] = field(default_factory=dict)


def _require(module: str, fmt: Format, direction: str):
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError:
        raise FormatUnavailable(fmt, direction) from None


def _first_available(fmt: Format, direction: str, *modules: str):
    """Import the first of *modules* that is installed."""
    import importlib

    for m in modules:
        try:
            return m, importlib.import_module(m)
        except ImportError:
            continue
    raise FormatUnavailable(fmt, direction)


# --------------------------------------------------------------------------
# Base
# --------------------------------------------------------------------------


class Codec:
    name = ""

    @property
    def fmt(self) -> Format:
        return lookup(self.name)

    # -- reading -----------------------------------------------------------
    def load_all(self, raw: bytes, ctx: Context) -> list[Any]:
        raise CodecError(f"{self.name} cannot be used as an input format")

    # -- writing -----------------------------------------------------------
    def dump(self, obj: Any, ctx: Context) -> bytes:
        raise CodecError(f"{self.name} cannot be used as an output format")

    # -- helpers -----------------------------------------------------------
    def _text(self, raw: bytes, ctx: Context) -> str:
        return raw.decode(ctx.encoding)

    def _split_lines(self, raw: bytes, ctx: Context) -> list[str]:
        return [ln for ln in self._text(raw, ctx).splitlines() if ln.strip()]


# --------------------------------------------------------------------------
# JSON family (stdlib)
# --------------------------------------------------------------------------


class JsonCodec(Codec):
    name = "json"

    def load_all(self, raw, ctx):
        if ctx.multipacket:
            return [json.loads(ln) for ln in self._split_lines(raw, ctx)]
        text = self._text(raw, ctx)
        try:
            return [json.loads(text)]
        except json.JSONDecodeError:
            # Concatenated documents ("{...}{...}") are common in packet
            # captures; fall back to incremental decoding before giving up.
            docs = list(_iter_concatenated_json(text))
            if docs:
                return docs
            raise

    def dump(self, obj, ctx):
        text = json.dumps(
            obj,
            indent=ctx.indent,
            sort_keys=ctx.sort_keys,
            ensure_ascii=ctx.ensure_ascii,
            default=_json_default,
        )
        return (text + "\n").encode(ctx.encoding)


def _iter_concatenated_json(text: str):
    dec = json.JSONDecoder()
    idx, n = 0, len(text)
    while idx < n:
        while idx < n and text[idx].isspace():
            idx += 1
        if idx >= n:
            return
        obj, idx = dec.raw_decode(text, idx)
        yield obj


def _json_default(o: Any):
    if isinstance(o, (bytes, bytearray)):
        return o.decode("utf-8", "backslashreplace")
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


class JsonlCodec(JsonCodec):
    name = "jsonl"

    def load_all(self, raw, ctx):
        return [json.loads(ln) for ln in self._split_lines(raw, ctx)]

    def dump(self, obj, ctx):
        # A top-level list becomes one line per element -- that is the whole
        # point of JSONL; anything else is a single line.
        docs = obj if isinstance(obj, list) else [obj]
        out = "".join(
            json.dumps(
                d,
                sort_keys=ctx.sort_keys,
                ensure_ascii=ctx.ensure_ascii,
                default=_json_default,
            )
            + "\n"
            for d in docs
        )
        return out.encode(ctx.encoding)


class ReprCodec(Codec):
    name = "repr"

    def load_all(self, raw, ctx):
        text = self._text(raw, ctx)
        if ctx.multipacket:
            return [ast.literal_eval(ln) for ln in self._split_lines(raw, ctx)]
        return [ast.literal_eval(text)]

    def dump(self, obj, ctx):
        if ctx.indent:
            import pprint

            text = pprint.pformat(obj, indent=1, width=88, sort_dicts=ctx.sort_keys)
        else:
            text = repr(obj)
        return (text + "\n").encode(ctx.encoding)


# --------------------------------------------------------------------------
# YAML
# --------------------------------------------------------------------------


class YamlCodec(Codec):
    name = "yaml"

    def load_all(self, raw, ctx):
        yaml = _require("yaml", self.fmt, "in")
        # safe_load_all handles `---` separated multi-document streams, which
        # is YAML's native answer to --multipacket.
        return list(yaml.safe_load_all(self._text(raw, ctx)))

    def dump(self, obj, ctx):
        yaml = _require("yaml", self.fmt, "out")
        text = yaml.safe_dump(
            _plainify(obj),
            indent=ctx.indent or 2,
            sort_keys=ctx.sort_keys,
            allow_unicode=not ctx.ensure_ascii,
            default_flow_style=False,
        )
        return text.encode(ctx.encoding)


def _plainify(o: Any):
    """Coerce exotic scalars into types PyYAML/TOML/etc. can represent."""
    if isinstance(o, dict):
        return {k: _plainify(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plainify(v) for v in o]
    if isinstance(o, (set, frozenset)):
        return [_plainify(v) for v in o]
    if isinstance(o, (bytes, bytearray)):
        return o.decode("utf-8", "backslashreplace")
    return o


# --------------------------------------------------------------------------
# JSON5
# --------------------------------------------------------------------------


class Json5Codec(Codec):
    name = "json5"

    def load_all(self, raw, ctx):
        _mod_name, mod = _first_available(self.fmt, "in", "json5", "pyjson5")
        text = self._text(raw, ctx)
        loads = mod.loads if hasattr(mod, "loads") else mod.decode
        if ctx.multipacket:
            return [loads(ln) for ln in self._split_lines(raw, ctx)]
        return [loads(text)]

    def dump(self, obj, ctx):
        mod_name, mod = _first_available(self.fmt, "out", "json5", "pyjson5")
        if mod_name == "json5":
            text = mod.dumps(obj, indent=ctx.indent, sort_keys=ctx.sort_keys)
        else:  # pyjson5
            text = mod.dumps(obj)
        return (text + "\n").encode(ctx.encoding)


# --------------------------------------------------------------------------
# TOON
# --------------------------------------------------------------------------


class ToonCodec(Codec):
    name = "toon"

    def _api(self, direction: str):
        mod_name, mod = _first_available(self.fmt, direction, "toon_format", "toon")
        encode = getattr(mod, "encode", None) or getattr(mod, "dumps", None)
        decode = getattr(mod, "decode", None) or getattr(mod, "loads", None)
        if encode is None or decode is None:
            raise CodecError(
                f"the installed `{mod_name}` module does not expose "
                f"encode()/decode(); install `toon-format` for the expected API"
            )
        return encode, decode

    def load_all(self, raw, ctx):
        _, decode = self._api("in")
        return [decode(self._text(raw, ctx))]

    def dump(self, obj, ctx):
        encode, _ = self._api("out")
        text = encode(_plainify(obj))
        if not text.endswith("\n"):
            text += "\n"
        return text.encode(ctx.encoding)


# --------------------------------------------------------------------------
# TOML
# --------------------------------------------------------------------------


class TomlCodec(Codec):
    name = "toml"

    def load_all(self, raw, ctx):
        try:
            import tomllib as toml_read
        except ImportError:
            toml_read = _require("tomli", self.fmt, "in")
        return [toml_read.loads(self._text(raw, ctx))]

    def dump(self, obj, ctx):
        tomli_w = _require("tomli_w", self.fmt, "out")
        obj = _plainify(obj)
        if not isinstance(obj, dict):
            raise CodecError(
                "TOML documents must have a mapping at the root; got "
                f"{type(obj).__name__}. Wrap it with --wrap-key NAME."
            )
        obj = _strip_nulls(obj, ctx.null_policy)
        try:
            return tomli_w.dumps(obj).encode(ctx.encoding)
        except TypeError as exc:
            # tomli_w reports the type it could not write and stops there.
            # For null -- much the commonest case, since TOML 1.0 has no null
            # at all -- the useful half of the message is the flag that fixes
            # it, which is ours to add and not tomli_w's to know about.
            if "NoneType" in str(exc):
                raise CodecError(
                    "TOML has no null, so a document containing one cannot be "
                    "written. Use --null-policy drop to omit those keys, or "
                    '--null-policy empty to write them as "".'
                ) from None
            raise CodecError(str(exc)) from None


def _strip_nulls(o: Any, policy: str):
    """TOML and a few others cannot represent null."""
    if policy == "keep":
        return o
    replacement = "" if policy == "empty" else None
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if v is None:
                if policy == "drop":
                    continue
                out[k] = replacement
            else:
                out[k] = _strip_nulls(v, policy)
        return out
    if isinstance(o, list):
        items = []
        for v in o:
            if v is None:
                if policy == "drop":
                    continue
                items.append(replacement)
            else:
                items.append(_strip_nulls(v, policy))
        return items
    return o


# --------------------------------------------------------------------------
# MessagePack
# --------------------------------------------------------------------------


class MsgpackCodec(Codec):
    name = "msgpack"

    def load_all(self, raw, ctx):
        msgpack = _require("msgpack", self.fmt, "in")
        unpacker = msgpack.Unpacker(io.BytesIO(raw), raw=False, strict_map_key=False)
        return list(unpacker)

    def dump(self, obj, ctx):
        msgpack = _require("msgpack", self.fmt, "out")
        return msgpack.packb(obj, use_bin_type=True, default=_json_default)


# --------------------------------------------------------------------------
# CBOR
# --------------------------------------------------------------------------


class CborCodec(Codec):
    name = "cbor"

    def load_all(self, raw, ctx):
        cbor2 = _require("cbor2", self.fmt, "in")
        stream = io.BytesIO(raw)
        decoder = cbor2.CBORDecoder(stream)
        docs = []
        while stream.tell() < len(raw):
            docs.append(decoder.decode())
        return docs

    def dump(self, obj, ctx):
        cbor2 = _require("cbor2", self.fmt, "out")
        return cbor2.dumps(obj, default=_cbor_default)


def _cbor_default(encoder, value):
    encoder.encode(_json_default(value))


# --------------------------------------------------------------------------
# BSON
# --------------------------------------------------------------------------


class BsonCodec(Codec):
    name = "bson"

    def _mod(self, direction):
        bson = _require("bson", self.fmt, direction)
        if not hasattr(bson, "decode_all"):
            raise CodecError(
                "the installed `bson` module is the unrelated PyPI package of "
                "that name, not the one bundled with pymongo.\n"
                "  pip uninstall bson && pip install pymongo"
            )
        return bson

    def load_all(self, raw, ctx):
        bson = self._mod("in")
        return bson.decode_all(raw)

    def dump(self, obj, ctx):
        bson = self._mod("out")
        obj = _plainify(obj)
        if not isinstance(obj, dict):
            raise CodecError(
                "BSON documents must have a mapping at the root; got "
                f"{type(obj).__name__}. Wrap it with --wrap-key NAME."
            )
        return bson.encode({str(k): v for k, v in obj.items()})


# --------------------------------------------------------------------------
# Avro
# --------------------------------------------------------------------------


class AvroCodec(Codec):
    name = "avro"

    def load_all(self, raw, ctx):
        fastavro = _require("fastavro", self.fmt, "in")
        return list(fastavro.reader(io.BytesIO(raw)))

    def dump(self, obj, ctx):
        fastavro = _require("fastavro", self.fmt, "out")
        obj = _plainify(obj)
        records = obj if isinstance(obj, list) else [obj]
        if ctx.avro_schema is not None:
            schema = ctx.avro_schema
        else:
            schema = infer_avro_schema(records, ctx.avro_record_name)
        buf = io.BytesIO()
        fastavro.writer(buf, fastavro.parse_schema(schema), records)
        return buf.getvalue()


def infer_avro_schema(records: list[Any], name: str = "Root") -> dict:
    """Best-effort schema inference.

    Avro is schema-first, so a schemaless dump has to guess.  Every field
    becomes a union that includes "null", which is both the safest and the
    most permissive choice; heterogeneous arrays widen to a union of the
    member types.  Pass --avro-schema for anything load-bearing.
    """
    merged: dict[str, list[Any]] = {}
    for rec in records:
        if not isinstance(rec, dict):
            raise CodecError(
                "Avro records must be mappings; got "
                f"{type(rec).__name__}. Wrap it with --wrap-key NAME."
            )
        for k, v in rec.items():
            merged.setdefault(str(k), []).append(v)

    fields = []
    for k, values in merged.items():
        fields.append(
            {
                "name": _avro_name(k),
                "type": _avro_union(values, f"{name}_{_avro_name(k)}"),
                "default": None,
            }
        )
    return {"type": "record", "name": name, "fields": fields}


def _avro_name(k: str) -> str:
    safe = "".join(c if (c.isalnum() or c == "_") else "_" for c in str(k))
    return safe if safe[:1].isalpha() or safe[:1] == "_" else "_" + safe


def _avro_union(values: list[Any], name: str):
    kinds: list[Any] = ["null"]
    seen = {"null"}

    def add(t):
        key = json.dumps(t, sort_keys=True) if isinstance(t, dict) else t
        if key not in seen:
            seen.add(key)
            kinds.append(t)

    for v in values:
        if v is None:
            continue
        if isinstance(v, bool):
            add("boolean")
        elif isinstance(v, int):
            add("long")
        elif isinstance(v, float):
            add("double")
        elif isinstance(v, str):
            add("string")
        elif isinstance(v, (bytes, bytearray)):
            add("bytes")
        elif isinstance(v, list):
            add({"type": "array", "items": _avro_union(v, name + "_item")})
        elif isinstance(v, dict):
            # Maps are far safer than nested records: no name collisions and
            # no requirement that every occurrence share a field set.
            add({"type": "map", "values": _avro_union(list(v.values()), name + "_val")})
        else:
            add("string")
    return kinds


# --------------------------------------------------------------------------
# NestedText
# --------------------------------------------------------------------------


class NestedTextCodec(Codec):
    name = "nestedtext"

    def load_all(self, raw, ctx):
        nt = _require("nestedtext", self.fmt, "in")
        return [nt.loads(self._text(raw, ctx))]

    def dump(self, obj, ctx):
        nt = _require("nestedtext", self.fmt, "out")
        text = nt.dumps(_nt_stringify(_plainify(obj)))
        return (text + "\n").encode(ctx.encoding)


def _nt_stringify(o: Any):
    """NestedText leaves are always strings -- coerce before handing it over."""
    if isinstance(o, dict):
        return {str(k): _nt_stringify(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_nt_stringify(v) for v in o]
    if o is None:
        return ""
    if o is True:
        return "true"
    if o is False:
        return "false"
    return str(o)


# --------------------------------------------------------------------------
# Protobuf (google.protobuf.Value -- the schemaless well-known type)
# --------------------------------------------------------------------------


class ProtobufStructCodec(Codec):
    name = "protobuf-struct"

    def _api(self, direction):
        _require("google.protobuf", self.fmt, direction)
        from google.protobuf import json_format, struct_pb2

        return struct_pb2, json_format

    def load_all(self, raw, ctx):
        struct_pb2, json_format = self._api("in")
        value = struct_pb2.Value()
        value.ParseFromString(raw)
        return [json_format.MessageToDict(value)]

    def dump(self, obj, ctx):
        struct_pb2, json_format = self._api("out")
        value = struct_pb2.Value()
        json_format.ParseDict(_plainify(obj), value)
        return value.SerializeToString()


# --------------------------------------------------------------------------
# Render-only pseudo formats
# --------------------------------------------------------------------------


class RenderCodec(Codec):
    """Adapter for the template renderer in :mod:`leafdump.flatten`.

    One instance per pseudocode template, plus one for ``pseudocode`` itself,
    which has no template of its own and renders whatever ``--template``
    selected -- including a template read from a file, which no format name
    could name.
    """

    def __init__(self, name: str, template: Any = None):
        self.name = name
        self.template = template

    def dump(self, obj, ctx):
        from .flatten import RenderOptions, render_template
        from .templates import DEFAULT

        opts: RenderOptions = ctx.extra.get("render_options") or RenderOptions()
        opts.sort_keys = ctx.sort_keys
        opts.ascii_only = ctx.ensure_ascii
        # An explicit --template outranks the format name it was reached by.
        lines = render_template(obj, opts, opts.template or self.template or DEFAULT)
        return "".join(line + "\n" for line in lines).encode(
            ctx.encoding, "backslashreplace"
        )


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

_CODECS: dict[str, Codec] = {
    c.name: c
    for c in (
        JsonCodec(),
        JsonlCodec(),
        ReprCodec(),
        YamlCodec(),
        Json5Codec(),
        ToonCodec(),
        TomlCodec(),
        MsgpackCodec(),
        CborCodec(),
        BsonCodec(),
        AvroCodec(),
        NestedTextCodec(),
        ProtobufStructCodec(),
    )
}
# Every template in the catalogue, reachable as an output format of its own
# name; `pseudocode` is the one that defers to --template entirely.
for _t in _TEMPLATES:
    _CODECS[_t.name] = RenderCodec(_t.name, _t)
_CODECS["pseudocode"] = RenderCodec("pseudocode")


def codec_for(fmt: Format | str) -> Codec:
    f = fmt if isinstance(fmt, Format) else lookup(fmt)
    try:
        return _CODECS[f.name]
    except KeyError:
        raise CodecError(f"no codec implemented for format {f.name!r}") from None
