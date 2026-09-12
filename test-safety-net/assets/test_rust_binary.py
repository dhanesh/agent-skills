#!/usr/bin/env python3
"""Tests for `rust_binary.py`: does it refuse the binaries a preloaded hook
cannot guard, and only those?

`inspect` is pure struct-reading, so most of the suite builds the exact
bytes it reads -- an ELF64 or Mach-O64 image with only the fields `inspect`
looks at -- and checks `refusal` against them. Each of the four rules
(not-64-bit, static, musl, stripped) gets one positive test per format that
proves the OTHER three are not what's tripping, plus the "everything is
fine" case. `TestRealBinaries` closes the loop against an actual `cargo`
build: it skips VISIBLY without `cargo` (and without `strip`), so the run
reads `OK (skipped=N)` rather than failing on a machine that lacks the
toolchain.

Mutation coverage: making `refusal` ignore `crate_symbols` is killed by
`test_a_stripped_dynamic_binary_is_refused` (ELF) and
`test_a_stripped_dynamic_binary_is_refused` (Mach-O) below, and by
`TestRealBinaries.test_a_stripped_copy_is_refused_as_stripped`.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rust_binary = _load("rust_binary")


# ── Synthetic image builders (task brief, verbatim: already round-tripped) ─

def elf(interp=None, symbols=()):
    """A minimal ELF64 LE image: header, optional PT_INTERP, optional .symtab/.strtab."""
    nph = 1 if interp else 0
    interp_b = (interp.encode() + b"\0") if interp else b""
    off = 64 + 56 * nph
    interp_off, off = off, off + len(interp_b)
    strtab = b"\0" + b"".join(s.encode() + b"\0" for s in symbols)
    str_off, off = off, off + len(strtab)
    syms, pos = struct.pack("<IBBHQQ", 0, 0, 0, 0, 0, 0), 1
    for s in symbols:                       # STB_GLOBAL | STT_FUNC, defined, sized
        syms += struct.pack("<IBBHQQ", pos, 0x12, 0, 1, 0x1000, 16)
        pos += len(s) + 1
    sym_off, off = off, off + len(syms)
    shstr = b"\0.symtab\0.strtab\0.shstrtab\0"
    shstr_off, off = off, off + len(shstr)
    sh = [struct.pack("<IIQQQQIIQQ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)]
    if symbols:
        sh.append(struct.pack("<IIQQQQIIQQ", 1, 2, 0, 0, sym_off, len(syms), 2, 1, 8, 24))
        sh.append(struct.pack("<IIQQQQIIQQ", 9, 3, 0, 0, str_off, len(strtab), 0, 0, 1, 0))
    sh.append(struct.pack("<IIQQQQIIQQ", 17, 3, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0))
    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\0" * 8          # 64-bit, little-endian
    ehdr = ident + struct.pack("<HHIQQQIHHHHHH", 2, 0xB7, 1, 0, 64 if nph else 0, off, 0,
                               64, 56, nph, 64, len(sh), len(sh) - 1)
    phdr = (struct.pack("<IIQQQQQQ", 3, 4, interp_off, 0, 0, len(interp_b), len(interp_b), 1)
            if nph else b"")
    return ehdr + phdr + interp_b + strtab + syms + shstr + b"".join(sh)


def macho(dylinker=True, symbols=()):
    """A minimal 64-bit Mach-O: header, optional LC_LOAD_DYLINKER, optional LC_SYMTAB."""
    cmds = b""
    if dylinker:
        name = b"/usr/lib/dyld\0".ljust(20, b"\0")       # cmdsize 32: a multiple of 8
        cmds += struct.pack("<III", 0xE, 12 + len(name), 12) + name          # LC_LOAD_DYLINKER
    strtab = b"\0" + b"".join(s.encode() + b"\0" for s in symbols)
    ncmds = (1 if dylinker else 0) + (1 if symbols else 0)
    header_len = 32 + len(cmds) + (24 if symbols else 0)
    if symbols:
        sym_off = header_len
        str_off = sym_off + 16 * len(symbols)
        cmds += struct.pack("<IIIIII", 0x2, 24, sym_off, len(symbols), str_off, len(strtab))
    nl, pos = b"", 1
    for s in symbols:                       # N_SECT | N_EXT
        nl += struct.pack("<IBBHQ", pos, 0x0F, 1, 0, 0x100000000)
        pos += len(s) + 1
    hdr = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, ncmds, len(cmds), 0, 0)
    return hdr + cmds + nl + (strtab if symbols else b"")


# The trailing 16 hex chars are the mangler's disambiguator hash, not a
# secret; a fixed, readable one (`0123456789abcdef`) is used throughout so
# a diff never looks like a real hash landed here. Mach-O spells the same
# symbol with one extra leading underscore (`_mangled_crate`'s docstring).
CRATE_SYMBOL = "_ZN5calcx4calc4pure17h0123456789abcdefE"        # scan-leaks:ignore -- mangled Rust symbol fixture, not a secret
STD_SYMBOL = "_ZN3std2io5stdin17h0123456789abcdefE"              # scan-leaks:ignore -- ditto
MACHO_CRATE_SYMBOL = "_" + CRATE_SYMBOL
MACHO_STD_SYMBOL = "_" + STD_SYMBOL


class BinaryCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tsn-rust-binary-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, data: bytes) -> str:
        path = os.path.join(self.root, "exe")
        with open(path, "wb") as f:
            f.write(data)
        return path

    def facts(self, data: bytes):
        return rust_binary.inspect(self.write(data))


# ── ELF64 ────────────────────────────────────────────────────────────────

class TestELF(BinaryCase):
    def test_a_dynamic_glibc_binary_with_a_crate_symbol_is_not_refused(self):
        facts = self.facts(elf(interp="/lib64/ld-linux-x86-64.so.2", symbols=[CRATE_SYMBOL]))
        self.assertEqual(facts, rust_binary.BinaryFacts("elf64", True, "glibc", 1))
        self.assertIsNone(rust_binary.refusal(facts))

    def test_no_pt_interp_is_statically_linked(self):
        facts = self.facts(elf(interp=None, symbols=[CRATE_SYMBOL]))
        self.assertFalse(facts.dynamic)
        self.assertEqual(rust_binary.refusal(facts),
                         "statically linked: LD_PRELOAD cannot load a hook")

    def test_a_musl_interpreter_is_musl(self):
        facts = self.facts(elf(interp="/lib/ld-musl-aarch64.so.1", symbols=[CRATE_SYMBOL]))
        self.assertEqual(facts.libc, "musl")
        self.assertEqual(rust_binary.refusal(facts),
                         "musl: the hook is built for glibc's dynamic loader")

    def test_a_stripped_dynamic_binary_is_refused(self):
        # NEGATIVE: only a std/core/alloc/test symbol is present -- no crate
        # function to attribute a frame to. Mutation "ignore crate_symbols
        # in refusal" is killed here.
        facts = self.facts(elf(interp="/lib64/ld-linux-x86-64.so.2", symbols=[STD_SYMBOL]))
        self.assertEqual(facts.crate_symbols, 0)
        self.assertEqual(rust_binary.refusal(facts),
                         "stripped: no crate symbols, so no call can be attributed")

    def test_a_32_bit_elf_is_not_a_64_bit_executable(self):
        data = b"\x7fELF\x01\x01\x01\x00" + b"\x00" * 60
        facts = self.facts(data)
        self.assertEqual(facts.fmt, "other")
        self.assertEqual(rust_binary.refusal(facts),
                         "not a 64-bit ELF or Mach-O executable")


# ── Mach-O 64 ────────────────────────────────────────────────────────────

class TestMachO(BinaryCase):
    def test_a_dynamic_binary_with_a_crate_symbol_is_not_refused(self):
        facts = self.facts(macho(symbols=[MACHO_CRATE_SYMBOL]))
        self.assertEqual(facts, rust_binary.BinaryFacts("macho64", True, "darwin", 1))
        self.assertIsNone(rust_binary.refusal(facts))

    def test_no_dylinker_is_statically_linked(self):
        facts = self.facts(macho(dylinker=False,
                                 symbols=[MACHO_CRATE_SYMBOL]))
        self.assertFalse(facts.dynamic)
        self.assertEqual(rust_binary.refusal(facts),
                         "statically linked: LD_PRELOAD cannot load a hook")

    def test_a_stripped_dynamic_binary_is_refused(self):
        facts = self.facts(macho(symbols=[MACHO_STD_SYMBOL]))
        self.assertEqual(facts.crate_symbols, 0)
        self.assertEqual(rust_binary.refusal(facts),
                         "stripped: no crate symbols, so no call can be attributed")

    def test_a_32_bit_macho_is_not_a_64_bit_executable(self):
        data = struct.pack("<I", 0xFEEDFACE) + b"\x00" * 60
        facts = self.facts(data)
        self.assertEqual(facts.fmt, "other")
        self.assertEqual(rust_binary.refusal(facts),
                         "not a 64-bit ELF or Mach-O executable")


# ── Bounds safety ────────────────────────────────────────────────────────

class TestTruncation(BinaryCase):
    def test_an_empty_file_is_other_not_an_exception(self):
        facts = self.facts(b"")
        self.assertEqual(facts, rust_binary.BinaryFacts("other", False, None, 0))
        self.assertEqual(rust_binary.refusal(facts),
                         "not a 64-bit ELF or Mach-O executable")

    def test_a_truncated_elf_is_other(self):
        # A whole, valid image, cut at three points that each remove data
        # `inspect` actually reads: mid fixed header (16, past e_ident;
        # 40, mid e_phoff/e_shoff), and inside the section-header table
        # itself (dropping the .strtab section's own header, which is read
        # by index through .symtab's sh_link -- three of the four 64-byte
        # entries removed, leaving only the null entry).
        whole = elf(interp="/lib64/ld-linux-x86-64.so.2", symbols=[CRATE_SYMBOL])
        for cut in (16, 40, len(whole) - 3 * 64):
            with self.subTest(cut=cut):
                self.assertEqual(self.facts(whole[:cut]).fmt, "other")

    def test_a_truncated_macho_is_other(self):
        # Cut at: mid fixed header (4, 20); inside LC_SYMTAB's own body (72
        # -- its 8-byte cmd/cmdsize header is present, but not the full 24
        # bytes the header declares, past mach_header_64's 32 + the 32-byte
        # LC_LOAD_DYLINKER); and inside the first nlist entry (93 -- past
        # where the symbol table starts at 88, but short of one full
        # 16-byte entry).
        whole = macho(symbols=[MACHO_CRATE_SYMBOL])
        for cut in (4, 20, 72, 93):
            with self.subTest(cut=cut):
                self.assertEqual(self.facts(whole[:cut]).fmt, "other")

    def test_inspect_never_raises_on_any_prefix(self):
        # Property check across every possible truncation point: `inspect`
        # always returns a BinaryFacts, never an exception.
        for whole in (elf(interp="/lib64/ld-linux-x86-64.so.2", symbols=[CRATE_SYMBOL]),
                     macho(symbols=[MACHO_CRATE_SYMBOL])):
            for cut in range(0, len(whole), 7):
                with self.subTest(cut=cut):
                    facts = self.facts(whole[:cut])
                    self.assertIn(facts.fmt, ("elf64", "macho64", "other"))

    def test_a_file_that_cannot_be_opened_is_other(self):
        facts = rust_binary.inspect(os.path.join(self.root, "does-not-exist"))
        self.assertEqual(facts, rust_binary.BinaryFacts("other", False, None, 0))


# ── The demangler ────────────────────────────────────────────────────────

class TestMangledCrate(unittest.TestCase):
    def test_the_crate_is_the_first_segment(self):
        self.assertEqual(rust_binary._mangled_crate(CRATE_SYMBOL), "calcx")

    def test_std_core_alloc_and_test_are_excluded_by_the_caller(self):
        # _mangled_crate itself just answers the segment; refusal's exclusion
        # of std/core/alloc/test is exercised end-to-end by the ELF/Mach-O
        # stripped tests above.
        for crate, sym in (("std", STD_SYMBOL),
                           ("core", "_ZN4core3fmt5Write17h0123456789abcdefE"),        # scan-leaks:ignore -- mangled Rust symbol fixture, not a secret
                           ("alloc", "_ZN5alloc3vec3Vec3new17h0123456789abcdefE"),    # scan-leaks:ignore -- ditto
                           ("test", "_ZN4test8TestDesc17h0123456789abcdefE")):        # scan-leaks:ignore -- ditto
            with self.subTest(sym=sym):
                self.assertEqual(rust_binary._mangled_crate(sym), crate)

    def test_non_mangled_names_are_none(self):
        for sym in ("main", "_start", "printf", "_ZN5calcxE", "_ZN5calcx17hxxxxxxxxxxxxxxxxE",
                    "_ZN5calcx16h0123456789abcdE", ""):
            with self.subTest(sym=sym):
                self.assertIsNone(rust_binary._mangled_crate(sym))

    def test_macho_strips_the_extra_leading_underscore(self):
        self.assertEqual(rust_binary._macho_crate("_" + CRATE_SYMBOL), "calcx")
        self.assertIsNone(rust_binary._macho_crate(CRATE_SYMBOL))   # only one underscore


# ── Real binaries: cargo, then strip ─────────────────────────────────────

CARGO = shutil.which("cargo")
STRIP = shutil.which("strip")

LIB_RS = ("pub fn pure(n: u32) -> u32 { n }\n\n"
          "#[cfg(test)]\n"
          "mod tests {\n"
          "    #[test]\n"
          "    fn t() { assert_eq!(super::pure(1), 1); }\n"
          "}\n")


def _build_test_binary(target_dir: str, crate_dir: str):
    """Build a tiny crate's test binary; return its path, or None."""
    os.makedirs(os.path.join(crate_dir, "src"), exist_ok=True)
    with open(os.path.join(crate_dir, "Cargo.toml"), "w", encoding="utf-8") as f:
        f.write('[package]\nname = "calcx"\nversion = "0.1.0"\nedition = "2021"\n')
    with open(os.path.join(crate_dir, "src", "lib.rs"), "w", encoding="utf-8") as f:
        f.write(LIB_RS)
    env = dict(os.environ, CARGO_TARGET_DIR=target_dir)
    proc = subprocess.run(["cargo", "test", "--no-run", "--message-format=json"],
                          cwd=crate_dir, env=env, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise AssertionError("cargo test --no-run failed:\n%s" % proc.stderr)
    exe = None
    for line in proc.stdout.splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("reason") == "compiler-artifact" and msg.get("executable") \
                and (msg.get("profile") or {}).get("test"):
            exe = msg["executable"]
    return exe


class TestRealBinaries(unittest.TestCase):
    """`inspect`/`refusal` against a binary `cargo` actually produced.

    Skips visibly -- `OK (skipped=N)` -- without `cargo` or `strip` on
    PATH, so the suite still runs to completion on a machine without the
    Rust toolchain. CI and the `rust:1.92` container run both installed.
    """

    @classmethod
    def setUpClass(cls):
        if not CARGO:
            raise unittest.SkipTest("no `cargo` on PATH: rust_binary.py's real-binary tests "
                                    "are NOT exercised on this machine.")
        cls.tmp = tempfile.mkdtemp(prefix="tsn-rust-binary-real-")
        cls.exe = _build_test_binary(os.path.join(cls.tmp, "target"),
                                     os.path.join(cls.tmp, "crate"))
        if not cls.exe or not os.path.isfile(cls.exe):
            raise unittest.SkipTest("cargo did not report a test binary's path")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_a_freshly_built_test_binary_is_not_refused(self):
        facts = rust_binary.inspect(self.exe)
        self.assertIn(facts.fmt, ("elf64", "macho64"))
        self.assertTrue(facts.dynamic)
        self.assertGreater(facts.crate_symbols, 0)
        self.assertIsNone(rust_binary.refusal(facts))

    def test_a_stripped_copy_is_refused_as_stripped(self):
        if not STRIP:
            raise unittest.SkipTest("no `strip` on PATH")
        copy_path = self.exe + "-stripped"
        shutil.copy(self.exe, copy_path)
        proc = subprocess.run(["strip", copy_path], capture_output=True, text=True)
        if proc.returncode != 0:
            raise unittest.SkipTest("`strip` failed: %s" % proc.stderr)
        facts = rust_binary.inspect(copy_path)
        self.assertEqual(facts.crate_symbols, 0)
        self.assertEqual(rust_binary.refusal(facts),
                         "stripped: no crate symbols, so no call can be attributed")


if __name__ == "__main__":
    unittest.main()
