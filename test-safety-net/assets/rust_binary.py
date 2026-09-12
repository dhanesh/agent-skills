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

This module is pure inspection: it does not run the binary, does not shell
out, and does not know about `LD_PRELOAD` or tiers. Task 7's wrapper is the
only caller: `refusal(inspect(exe))`.
"""
from __future__ import annotations

import collections
import struct

BinaryFacts = collections.namedtuple("BinaryFacts", "fmt dynamic libc crate_symbols")
BinaryFacts.__doc__ = """What the guard needs to know about a compiled test binary.

    fmt            "elf64", "macho64", or "other" (not a 64-bit ELF/Mach-O)
    dynamic        True when the binary can load a preloaded hook at all
    libc           "glibc", "musl", "darwin", or None (unknown / not dynamic)
    crate_symbols  count of Rust-mangled symbols whose crate is not
                   std/core/alloc/test -- the frames a hook could attribute
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


# ── ELF64 LE ──────────────────────────────────────────────────────────────

_ELF_EHDR_REST = "<HHIQQQIHHHHHH"   # e_type .. e_shstrndx, right after e_ident
_ELF_PHDR = "<IIQQQQQQ"             # Elf64_Phdr
_ELF_SHDR = "<IIQQQQIIQQ"           # Elf64_Shdr
_ELF_SYM = "<IBBHQQ"                # Elf64_Sym

_PT_INTERP = 3
_SHT_SYMTAB = 2


def _inspect_elf(data: bytes) -> BinaryFacts:
    if len(data) < 16 or data[4] != 2 or data[5] != 1:   # ELFCLASS64, ELFDATA2LSB
        return BinaryFacts("other", False, None, 0)
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

    crate_symbols = 0
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
            crate = _mangled_crate(_cstr(strtab, st_name))
            if crate and crate not in _NON_CRATE:
                crate_symbols += 1

    return BinaryFacts("elf64", dynamic, libc, crate_symbols)


# ── Mach-O 64 ────────────────────────────────────────────────────────────

_MACHO_MAGIC_64 = 0xFEEDFACF
_MACHO_HDR = "<IiiIIIII"            # mach_header_64
_MACHO_LC_HEAD = "<II"              # cmd, cmdsize (shared prefix of every load command)
_MACHO_SYMTAB_LC = "<IIIIII"        # symtab_command
_MACHO_NLIST = "<IBBHQ"             # nlist_64

_LC_SYMTAB = 0x2
_LC_LOAD_DYLINKER = 0xE


def _inspect_macho(data: bytes) -> BinaryFacts:
    magic, _cputype, _cpusubtype, _filetype, ncmds, _sizeofcmds, _flags, _reserved = \
        _unpack(_MACHO_HDR, data, 0)
    if magic != _MACHO_MAGIC_64:
        return BinaryFacts("other", False, None, 0)

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

    crate_symbols = 0
    if symtab_cmd is not None:
        symoff, nsyms, stroff, strsize = symtab_cmd
        strtab = data[stroff:stroff + strsize]
        entsize = struct.calcsize(_MACHO_NLIST)
        for i in range(nsyms):
            n_strx, _type, _sect, _desc, _value = \
                _unpack(_MACHO_NLIST, data, symoff + i * entsize)
            crate = _macho_crate(_cstr(strtab, n_strx))
            if crate and crate not in _NON_CRATE:
                crate_symbols += 1

    return BinaryFacts("macho64", dynamic, "darwin", crate_symbols)


# ── Entry points ─────────────────────────────────────────────────────────

def inspect(path) -> BinaryFacts:
    """The `BinaryFacts` of the file at `path`. Never raises.

    Reads the whole file, dispatches on its magic, and turns any bounds
    violation -- a truncated file, an offset a hostile or corrupt header
    points off the end of the buffer -- into `fmt = "other"` rather than
    letting it propagate. A file that cannot even be opened is `"other"`
    too, for the same reason: the guard must be able to call this on
    anything and get an answer, not an exception.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return BinaryFacts("other", False, None, 0)
    try:
        if data[:4] == b"\x7fELF":
            return _inspect_elf(data)
        if len(data) >= 4 and struct.unpack_from("<I", data, 0)[0] == _MACHO_MAGIC_64:
            return _inspect_macho(data)
    except (_Truncated, struct.error):
        return BinaryFacts("other", False, None, 0)
    return BinaryFacts("other", False, None, 0)


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
