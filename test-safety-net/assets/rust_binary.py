#!/usr/bin/env python3
"""rust_binary.py -- can a preloaded hook attribute this binary's I/O calls?

The Rust guard runs a compiled test binary with a hook library preloaded
(`LD_PRELOAD` on Linux, `DYLD_INSERT_LIBRARIES` on macOS). The hook attributes
each I/O call to a function by resolving stack frames to symbols. Three
binary shapes make that fail OPEN -- the hook is
silently absent or blind, and the guard would report a clean run that never
ran under it at all:

  * a STATIC binary cannot load a preload;
  * a MUSL binary uses a different dynamic loader than the hook is built for;
  * a STRIPPED binary carries no crate symbols to attribute a frame to.

`inspect(path)` reads the file's own format -- ELF64 or Mach-O 64, both
little-endian on every platform this guard targets -- with pure `struct`
reads and never raises: a file too short or malformed to be one of the two
is `fmt = "other"`, not an exception, because a corrupt or unexpected binary
must refuse cleanly rather than crash the guard around it. `refusal(facts)`
turns that into the reason the hook would fail open, or `None` when it would
not.

A crate symbol is read in EITHER Rust mangling, decided per symbol (ruling
R26): legacy `_ZN...17h<hash>E`, and v0 `_R...` -- rustc 1.98's default, in
which std and libtest ship precompiled, so a 1.98 test binary has no legacy
symbol at all. `v0_facts` / `v0_demangle` are the v0 reader, the Python twin
of the hook's own (`io_guard_rust_hook.rs`, `mod v0`).

This module is pure inspection: it does not run the binary, does not shell
out, and does not know about `LD_PRELOAD` or tiers. Task 7's wrapper is the
only caller: `refusal(inspect(exe))`.
"""
from __future__ import annotations

import collections
import string
import struct

BinaryFacts = collections.namedtuple("BinaryFacts", "fmt dynamic libc crate_symbols")
BinaryFacts.__doc__ = """What the guard needs to know about a compiled test binary.

    fmt            "elf64", "macho64", or "other" (not a 64-bit ELF/Mach-O)
    dynamic        True when the binary can load a preloaded hook at all
    libc           "glibc", "musl", "darwin", or None (unknown / not dynamic)
    crate_symbols  count of Rust-mangled symbols (legacy or v0) whose crate is
                   not std/core/alloc/test -- the frames a hook could attribute
"""

_NON_CRATE = frozenset({"std", "core", "alloc", "test"})

# ── The bounds-checked reader ────────────────────────────────────────────
#
# Every multi-byte read goes through `_unpack`, which raises `_Truncated`
# rather than letting `struct` raise on a short buffer or letting a bogus
# offset read garbage. `inspect` catches it once, at the top, so a truncated
# or adversarial file is `fmt = "other"` and nothing here ever propagates an
# exception to the caller.


class _Truncated(Exception):
    """A read ran past the end of the file, or an offset was implausible."""


def _unpack(fmt: str, data: bytes, offset: int):
    size = struct.calcsize(fmt)
    if offset < 0 or offset + size > len(data):
        raise _Truncated()
    return struct.unpack_from(fmt, data, offset)


def _cstr(buf: bytes, offset: int) -> str:
    """The NUL-terminated string in `buf` starting at `offset`, or "" out of range.

    Never raises: an offset outside `buf` (a corrupt `st_name`/`n_strx`) is
    simply not a symbol name -- it can only cost a crate-symbol count, never
    crash the read.
    """
    if offset < 0 or offset >= len(buf):
        return ""
    end = buf.find(b"\0", offset)
    if end < 0:
        end = len(buf)
    return buf[offset:end].decode("utf-8", "replace")


# ── Legacy Rust mangling: `_ZN<len><seg>...<len>h<16 hex>E` ─────────────

def _mangled_crate(sym: str):
    """The crate name of a legacy-mangled `_ZN...17h<16hex>E` symbol, or None.

    Reads the length-prefixed segment chain generically -- `<digits><that
    many bytes>` repeated -- rather than anchoring on a fixed segment count,
    so a nested module path or a generic impl's disambiguator segment reads
    the same way. The LAST segment must be the hash (`h` + 16 lowercase hex,
    17 bytes, so its own length prefix is always `17`); the FIRST segment is
    the crate. Anything that does not parse this way -- not `_ZN...E` at
    all, a length that overruns the string, a last segment that is not the
    hash shape -- is None: a symbol this reads as not-a-crate-function costs
    a count, never a crash.
    """
    if not sym.startswith("_ZN") or not sym.endswith("E"):
        return None
    body = sym[3:-1]
    n = len(body)
    segs, i = [], 0
    while i < n and body[i].isdigit():
        j = i
        while j < n and body[j].isdigit():
            j += 1
        length = int(body[i:j])
        start, end = j, j + length
        if length <= 0 or end > n:
            return None
        segs.append(body[start:end])
        i = end
    if i != n or not segs:
        return None
    if len(segs[-1]) != 17 or segs[-1][0] != "h" or not _is_hex16(segs[-1][1:]):
        return None
    return segs[0]


def _is_hex16(s: str) -> bool:
    return len(s) == 16 and all(c in "0123456789abcdef" for c in s)


def _macho_crate(sym: str):
    """Mach-O spells the same symbol with one extra leading underscore."""
    return _mangled_crate(sym[1:]) if sym.startswith("_") else None


# ── v0 Rust mangling: `_R <path> [<instantiating-crate>] [<suffix>]` ─────
#
# The grammar is the official one, "Symbol grammar summary" in
# https://doc.rust-lang.org/rustc/symbol-mangling/v0.html, plus what
# rustc-demangle's reference parser also accepts: a type's optional `w`
# prefix, the pattern type `W <type> <pattern>`, and the extended `const`
# forms (`e`/`Re` strings, `A`/`T`/`V` aggregates). Back-references
# `B <base-62-number>` are offsets "starting from just after the `_R`
# prefix" (spec, "Backref"), and are followed only to an EARLIER position.
#
# This is written independently of the hook's `mod v0` but to the same
# rules -- the same grammar, the same depth and step caps, counted at the
# same places -- and `test_io_guard_rust.TestClassifier` checks that the two
# agree on every v0 symbol of a real test binary.

V0_MAX_DEPTH = 64          # nesting past this is unreadable (the hook: transparent)
V0_MAX_STEPS = 10000       # nodes parsed, back-references re-read included
_U64 = 1 << 64
_V0_BASIC = {"a": "i8", "b": "bool", "c": "char", "d": "f64", "e": "str", "f": "f32",
             "h": "u8", "i": "isize", "j": "usize", "l": "i32", "m": "u32", "n": "i128",
             "o": "u128", "p": "_", "s": "i16", "t": "u16", "u": "()", "v": "...",
             "x": "i64", "y": "u64", "z": "!"}
_V0_DIGITS = "0123456789"
_V0_HEX = "0123456789abcdef"
_V0_DROP_GLUE = ("drop_glue", "drop_in_place")

V0Facts = collections.namedtuple("V0Facts", "krate main_idents drop_crate control_idents")
V0Facts.__doc__ = """What the frame classifier reads from one v0 symbol.

    krate        the deciding crate: a path's root crate; an impl's (`M`/`X`)
                 first crate in its SELF TYPE, not in `skip` ("" when the
                 self type names none -- a primitive, the placeholder `p`, a
                 std type); a provided method's (`Y`) self type, else its
                 trait's root crate
    main_idents  the main path's own identifiers, innermost-parent first (not
                 generic arguments', not an impl's self type's)
    drop_crate   for `core::ptr::drop_glue<T>` / `drop_in_place<T>`: the first
                 crate not in `skip` anywhere in T, else None
    control_idents  the identifiers in CONTROL's scope, which is legacy's
                 (ruling R28): the main path's, an M/X impl's self type's and
                 drop glue's payload's -- never an ordinary fn's generic
                 arguments, never a `Y` self type's. SEED's scope is
                 `main_idents` of a symbol whose `krate` is std.
"""


class _V0Error(Exception):
    """Not a v0 symbol this reader can read: malformed, truncated, too deep."""


class _V0Parser:
    """Recursive descent over one v0 symbol body (the bytes after `_R`), into
    tuples whose FIRST element is always a tag string -- the grammar letter,
    or "id" (name, dis), "seg", "impl", "dyntrait", "bind", "field" -- so a
    walk over the tree can never mistake data for a node. An `N` chain is ONE
    flat node, ("N", inner, [("seg", ns, ident), ...]), read and walked
    iteratively as the hook reads it (ruling R29): a 1,200-level path must
    not cost 1,200 Python frames."""

    def __init__(self, s):
        self.s, self.pos, self.depth, self.steps = s, 0, 0, 0

    def peek(self):
        return self.s[self.pos] if self.pos < len(self.s) else None

    def next(self):
        c = self.peek()
        if c is None:
            raise _V0Error()
        self.pos += 1
        return c

    def eat(self, c):
        if self.peek() == c:
            self.pos += 1
            return True
        return False

    def enter(self):
        self.depth += 1
        self.steps += 1
        if self.depth > V0_MAX_DEPTH or self.steps > V0_MAX_STEPS:
            raise _V0Error()

    def leave(self):
        self.depth -= 1

    def decimal(self):
        c = self.next()
        if c not in _V0_DIGITS:
            raise _V0Error()
        v = int(c)
        if v == 0:
            return 0
        while self.peek() is not None and self.peek() in _V0_DIGITS:
            v = v * 10 + int(self.next())
            if v >= _U64:
                raise _V0Error()
        return v

    def base62(self):
        if self.eat("_"):
            return 0
        v = 0
        while True:
            c = self.next()
            if c == "_":
                if v + 1 >= _U64:
                    raise _V0Error()
                return v + 1
            if c in _V0_DIGITS:
                d = ord(c) - ord("0")
            elif "a" <= c <= "z":
                d = ord(c) - ord("a") + 10
            elif "A" <= c <= "Z":
                d = ord(c) - ord("A") + 36
            else:
                raise _V0Error()
            v = v * 62 + d
            if v >= _U64:
                raise _V0Error()

    def undis(self):
        self.eat("u")                      # Punycode: kept as its ASCII bytes
        n = self.decimal()
        self.eat("_")                      # the separator before a digit or `_`
        end = self.pos + n
        if end > len(self.s):
            raise _V0Error()
        name = self.s[self.pos:end]
        self.pos = end
        return name

    def ident(self):
        dis = self.base62() + 1 if self.eat("s") else 0
        return ("id", self.undis(), dis)

    def backref(self, parse):
        """After a consumed `B`: parse at the target, then resume here."""
        at = self.pos - 1
        target = self.base62()
        if target >= at:
            raise _V0Error()
        back, self.pos = self.pos, target
        node = parse()
        self.pos = back
        return node

    def path(self):
        self.enter()
        nss = []
        while self.peek() == "N":
            self.pos += 1
            ns = self.next()
            if ns not in string.ascii_letters:
                raise _V0Error()
            nss.append(ns)
        tag = self.next()
        if tag == "C":
            node = ("C", self.ident())
        elif tag == "M":
            impl = self.impl_path()
            node = ("M", impl, self.type())
        elif tag == "X":
            impl = self.impl_path()
            self_type = self.type()
            node = ("X", impl, self_type, self.path())
        elif tag == "Y":
            self_type = self.type()
            node = ("Y", self_type, self.path())
        elif tag == "I":
            inner, args = self.path(), []
            while not self.eat("E"):
                args.append(self.generic_arg())
            node = ("I", inner, args)
        elif tag == "B":
            node = self.backref(self.path)
        else:
            raise _V0Error()
        if nss:                            # `N a N b <inner> id_b id_a`
            node = ("N", node, [("seg", ns, self.ident()) for ns in reversed(nss)])
        self.leave()
        return node

    def impl_path(self):
        dis = self.base62() + 1 if self.eat("s") else 0
        return ("impl", dis, self.path())

    def generic_arg(self):
        if self.eat("L"):
            self.base62()
            return ("L",)
        if self.eat("K"):
            return ("K", self.const())
        return self.type()

    def type(self):
        self.eat("w")
        tag = self.next()
        if tag in _V0_BASIC:
            return ("basic", tag)
        self.enter()
        if tag in "RQ":
            if self.eat("L"):
                self.base62()
            node = (tag, self.type())
        elif tag in "POS":
            node = (tag, self.type())
        elif tag == "A":
            elem = self.type()
            node = ("A", elem, self.const())
        elif tag == "T":
            items = []
            while not self.eat("E"):
                items.append(self.type())
            node = ("T", items)
        elif tag == "F":
            if self.eat("G"):
                self.base62()
            self.eat("U")
            abi = None
            if self.eat("K"):
                abi = "C" if self.eat("C") else self.undis()
            params = []
            while not self.eat("E"):
                params.append(self.type())
            node = ("F", abi, params, self.type())
        elif tag == "D":
            if self.eat("G"):
                self.base62()
            traits = []
            while not self.eat("E"):
                trait, binds = self.path(), []
                while self.eat("p"):
                    name = self.undis()
                    binds.append(("bind", name,
                                  ("K", self.const()) if self.eat("K") else self.type()))
                traits.append(("dyntrait", trait, binds))
            if not self.eat("L"):
                raise _V0Error()
            self.base62()
            node = ("D", traits)
        elif tag == "W":
            base = self.type()
            node = ("W", base, self.pattern())
        elif tag == "B":
            node = self.backref(self.type)
        elif tag in "CNMXYI":
            self.pos -= 1
            node = self.path()
        else:
            raise _V0Error()
        self.leave()
        return node

    def const(self):
        tag = self.next()
        self.enter()
        if tag == "p":
            node = ("Kp",)
        elif tag in "htmyojbce":
            node = ("Kint", tag, False, self.hex())
        elif tag in "aslxni":
            neg = self.eat("n")
            node = ("Kint", tag, neg, self.hex())
        elif tag in "RQ":
            if tag == "R" and self.eat("e"):
                node = ("Kint", "e", False, self.hex())
            else:
                node = ("Kref", tag, self.const())
        elif tag in "AT":
            items = []
            while not self.eat("E"):
                items.append(self.const())
            node = ("K" + tag, items)
        elif tag == "V":
            value_path, kind, fields = self.path(), self.next(), []
            if kind == "T":
                while not self.eat("E"):
                    fields.append(("field", None, self.const()))
            elif kind == "S":
                while not self.eat("E"):
                    name = self.ident()
                    fields.append(("field", name, self.const()))
            elif kind != "U":
                raise _V0Error()
            node = ("KV", value_path, kind, fields)
        elif tag == "B":
            node = self.backref(self.const)
        else:
            raise _V0Error()
        self.leave()
        return node

    def hex(self):
        start = self.pos
        while True:
            c = self.next()
            if c == "_":
                return self.s[start:self.pos - 1]
            if c not in _V0_HEX:
                raise _V0Error()

    def pattern(self):
        tag = self.next()
        if tag == "R":
            low = self.const()
            return ("WR", low, self.const())
        if tag == "N":
            return ("WN",)
        if tag == "O":
            self.enter()
            items = [self.pattern()]
            while not self.eat("E"):
                items.append(self.pattern())
            self.leave()
            return ("WO", items)
        raise _V0Error()


def _v0_parse(sym):
    """(main path, instantiating crate or None) of a v0 symbol -- `_R`, or
    Mach-O's `__R` -- or None."""
    if sym.startswith("__R"):
        body = sym[3:]
    elif sym.startswith("_R"):
        body = sym[2:]
    else:
        return None
    p = _V0Parser(body)
    try:
        if p.peek() is not None and p.peek() in _V0_DIGITS:
            p.decimal()                    # the encoding version: never emitted today
        main, inst = p.path(), None
        if p.peek() not in (None, ".", "$"):
            inst = p.path()
            if p.peek() not in (None, ".", "$"):
                return None
    except Exception:                      # noqa: BLE001 -- R29: unreadable, never a crash
        return None
    return main, inst


def _v0_idents(node):
    """Every identifier in `node`, in the order the symbol spells them."""
    if isinstance(node, list):
        for item in node:
            yield from _v0_idents(item)
    elif isinstance(node, tuple) and node:
        tag = node[0]
        if tag == "id":
            yield node[1]
            return
        if tag == "bind":
            yield node[1]
        elif tag == "F" and node[1] not in (None, "C"):
            yield node[1]                  # an ABI read as an identifier
        for part in node[1:]:
            yield from _v0_idents(part)


def _v0_crates(node):
    """Every crate root in `node`, in the order the symbol spells them."""
    if isinstance(node, list):
        for item in node:
            yield from _v0_crates(item)
    elif isinstance(node, tuple) and node:
        if node[0] == "C":
            yield node[1][1]
            return
        for part in node[1:]:
            yield from _v0_crates(part)


def _v0_first_crate(node, skip):
    return next((c for c in _v0_crates(node) if c and c not in skip), None)


def _v0_decide(node, skip, st):
    """The deciding crate of the main path `node`; notes its own identifiers,
    the last one read, drop glue's payload crate, and CONTROL's scope (R28)
    in `st`."""
    tag = node[0]
    if tag == "C":
        st["last"] = ""
        return node[1][1]
    if tag == "N":
        krate = _v0_decide(node[1], skip, st)
        for _seg, _ns, ident in node[2]:
            st["main"].append(ident[1])
            st["control"].append(ident[1])
            st["last"] = ident[1]
        return krate
    if tag in ("M", "X"):
        st["last"] = ""
        st["control"].extend(_v0_idents(node[2]))
        return _v0_first_crate(node[2], skip) or ""
    if tag == "Y":
        own = _v0_first_crate(node[1], skip)
        trait = _v0_decide(node[2], skip, st)
        return own or trait
    if tag == "I":
        krate = _v0_decide(node[1], skip, st)
        if st["last"] in _V0_DROP_GLUE:
            for arg in node[2]:
                st["control"].extend(_v0_idents(arg))
            if st["drop"] is None:
                st["drop"] = next((c for c in (_v0_first_crate(a, skip) for a in node[2])
                                   if c), None)
        return krate
    raise _V0Error()


def v0_facts(sym, skip):
    """The `V0Facts` of v0 symbol `sym`, or None when it is not one this
    reader can read. `skip` names the crates a self-type or drop-glue search
    steps over. Never raises: anything that goes wrong is "unreadable" (R29)."""
    try:
        parsed = _v0_parse(sym)
        if parsed is None:
            return None
        st = {"main": [], "last": "", "drop": None, "control": []}
        krate = _v0_decide(parsed[0], skip, st)
        return V0Facts(krate, tuple(st["main"]), st["drop"], tuple(st["control"]))
    except Exception:                      # noqa: BLE001
        return None


def _v0_print(n):
    tag = n[0]
    if tag == "C":
        return n[1][1]
    if tag == "N":
        out = _v0_print(n[1])
        for _seg, ns, (_, name, dis) in n[2]:
            if ns in string.ascii_uppercase:
                label = {"C": "closure", "S": "shim"}.get(ns, ns)
                out = "%s::{%s%s#%d}" % (out, label, ":" + name if name else "", dis)
            elif name:
                out = out + "::" + name
        return out
    if tag == "M":
        return "<%s>" % _v0_print(n[2])
    if tag == "X":
        return "<%s as %s>" % (_v0_print(n[2]), _v0_print(n[3]))
    if tag == "Y":
        return "<%s as %s>" % (_v0_print(n[1]), _v0_print(n[2]))
    if tag == "I":
        return "%s<%s>" % (_v0_print(n[1]), ", ".join(_v0_print(a) for a in n[2]))
    if tag == "basic":
        return _V0_BASIC[n[1]]
    if tag in ("R", "Q", "P", "O"):
        return {"R": "&", "Q": "&mut ", "P": "*const ", "O": "*mut "}[tag] + _v0_print(n[1])
    if tag == "S":
        return "[%s]" % _v0_print(n[1])
    if tag == "A":
        return "[%s; %s]" % (_v0_print(n[1]), _v0_print(n[2]))
    if tag == "T":
        items = [_v0_print(t) for t in n[1]]
        return "(%s%s)" % (", ".join(items), "," if len(items) == 1 else "")
    if tag == "F":
        abi = 'extern "%s" ' % n[1].replace("_", "-") if n[1] else ""
        ret = "" if n[3] == ("basic", "u") else " -> " + _v0_print(n[3])
        return "%sfn(%s)%s" % (abi, ", ".join(_v0_print(t) for t in n[2]), ret)
    if tag == "D":
        parts = []
        for _, trait, binds in n[1]:
            text = _v0_print(trait)
            if binds:
                text += "<%s>" % ", ".join("%s = %s" % (b, _v0_print(v)) for _, b, v in binds)
            parts.append(text)
        return "dyn " + " + ".join(parts)
    if tag == "W":
        return "%s is %s" % (_v0_print(n[1]), _v0_print(n[2]))
    if tag == "WR":
        return "%s..=%s" % (_v0_print(n[1]), _v0_print(n[2]))
    if tag == "WN":
        return "!null"
    if tag == "WO":
        return " | ".join(_v0_print(p) for p in n[1])
    if tag == "L":
        return "'_"
    if tag == "K":
        return _v0_print(n[1])
    if tag == "Kp":
        return "_"
    if tag == "Kint":
        ty, neg, digits = n[1], n[2], n[3]
        if ty == "e":
            try:
                return '"%s"' % bytes.fromhex(digits).decode("utf-8", "replace")
            except ValueError:
                return '"?"'
        value = int(digits, 16) if digits else 0
        if ty == "b":
            return "true" if value else "false"
        if ty == "c":
            return repr(chr(value)) if value < 0x110000 else "?"
        return ("-" if neg else "") + str(value)
    if tag == "Kref":
        return ("&" if n[1] == "R" else "&mut ") + _v0_print(n[2])
    if tag == "KA":
        return "[%s]" % ", ".join(_v0_print(c) for c in n[1])
    if tag == "KT":
        return "(%s)" % ", ".join(_v0_print(c) for c in n[1])
    if tag == "KV":
        head = _v0_print(n[1])
        if n[2] == "T":
            return "%s(%s)" % (head, ", ".join(_v0_print(c) for _, _, c in n[3]))
        if n[2] == "S":
            return "%s { %s }" % (head, ", ".join("%s: %s" % (ident[1], _v0_print(c))
                                                  for _, ident, c in n[3]))
        return head
    return "?"


def v0_demangle(sym):
    """The main path of v0 symbol `sym` as Rust prints a path -- crate
    disambiguators, the instantiating crate and any suffix dropped -- or None
    when it is not one. Never raises."""
    try:
        parsed = _v0_parse(sym)
        return None if parsed is None else _v0_print(parsed[0])
    except Exception:                      # noqa: BLE001 -- R29: unreadable, never a crash
        return None


def _crate_symbol(name: str, macho: bool) -> bool:
    """Is `name` a Rust symbol a hook could attribute to a crate other than
    std/core/alloc/test? Decided PER SYMBOL: v0 (`_R`/`__R`) or legacy."""
    if name.startswith(("_R", "__R")):
        f = v0_facts(name, _NON_CRATE)
        return bool(f and f.krate and f.krate not in _NON_CRATE)
    crate = _macho_crate(name) if macho else _mangled_crate(name)
    return bool(crate and crate not in _NON_CRATE)


# ── ELF64 LE ──────────────────────────────────────────────────────────────

_ELF_EHDR_REST = "<HHIQQQIHHHHHH"   # e_type .. e_shstrndx, right after e_ident
_ELF_PHDR = "<IIQQQQQQ"             # Elf64_Phdr
_ELF_SHDR = "<IIQQQQIIQQ"           # Elf64_Shdr
_ELF_SYM = "<IBBHQQ"                # Elf64_Sym

_PT_INTERP = 3
_SHT_SYMTAB = 2


def _elf_parts(data: bytes):
    """(dynamic, libc, symbol names) of an ELF64 LE image, or None when it is
    not one. Raises _Truncated."""
    if len(data) < 16 or data[4] != 2 or data[5] != 1:   # ELFCLASS64, ELFDATA2LSB
        return None
    (_e_type, _e_machine, _e_version, _e_entry, e_phoff, e_shoff, _e_flags,
     _e_ehsize, e_phentsize, e_phnum, e_shentsize, e_shnum, _e_shstrndx) = \
        _unpack(_ELF_EHDR_REST, data, 16)

    dynamic, libc = False, None
    for i in range(e_phnum):
        p_type, _flags, p_offset, _vaddr, _paddr, p_filesz, _memsz, _align = \
            _unpack(_ELF_PHDR, data, e_phoff + i * e_phentsize)
        if p_type == _PT_INTERP:
            dynamic = True
            interp = data[p_offset:p_offset + p_filesz].split(b"\0", 1)[0] \
                .decode("utf-8", "replace")
            libc = "musl" if "ld-musl" in interp else "glibc"

    symtab = None
    for i in range(e_shnum):
        _name, sh_type, _fl, _addr, sh_offset, sh_size, sh_link, _info, _al, _ent = \
            _unpack(_ELF_SHDR, data, e_shoff + i * e_shentsize)
        if sh_type == _SHT_SYMTAB:
            symtab = (sh_offset, sh_size, sh_link)
            break

    names = []
    if symtab is not None:
        sh_offset, sh_size, sh_link = symtab
        _sname, _stype, _sfl, _saddr, str_offset, str_size, _sl, _si, _sal, _sent = \
            _unpack(_ELF_SHDR, data, e_shoff + sh_link * e_shentsize)
        strtab = data[str_offset:str_offset + str_size]
        entsize = struct.calcsize(_ELF_SYM)
        count = sh_size // entsize if entsize else 0
        for i in range(1, count):                       # entry 0 is the null symbol
            st_name, _info, _other, _shndx, _value, _size = \
                _unpack(_ELF_SYM, data, sh_offset + i * entsize)
            names.append(_cstr(strtab, st_name))
    return dynamic, libc, names


def _inspect_elf(data: bytes) -> BinaryFacts:
    parts = _elf_parts(data)
    if parts is None:
        return BinaryFacts("other", False, None, 0)
    dynamic, libc, names = parts
    return BinaryFacts("elf64", dynamic, libc, sum(_crate_symbol(n, False) for n in names))


# ── Mach-O 64 ────────────────────────────────────────────────────────────

_MACHO_MAGIC_64 = 0xFEEDFACF
_MACHO_HDR = "<IiiIIIII"            # mach_header_64
_MACHO_LC_HEAD = "<II"              # cmd, cmdsize (shared prefix of every load command)
_MACHO_SYMTAB_LC = "<IIIIII"        # symtab_command
_MACHO_NLIST = "<IBBHQ"             # nlist_64

_LC_SYMTAB = 0x2
_LC_LOAD_DYLINKER = 0xE


def _macho_parts(data: bytes):
    """(dynamic, symbol names) of a Mach-O 64 image, or None when it is not
    one. Raises _Truncated."""
    magic, _cputype, _cpusubtype, _filetype, ncmds, _sizeofcmds, _flags, _reserved = \
        _unpack(_MACHO_HDR, data, 0)
    if magic != _MACHO_MAGIC_64:
        return None

    dynamic = False
    symtab_cmd = None
    off = struct.calcsize(_MACHO_HDR)
    for _ in range(ncmds):
        cmd, cmdsize = _unpack(_MACHO_LC_HEAD, data, off)
        if cmdsize < struct.calcsize(_MACHO_LC_HEAD) or off + cmdsize > len(data):
            raise _Truncated()
        if cmd == _LC_LOAD_DYLINKER:
            dynamic = True
        elif cmd == _LC_SYMTAB:
            _cmd, _size, symoff, nsyms, stroff, strsize = \
                _unpack(_MACHO_SYMTAB_LC, data, off)
            symtab_cmd = (symoff, nsyms, stroff, strsize)
        off += cmdsize

    names = []
    if symtab_cmd is not None:
        symoff, nsyms, stroff, strsize = symtab_cmd
        strtab = data[stroff:stroff + strsize]
        entsize = struct.calcsize(_MACHO_NLIST)
        for i in range(nsyms):
            n_strx, _type, _sect, _desc, _value = \
                _unpack(_MACHO_NLIST, data, symoff + i * entsize)
            names.append(_cstr(strtab, n_strx))
    return dynamic, names


def _inspect_macho(data: bytes) -> BinaryFacts:
    parts = _macho_parts(data)
    if parts is None:
        return BinaryFacts("other", False, None, 0)
    dynamic, names = parts
    return BinaryFacts("macho64", dynamic, "darwin", sum(_crate_symbol(n, True) for n in names))


# ── Entry points ─────────────────────────────────────────────────────────

def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def inspect(path) -> BinaryFacts:
    """The `BinaryFacts` of the file at `path`. Never raises.

    Reads the whole file, dispatches on its magic, and turns any bounds
    violation -- a truncated file, an offset a hostile or corrupt header
    points off the end of the buffer -- into `fmt = "other"` rather than
    letting it propagate. A file that cannot even be opened is `"other"`
    too, for the same reason: the guard must be able to call this on
    anything and get an answer, not an exception.
    """
    data = _read(path)
    if data is None:
        return BinaryFacts("other", False, None, 0)
    try:
        if data[:4] == b"\x7fELF":
            return _inspect_elf(data)
        if len(data) >= 4 and struct.unpack_from("<I", data, 0)[0] == _MACHO_MAGIC_64:
            return _inspect_macho(data)
    except (_Truncated, struct.error):
        return BinaryFacts("other", False, None, 0)
    return BinaryFacts("other", False, None, 0)


def symbol_names(path) -> list:
    """Every symbol name in the ELF64 or Mach-O 64 file at `path`, in table
    order; [] for anything else, a truncated file included. Never raises."""
    data = _read(path)
    if data is None:
        return []
    try:
        if data[:4] == b"\x7fELF":
            parts = _elf_parts(data)
            return parts[2] if parts else []
        if len(data) >= 4 and struct.unpack_from("<I", data, 0)[0] == _MACHO_MAGIC_64:
            parts = _macho_parts(data)
            return parts[1] if parts else []
    except (_Truncated, struct.error):
        return []
    return []


def refusal(facts: BinaryFacts):
    """The reason a preloaded hook would fail open on `facts`, or None.

    Checked in this order -- the first true reason wins, so a stripped
    static binary reports "statically linked", the more fundamental defect:

      1. not a 64-bit ELF or Mach-O executable at all;
      2. no dynamic loader to preload into;
      3. a loader the hook is not built for (musl);
      4. no crate symbol to attribute a resolved frame to.
    """
    if facts.fmt == "other":
        return "not a 64-bit ELF or Mach-O executable"
    if not facts.dynamic:
        return "statically linked: LD_PRELOAD cannot load a hook"
    if facts.libc == "musl":
        return "musl: the hook is built for glibc's dynamic loader"
    if facts.crate_symbols == 0:
        return "stripped: no crate symbols, so no call can be attributed"
    return None
