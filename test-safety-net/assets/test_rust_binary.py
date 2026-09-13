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

Ruling R26 (rustc 1.98 mangles v0 by default, std and libtest included):
* counting legacy symbols alone ->
  `TestV0Binaries.test_an_elf_with_only_v0_crate_symbols_is_not_refused` (and
  the Mach-O twin), and on a 1.98 toolchain
  `TestRealBinaries.test_a_freshly_built_test_binary_is_not_refused`;
* measuring a back-reference from the symbol's first byte ->
  `TestV0Parser.test_a_back_reference_resolves_from_just_after_the_prefix`.

Ruling R28 (the name tables keep legacy scope) and R29 (a bounded reader):
* collecting every identifier into `control_idents`, or reading SEED off a
  non-std symbol -> `TestV0Parser.test_the_name_tables_keep_legacy_scope`;
* an `N` chain costing one Python frame (or one depth step) per level ->
  `TestV0Parser.test_a_deep_nested_path_reads_without_recursion`.

Ruling R42 (residual 11: the crate-export list), each run:
* reading every member, not only `*.rcgu.o` ->
  `TestRlibExports.test_a_gnu_rlib_lists_its_rcgu_members_unmangled_text_alone`
  and `..._native_member_is_never_read_even_as_bitcode`;
* keeping data symbols, or dropping the mangled filter ->
  `TestObjectExports.test_elf_keeps_defined_global_and_weak_text_only` and
  `TestRlibExports.test_an_rlib_with_no_unmangled_fn_is_an_empty_list`;
* reading bitcode as "no symbols" instead of refusing ->
  `TestRlibExports.test_a_bitcode_rcgu_member_refuses_and_names_the_rlib_and_member`
  and, on real rustc output, `TestRealRlibs.test_a_linker_plugin_lto_rlib_is_refused`.
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


# ── v0 mangling (ruling R26: rustc 1.98's default) ───────────────────────
#
# Real rustc 1.98 symbols (darwin `nm`, the Mach-O underscore dropped), each
# crate's disambiguator replaced by a readable one of the SAME length -- 11
# base-62 characters, as rustc's are -- so every back-reference `B<n>_`,
# an offset measured from just after `_R`, still lands where rustc put it.
# A disambiguator is a u64: the leading `0` keeps each readable one inside
# it, as a real one is (`stdxxxxxxxx` would overflow and rightly not parse).
_V0_DIS = {name: "Cs" + ("0" + name + "x" * 11)[:11] + "_"
           for name in ("std", "core", "alloc", "test", "p", "calcx")}


def v0(template):
    return template.format(**_V0_DIS)


V0 = {
    # plain paths; the instantiating-crate suffix (`B3_`, `{p}1p`) never decides
    "crate_fn": v0("_RNv{p}1p20tsn_control_temp_dir"),
    "calcx_fn": v0("_RNv{calcx}5calcx4pure"),
    "std_fn": v0("_RNvNt{std}3std2io5stdin"),
    "closure": v0("_RNCNv{p}1p8t_thread0B3_"),
    "vendor_suffix": v0("_RNvNtNt{std}3std2io5stdio19OUTPUT_CAPTURE_USED.0"),
    # impls: the SELF TYPE decides (R14), reached here through a back-reference
    "inherent_backref": v0("_RNvMs1_{p}1pNtB5_3Dsp3inh"),
    "trait_impl_backref": v0("_RNvXs3_{p}1pNtB5_13TsnControlEnv"
                             "NtNtNt{core}4core3ops4drop4Drop4drop"),
    "blanket_dsp": v0("_RNCNvXs2_{p}1pNtB7_3DspNtB7_7Blanket1b0B7_"),
    # R18: a primitive or the placeholder names no crate
    "blanket_u8": v0("_RNCNvXs2_{p}1phNtB7_7Blanket1b0B7_"),
    "blanket_slice": v0("_RNCNvXs2_{p}1pRShNtB7_7Blanket1b0B7_"),
    "blanket_placeholder": v0("_RNvX{p}1ppNtB2_7Blanket1b"),
    # provided trait methods (`Y`): the self type, else the trait
    "provided_std": v0("_RINvYNtNtNt{std}3std4hash6random11RandomState"
                       "NtNt{core}4core4hash11BuildHasher8hash_oneRlE{p}1p"),
    "provided_crate_self": v0("_RNvYNt{p}1p2PdNtNt{core}4core3fmt5Write9write_fmt"),
    "provided_crate_trait": v0("_RNvYhNt{p}1p2Ob1o"),
    # libtest's body-side boundaries (R13, R17a) and std's look-alike
    "begin_short_backtrace": v0("_RINv{test}4test28___rust_begin_short_backtrace"
                                "INtNt{core}4core6result6ResultuNtNt{alloc}5alloc6string"
                                "6StringEFEBQ_EB2_"),
    "assert_test_result": v0("_RINv{test}4test18assert_test_resultNt{p}1p3RepEBH_"),
    "std_begin_short_backtrace": v0("_RINvNtNt{std}3std3sys9backtrace"
                                    "28___rust_begin_short_backtraceFEuuE{p}1p"),
    "libtest_fn": v0("_RNv{test}4test16test_main_static"),
    # drop glue (R17b): the first non-transparent crate anywhere in T
    "drop_vec": v0("_RINvNt{core}4core3ptr9drop_glueINtNtB4_6option6OptionINtNt{alloc}5alloc3vec"
                   "3VecNt{p}1p2PdEEEB1w_"),
    "drop_tuple": v0("_RINvNt{core}4core3ptr9drop_glueINtNtB4_6option6OptionTmNt{p}1p2PdEEEB11_"),
    "drop_array": v0("_RINvNt{core}4core3ptr9drop_glueANt{p}1p2Pdj1_EBE_"),
    "drop_dyn": v0("_RINvNt{core}4core3ptr9drop_glueINtNtB4_6option6OptionINtNt{alloc}5alloc5boxed"
                   "3BoxDNt{p}1p2ObEL_EEEB1z_"),
    "drop_std": v0("_RINvNt{core}4core3ptr9drop_glueINtNtB4_6option6OptionINtNtNt{std}3std6thread"
                   "11join_handle10JoinHandleuEEE{test}4test"),
    "drop_std_linux": v0("_RINvNt{core}4core3ptr9drop_glueINtNt{alloc}5alloc3vec3VecNtNtNtNt{std}"
                         "3std4sync4mpmc5waker5EntryEE{test}4test"),
    "drop_runner_type": v0("_RINvNt{core}4core3ptr9drop_glueINtNtB4_6result6ResultNtNt{test}4test"
                           "5event13CompletedTestNtNtNt{std}3std4sync4mpsc16RecvTimeoutErrorEEB11_"),
    "drop_control": v0("_RINvNt{core}4core3ptr9drop_glueNt{p}1p13TsnControlEnvEBD_"),
    # Rc/Arc's deferred drop (R17b extended)
    "drop_slow_std": v0("_RNvMsn_Nt{alloc}5alloc4syncINtB5_3ArcINtNtNt{std}3std6thread9lifecycle"
                        "6PacketuEE9drop_slow{test}4test"),
    "drop_slow_crate": v0("_RNvMs6_Nt{alloc}5alloc2rcINtB5_2RcNt{p}1p2PdE9drop_slowBF_"),
    # std seeding its HashMap
    "seed": v0("_RNvNtNt{std}3std3sys6random19hashmap_random_keys"),
    # Ruling R28: names OUTSIDE the tables' legacy scope. Legacy spells the
    # first two `_ZN3std2fs4read17h…E` -- no generic arguments at all -- so
    # they are std's read, transparent: never std seeding, never a control.
    # A provided method's (`Y`) self type, and a crate fn that merely carries
    # the seed's name (`_ZN1p32f_hashmap_random_keys_named_test17h…E`), are
    # not in scope either.
    "std_read_seed_type": v0("_RINvNt{std}3std2fs4readNt{p}1p19hashmap_random_keysE{p}1p"),
    "std_read_control_type": v0("_RINvNt{std}3std2fs4readNt{p}1p13TsnControlEnvE{p}1p"),
    "provided_control_self": v0("_RNvYNt{p}1p13TsnControlEnvNtNt{core}4core3fmt5Write"
                                "9write_fmt"),
    "crate_fn_seed_name": v0("_RNv{p}1p32f_hashmap_random_keys_named_test"),
    # Ruling R34: a control helper only as a CONCRETE generic argument of an
    # M/X self type, or behind `&`. Legacy prints the impl's declared
    # generics -- `core::result::Result<T,E>::map`, `std::sync::mpsc::
    # Receiver<T>::recv_timeout`, `<alloc::vec::into_iter::IntoIter<T,A> as
    # Iterator>::fold`, `<&T as Debug>::fmt` -- so none is the control.
    "mx_result_map": v0("_RINvMNt{core}4core6resultINtNt{core}4core6result6ResultReNt{p}1p"
                        "13TsnControlEnvE3mapppE{p}1p"),
    "mx_receiver": v0("_RNvMNtNt{std}3std4sync4mpscINtNtNt{std}3std4sync4mpsc8ReceiverNt{p}1p"
                      "13TsnControlEnvE12recv_timeout"),
    "mx_iter_x": v0("_RNvXNt{alloc}5alloc3vecINtNtNt{alloc}5alloc3vec9into_iter8IntoIterINtNt"
                    "{core}4core6result6ResultReNt{p}1p13TsnControlEnvEENtNtNt{core}4core4iter"
                    "6traits8Iterator4fold"),
    "mx_ref_self": v0("_RNvXs_{p}1pRNtB4_13TsnControlEnvNtNt{core}4core3fmt5Debug3fmt"),
    # ... while the helper's OWN impl still is (legacy `p::TsnControlEnv::restore`).
    "inherent_control": v0("_RNvMs0_{p}1pNtB5_13TsnControlEnv7restore"),
    # Ruling R37: v0 prints a blanket `impl<T> Touch for T` at the type it ran
    # on, and `impl<F: Fn> ViaFn for F` at the fn item; legacy prints `<T as
    # p::Touch>::touch`, `<F as p::ViaFn>::via_fn` -- no control. Only core's
    # `Drop` lends its self type a name (its path through a back-reference
    # here); a crate's own trait named `drop::Drop` does not.
    "blanket_x_control": v0("_RNvX{p}1pNt{p}1p13TsnControlEnvNt{p}1p5Touch5touch"),
    "fnitem_x_control": v0("_RNvX{p}1pNv{p}1p19tsn_control_set_envNt{p}1p5ViaFn6via_fn"),
    "drop_x_trait_backref": v0("_RNvXNt{core}4core3opsNt{p}1p13TsnControlEnvNtNtB2_4drop"
                               "4Drop4drop"),
    "drop_lookalike_x": v0("_RNvX{p}1pNt{p}1p13TsnControlEnvNtNt{p}1p4drop4Drop4drop"),
    # Ruling R39: only CORE's drop glue lends its payload a control name. A
    # crate's own fn -- or generic method -- NAMED drop_in_place / drop_glue
    # is printed by legacy without its generic arguments (`_ZN1p13drop_in_
    # place17h…E`, `p::Slot<T>::drop_in_place`, `_ZN1p4core3ptr13drop_in_
    # place17h…E`): the crate's own frame, no control, no payload search.
    "userfn_drop_in_place": v0("_RINv{p}1p13drop_in_placeNt{p}1p13TsnControlEnvE{p}1p"),
    "userfn_drop_glue": v0("_RINv{p}1p9drop_glueRNt{p}1p13TsnControlEnvE{p}1p"),
    "method_drop_in_place": v0("_RINvM{p}1pINt{p}1p4SlothE13drop_in_place"
                               "Nt{p}1p13TsnControlEnvE{p}1p"),
    "nested_core_drop_in_place": v0("_RINvNtNt{p}1p4core3ptr13drop_in_place"
                                    "Nt{p}1p13TsnControlEnvE{p}1p"),
    # ... while core's own still does, under either name (legacy `_ZN4core3ptr
    # 85drop_in_place$LT$p..TsnControlEnv$GT$17h…E`).
    "drop_in_place_control": v0("_RINvNt{core}4core3ptr13drop_in_place"
                                "Nt{p}1p13TsnControlEnvE{p}1p"),
}

# Ruling R29: 1,200 `N` levels, as an `#[export_name]` may spell them. The
# hook reads an `N` chain iteratively; the Python twin must too.
V0_DEEP = "_R" + "Nv" * 1200 + "C1a" + "1f" * 1200


def _nest(depth):
    """drop glue of `depth` nested 1-tuples around a crate type: `((((p::Pd,),),),)`."""
    return v0("_RINvNt{core}4core3ptr9drop_glue" + "T" * depth + "Nt{p}1p2Pd" + "E" * depth + "E")


# Malformed or not v0 at all: each must parse to None, never raise.
V0_MALFORMED = {
    "empty": "",
    "prefix_only": "_R",
    "macho_prefix_only": "__R",
    "cut_path": "_RNv",
    "length_overrun": v0("_RNv{p}1p99f"),
    "forward_backref": v0("_RNvB9_1f"),
    "self_backref": v0("_RNvB1_1f"),
    "bad_namespace": v0("_RN1{p}1p1f"),
    "trailing_junk": v0("_RNv{p}1p1f!!"),
    "unknown_type": v0("_RINvNt{core}4core3ptr9drop_glueqE"),
    "too_deep": _nest(80),
    "legacy": CRATE_SYMBOL,
    "cxx": "_ZN3foo3barEv",
}
_TRANSPARENT = frozenset({"std", "core", "alloc", "test"})


class TestV0Parser(unittest.TestCase):
    """`v0_facts` / `v0_demangle`: the v0 grammar, from the official spec."""

    def facts(self, name, skip=_TRANSPARENT):
        f = rust_binary.v0_facts(V0[name], skip)
        self.assertIsNotNone(f, name)
        return f

    def test_the_crate_root_is_read_through_the_path(self):
        for name, crate in (("crate_fn", "p"), ("calcx_fn", "calcx"), ("std_fn", "std"),
                            ("closure", "p"), ("vendor_suffix", "std"), ("libtest_fn", "test"),
                            ("seed", "std")):
            with self.subTest(sym=name):
                self.assertEqual(self.facts(name).krate, crate)

    def test_a_back_reference_resolves_from_just_after_the_prefix(self):
        # MUTATION: measuring `B<n>_` from the symbol's first byte (`_R`
        # included) misreads all three and is killed here.
        for name in ("inherent_backref", "trait_impl_backref", "blanket_dsp"):
            with self.subTest(sym=name):
                self.assertEqual(self.facts(name).krate, "p")
        self.assertEqual(rust_binary.v0_demangle(V0["inherent_backref"]), "<p::Dsp>::inh")

    def test_an_impl_is_decided_by_its_self_type_not_the_trait_or_the_impl_path(self):
        self.assertEqual(self.facts("trait_impl_backref").krate, "p")
        self.assertEqual(self.facts("drop_slow_crate").krate, "p")        # Rc<p::Pd>
        self.assertEqual(self.facts("drop_slow_std").krate, "")           # Arc<std Packet<()>>

    def test_a_primitive_or_the_placeholder_names_no_crate(self):
        # R18: `<u8 as p::Blanket>` is not crate p's, though the impl lives in p.
        for name in ("blanket_u8", "blanket_slice", "blanket_placeholder"):
            with self.subTest(sym=name):
                self.assertEqual(self.facts(name).krate, "")

    def test_a_provided_method_is_its_self_types_else_its_traits(self):
        self.assertEqual(self.facts("provided_std").krate, "core")
        self.assertEqual(self.facts("provided_crate_self").krate, "p")
        self.assertEqual(self.facts("provided_crate_trait").krate, "p")

    def test_drop_glue_is_searched_through_every_generic(self):
        for name in ("drop_vec", "drop_tuple", "drop_array", "drop_dyn", "drop_control",
                     "drop_in_place_control"):
            with self.subTest(sym=name):
                f = self.facts(name)
                self.assertEqual((f.krate, f.drop_crate), ("core", "p"))
        for name in ("drop_std", "drop_std_linux", "drop_runner_type"):
            with self.subTest(sym=name):
                self.assertIsNone(self.facts(name).drop_crate)

    def test_identifiers_are_decoded_past_their_length_and_separator(self):
        f = self.facts("begin_short_backtrace")
        self.assertEqual(f.krate, "test")
        self.assertIn("__rust_begin_short_backtrace", f.main_idents)
        self.assertIn("drop_slow", self.facts("drop_slow_std").main_idents)
        self.assertIn("TsnControlEnv", self.facts("drop_control").control_idents)
        self.assertIn("hashmap_random_keys", self.facts("seed").main_idents)
        # a generic argument's identifiers are not the main path's
        self.assertNotIn("Rep", self.facts("assert_test_result").main_idents)

    def test_the_name_tables_keep_legacy_scope(self):
        # Ruling R28. CONTROL: the main path, an M/X self type, drop glue's
        # payload -- and nothing else. SEED: the main path of a std symbol.
        # R34: an M/X self type counts by its OWN path only.
        for name, ident in (("crate_fn", "tsn_control_temp_dir"),
                            ("trait_impl_backref", "TsnControlEnv"),
                            ("inherent_control", "TsnControlEnv"),
                            ("drop_x_trait_backref", "TsnControlEnv"),
                            ("drop_control", "TsnControlEnv"),
                            ("drop_in_place_control", "TsnControlEnv")):
            with self.subTest(sym=name):
                self.assertIn(ident, self.facts(name).control_idents)
        # R39: a crate's own fn or method NAMED like core's drop glue is no
        # drop glue -- no control name from its generic arguments, no payload.
        for name in ("userfn_drop_in_place", "userfn_drop_glue", "method_drop_in_place",
                     "nested_core_drop_in_place"):
            with self.subTest(sym=name):
                f = self.facts(name)
                self.assertNotIn("TsnControlEnv", f.control_idents)
                self.assertEqual((f.krate, f.drop_crate), ("p", None))
        for name in ("std_read_control_type", "provided_control_self", "mx_result_map",
                     "mx_receiver", "mx_iter_x", "mx_ref_self", "blanket_x_control",
                     "drop_lookalike_x"):
            with self.subTest(sym=name):
                self.assertNotIn("TsnControlEnv", self.facts(name).control_idents)
        # R37: nor the fn item's name when `impl<F: Fn> ViaFn for F` runs on it.
        self.assertNotIn("tsn_control_set_env", self.facts("fnitem_x_control").control_idents)
        self.assertEqual(self.facts("mx_result_map").krate, "p")     # R14 still decides
        seed = self.facts("seed")
        self.assertEqual(seed.krate, "std")
        self.assertIn("hashmap_random_keys", seed.main_idents)
        lookalike = self.facts("std_read_seed_type")
        self.assertEqual(lookalike.krate, "std")
        self.assertNotIn("hashmap_random_keys", lookalike.main_idents)
        self.assertNotIn("hashmap_random_keys", lookalike.control_idents)
        named = self.facts("crate_fn_seed_name")
        self.assertEqual(named.krate, "p")                 # its OWN crate: not std's seeding

    def test_a_deep_nested_path_reads_without_recursion(self):
        # Ruling R29: this used to raise RecursionError, and the wrapper --
        # which counts crate symbols with it -- exited 1, read as RED.
        for sym in (V0_DEEP, "_" + V0_DEEP):
            with self.subTest(macho=sym.startswith("__")):
                f = rust_binary.v0_facts(sym, _TRANSPARENT)
                self.assertIsNotNone(f)
                self.assertEqual((f.krate, len(f.main_idents)), ("a", 1200))
                self.assertTrue(rust_binary.v0_demangle(sym).startswith("a::f::f::f"))
        path = os.path.join(tempfile.mkdtemp(prefix="tsn-rust-deep-"), "exe")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), ignore_errors=True)
        with open(path, "wb") as f:
            f.write(elf(interp="/lib64/ld-linux-aarch64.so.1", symbols=[V0_DEEP]))
        self.assertEqual(rust_binary.inspect(path).crate_symbols, 1)

    def test_the_macho_spelling_reads_the_same(self):
        for name, sym in V0.items():
            with self.subTest(sym=name):
                self.assertEqual(rust_binary.v0_facts("_" + sym, _TRANSPARENT),
                                 rust_binary.v0_facts(sym, _TRANSPARENT))

    def test_malformed_and_truncated_symbols_are_none_and_never_raise(self):
        for name, sym in V0_MALFORMED.items():
            with self.subTest(sym=name):
                self.assertIsNone(rust_binary.v0_facts(sym, _TRANSPARENT))
                self.assertIsNone(rust_binary.v0_demangle(sym))
        for name, sym in V0.items():
            for cut in range(len(sym)):
                rust_binary.v0_facts(sym[:cut], _TRANSPARENT)       # must not raise
                rust_binary.v0_demangle(sym[:cut])

    def test_nesting_inside_the_depth_cap_reads_and_past_it_is_none(self):
        self.assertEqual(rust_binary.v0_facts(_nest(30), _TRANSPARENT).drop_crate, "p")
        self.assertIsNone(rust_binary.v0_facts(_nest(80), _TRANSPARENT))

    def test_demangle(self):
        for name, want in (
                ("crate_fn", "p::tsn_control_temp_dir"),
                ("closure", "p::t_thread::{closure#0}"),
                ("trait_impl_backref", "<p::TsnControlEnv as core::ops::drop::Drop>::drop"),
                ("blanket_u8", "<u8 as p::Blanket>::b::{closure#0}"),
                ("drop_vec", "core::ptr::drop_glue<core::option::Option<alloc::vec::Vec<p::Pd>>>"),
                ("drop_tuple", "core::ptr::drop_glue<core::option::Option<(u32, p::Pd)>>"),
                ("drop_array", "core::ptr::drop_glue<[p::Pd; 1]>"),
                ("drop_dyn", "core::ptr::drop_glue<core::option::Option<alloc::boxed::Box<dyn "
                             "p::Ob>>>"),
                ("drop_slow_crate", "<alloc::rc::Rc<p::Pd>>::drop_slow"),
                ("vendor_suffix", "std::io::stdio::OUTPUT_CAPTURE_USED")):
            with self.subTest(sym=name):
                self.assertEqual(rust_binary.v0_demangle(V0[name]), want)
                self.assertEqual(rust_binary.v0_demangle("_" + V0[name]), want)


class TestV0Binaries(BinaryCase):
    """R26: a 1.98 test binary carries only v0 symbols; it must still count."""

    CRATE = [V0["crate_fn"], V0["inherent_backref"], V0["closure"]]
    STD = [V0["std_fn"], V0["seed"], V0["libtest_fn"], V0["drop_std"], V0["vendor_suffix"],
           V0["blanket_u8"]]

    def test_an_elf_with_only_v0_crate_symbols_is_not_refused(self):
        # MUTATION: counting legacy symbols alone reads this 0 ("stripped").
        facts = self.facts(elf(interp="/lib64/ld-linux-aarch64.so.1", symbols=self.CRATE + self.STD))
        self.assertEqual(facts.crate_symbols, 3)
        self.assertIsNone(rust_binary.refusal(facts))

    def test_an_elf_with_only_v0_std_symbols_is_stripped(self):
        facts = self.facts(elf(interp="/lib64/ld-linux-aarch64.so.1", symbols=self.STD))
        self.assertEqual(facts.crate_symbols, 0)
        self.assertEqual(rust_binary.refusal(facts),
                         "stripped: no crate symbols, so no call can be attributed")

    def test_a_macho_with_only_v0_crate_symbols_is_not_refused(self):
        facts = self.facts(macho(symbols=["_" + s for s in self.CRATE + self.STD]))
        self.assertEqual(facts.crate_symbols, 3)
        self.assertIsNone(rust_binary.refusal(facts))

    def test_a_macho_with_only_v0_std_symbols_is_stripped(self):
        facts = self.facts(macho(symbols=["_" + s for s in self.STD]))
        self.assertEqual(facts.crate_symbols, 0)
        self.assertEqual(rust_binary.refusal(facts),
                         "stripped: no crate symbols, so no call can be attributed")

    def test_symbol_names_lists_the_table_and_never_raises(self):
        syms = [CRATE_SYMBOL, V0["crate_fn"]]
        self.assertEqual(rust_binary.symbol_names(self.write(elf(interp="/x", symbols=syms))),
                         syms)
        self.assertEqual(rust_binary.symbol_names(self.write(macho(symbols=syms))), syms)
        self.assertEqual(rust_binary.symbol_names(self.write(b"\x7fELF")), [])
        self.assertEqual(rust_binary.symbol_names(os.path.join(self.root, "absent")), [])


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


# ── Ruling R42: the crate-export list, on synthetic archives and objects ──
#
# Each builder writes only what the reader looks at. ELF sections: 1 .text
# (executable), 2 .data; Mach-O sections: 1 __TEXT,__text (instructions),
# 2 __DATA,__data.

_GLOBAL, _WEAK, _LOCAL = 1, 2, 0
_FUNC, _OBJECT, _NOTYPE = 2, 1, 0


def elf_rel(symbols, e_type=1, klass=2):
    """An ELF64 LE object; `symbols` are (name, bind, type, shndx)."""
    strtab, offs = b"\0", []
    for name, *_ in symbols:
        offs.append(len(strtab))
        strtab += name.encode() + b"\0"
    syms = struct.pack("<IBBHQQ", 0, 0, 0, 0, 0, 0)
    for (_name, bind, kind, shndx), o in zip(symbols, offs):
        syms += struct.pack("<IBBHQQ", o, (bind << 4) | kind, 0, shndx, 0, 4)
    text, data = b"\x1f\x20\x03\xd5" * 4, b"\0" * 8
    shstr = b"\0.text\0.data\0.symtab\0.strtab\0.shstrtab\0"
    blobs, off, at = [text, data, syms, strtab, shstr], 64, []
    for b in blobs:
        at.append(off)
        off += len(b)
    body = b"".join(blobs)
    pad = (-off) % 8
    shoff = off + pad
    sh = [struct.pack("<IIQQQQIIQQ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
          struct.pack("<IIQQQQIIQQ", 1, 1, 6, 0, at[0], len(text), 0, 0, 4, 0),
          struct.pack("<IIQQQQIIQQ", 7, 1, 3, 0, at[1], len(data), 0, 0, 8, 0),
          struct.pack("<IIQQQQIIQQ", 13, 2, 0, 0, at[2], len(syms), 4, 1, 8, 24),
          struct.pack("<IIQQQQIIQQ", 21, 3, 0, 0, at[3], len(strtab), 0, 0, 1, 0),
          struct.pack("<IIQQQQIIQQ", 29, 3, 0, 0, at[4], len(shstr), 0, 0, 1, 0)]
    ident = b"\x7fELF" + bytes([klass, 1, 1, 0]) + b"\0" * 8
    ehdr = ident + struct.pack("<HHIQQQIHHHHHH", e_type, 0xB7, 1, 0, 0, shoff, 0, 64, 0, 0, 64,
                               len(sh), len(sh) - 1)
    return ehdr + body + b"\0" * pad + b"".join(sh)


_N_EXT, _N_SECT, _N_UNDF, _N_STAB_FUN = 0x01, 0x0E, 0x00, 0x24


def macho_obj(symbols, filetype=1):
    """A Mach-O 64 object; `symbols` are (name, n_type, n_sect)."""
    seg = struct.pack("<II16sQQQQiiII", 0x19, 72 + 2 * 80, b"", 0, 0, 0, 0, 7, 7, 2, 0)
    seg += struct.pack("<16s16sQQIIIIIIII", b"__text", b"__TEXT", 0, 16, 0, 2, 0, 0,
                       0x80000400, 0, 0, 0)
    seg += struct.pack("<16s16sQQIIIIIIII", b"__data", b"__DATA", 16, 8, 0, 3, 0, 0, 0, 0, 0, 0)
    strtab, offs = b"\0", []
    for name, *_ in symbols:
        offs.append(len(strtab))
        strtab += name.encode() + b"\0"
    nlist = b"".join(struct.pack("<IBBHQ", o, n_type, n_sect, 0, 0)
                     for (_n, n_type, n_sect), o in zip(symbols, offs))
    head = 32 + len(seg) + 24
    symoff, stroff = head, head + len(nlist)
    symtab = struct.pack("<IIIIII", 0x2, 24, symoff, len(symbols), stroff, len(strtab))
    hdr = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, filetype, 2, len(seg) + 24, 0, 0)
    return hdr + seg + symtab + nlist + strtab


def _ar_header(name_field, size):
    return (name_field.ljust(16) + "0".ljust(12) + "0".ljust(6) + "0".ljust(6) + "644".ljust(8)
            + str(size).ljust(10)).encode("latin-1") + b"`\n"


def _ar_member(name_field, body):
    return _ar_header(name_field, len(body)) + body + (b"\n" if len(body) % 2 else b"")


def gnu_ar(members):
    """A GNU archive: a `/` symbol table, a `//` long-name table for names
    that do not fit `name/` in 16 bytes, then each member."""
    longnames, fields = b"", []
    for name, _body in members:
        if len(name) + 1 <= 16:
            fields.append(name + "/")
        else:
            fields.append("/%d" % len(longnames))
            longnames += name.encode() + b"/\n"
    out = b"!<arch>\n" + _ar_member("/", b"\0\0\0\0")
    if longnames:
        out += _ar_member("//", longnames)
    for field, (_name, body) in zip(fields, members):
        out += _ar_member(field, body)
    return out


def bsd_ar(members):
    """A BSD (darwin) archive: `#1/<len>` names leading each member's data,
    and a `__.SYMDEF SORTED` table first."""
    out = b"!<arch>\n"
    for name, body in [("__.SYMDEF SORTED", b"\0" * 8)] + list(members):
        raw = name.encode()
        raw += b"\0" * ((-len(raw)) % 8)
        out += _ar_member("#1/%d" % len(raw), raw + body)
    return out


LONG_RCGU = "ax-555212d5d7847123.8ahtpu3q62ny3yoa6vxew9po5.1dgocu1.rcgu.o"
LEGACY = "_ZN2ax5inner17h" + "f" * 16 + "E"     # built, so no literal reads as a secret
V0_SYM = "_RNvCs1234abcd_2ax5inner"


def elf_rcgu():
    return elf_rel([("ax_flush", _GLOBAL, _FUNC, 1), ("ax_weak", _WEAK, _FUNC, 1),
                    ("ax_local", _LOCAL, _FUNC, 1), ("ax_asm_label", _GLOBAL, _NOTYPE, 1),
                    ("AX_DATA", _GLOBAL, _OBJECT, 2),
                    ("DW.ref.rust_eh_personality", _WEAK, _OBJECT, 2),
                    ("ax_data_label", _GLOBAL, _NOTYPE, 2), ("memcpy", _GLOBAL, _NOTYPE, 0),
                    (LEGACY, _GLOBAL, _FUNC, 1), (V0_SYM, _GLOBAL, _FUNC, 1)])


def macho_rcgu():
    return macho_obj([("_ax_flush", _N_SECT | _N_EXT, 1), ("_ax_local", _N_SECT, 1),
                      ("_AX_DATA", _N_SECT | _N_EXT, 2), ("_memcpy", _N_UNDF | _N_EXT, 0),
                      ("_ax_stab", _N_STAB_FUN, 1), ("_" + LEGACY, _N_SECT | _N_EXT, 1),
                      ("_" + V0_SYM, _N_SECT | _N_EXT, 1)])


def native_member():
    """A build script's C, bundled into the rlib: never an rcgu member."""
    return elf_rel([("sqlite3_open", _GLOBAL, _FUNC, 1), ("ZSTD_compress", _GLOBAL, _FUNC, 1)])


class TestArchive(unittest.TestCase):
    def test_gnu_members_come_back_in_order_with_their_tables_consumed(self):
        members = [("lib.rmeta", b"meta!"), ("lib.rmeta-link", b"link"), (LONG_RCGU, b"obj"),
                   ("c877a2978823c39d-sqlite3.o", b"native")]
        self.assertEqual(rust_binary.ar_members(gnu_ar(members)), members)

    def test_bsd_members_come_back_with_their_long_names_and_no_symdef(self):
        members = [("lib.rmeta", b"meta!"), (LONG_RCGU, b"object"), ("x.o", b"odd")]
        self.assertEqual(rust_binary.ar_members(bsd_ar(members)), members)

    def test_every_symbol_table_spelling_is_consumed(self):
        for table in ("/", "/SYM64/", "__.SYMDEF", "__.SYMDEF SORTED", "__.SYMDEF_64"):
            with self.subTest(table=table):
                if table.startswith("/"):
                    data = b"!<arch>\n" + _ar_member(table, b"\0\0\0\0")
                else:
                    raw = table.encode() + b"\0" * ((-len(table)) % 8)
                    data = b"!<arch>\n" + _ar_member("#1/%d" % len(raw), raw)
                self.assertEqual(rust_binary.ar_members(data + _ar_member("a.o/", b"x")),
                                 [("a.o", b"x")])

    def test_a_thin_archive_and_a_non_archive_are_refused(self):
        for data in (b"!<thin>\n" + _ar_member("a.o/", b"x"), b"\x7fELF", b""):
            with self.subTest(data=data[:8]):
                with self.assertRaises(rust_binary.ExportListError):
                    rust_binary.ar_members(data)

    def test_a_long_name_with_no_table_is_refused(self):
        with self.assertRaises(rust_binary.ExportListError):
            rust_binary.ar_members(b"!<arch>\n" + _ar_member("/0", b"x"))

    def test_every_truncation_is_refused_never_crashes(self):
        # A prefix either still reads as whole members or raises
        # ExportListError: nothing else may escape the reader.
        for data in (gnu_ar([(LONG_RCGU, elf_rcgu())]), bsd_ar([(LONG_RCGU, macho_rcgu())])):
            for cut in range(len(data)):
                try:
                    rust_binary.ar_members(data[:cut])
                except rust_binary.ExportListError:
                    pass


class TestObjectExports(unittest.TestCase):
    def test_elf_keeps_defined_global_and_weak_text_only(self):
        # Defined FUNCs (global or weak) and an untyped label in .text count;
        # a local, a data object, a label in .data and an undefined import
        # do not. Mangling is the rlib reader's filter, not this one's.
        self.assertEqual(rust_binary.object_exports(elf_rcgu()),
                         ["ax_flush", "ax_weak", "ax_asm_label", LEGACY, V0_SYM])

    def test_macho_keeps_external_section_text_and_drops_the_underscore(self):
        self.assertEqual(rust_binary.object_exports(macho_rcgu()), ["ax_flush", LEGACY, V0_SYM])

    def test_llvm_bitcode_is_refused(self):
        for magic in (b"BC\xc0\xde", b"\xde\xc0\x17\x0b"):
            with self.subTest(magic=magic):
                with self.assertRaisesRegex(rust_binary.ExportListError, "bitcode"):
                    rust_binary.object_exports(magic + b"\0" * 64)

    def test_anything_but_a_64_bit_relocatable_object_is_refused(self):
        for data in (elf_rel([], e_type=2), elf_rel([], klass=1), macho_obj([], filetype=2),
                     b"\xca\xfe\xba\xbe" + b"\0" * 60, b"", b"\x7fELF"):
            with self.subTest(data=data[:8]):
                with self.assertRaises(rust_binary.ExportListError):
                    rust_binary.object_exports(data)

    def test_every_truncation_is_refused_never_crashes(self):
        for data in (elf_rcgu(), macho_rcgu()):
            for cut in range(len(data)):
                try:
                    rust_binary.object_exports(data[:cut])
                except rust_binary.ExportListError:
                    pass


class TestRlibExports(BinaryCase):
    def test_a_gnu_rlib_lists_its_rcgu_members_unmangled_text_alone(self):
        path = self.write(gnu_ar([("lib.rmeta", b"meta"), ("lib.rmeta-link", b"l"),
                                  (LONG_RCGU, elf_rcgu()),
                                  ("c877a2978823c39d-sqlite3.o", native_member())]))
        self.assertEqual(rust_binary.rlib_exports(path), ["ax_asm_label", "ax_flush", "ax_weak"])

    def test_a_bsd_rlib_lists_its_rcgu_members_unmangled_text_alone(self):
        path = self.write(bsd_ar([("lib.rmeta", b"meta"), (LONG_RCGU, macho_rcgu()),
                                  ("44ff4c55aa9e5133-zstd.o", native_member())]))
        self.assertEqual(rust_binary.rlib_exports(path), ["ax_flush"])

    def test_a_native_member_is_never_read_even_as_bitcode(self):
        # Bundled C is not the crate's code, whatever it is compiled to.
        path = self.write(gnu_ar([("abc-zlib.o", b"BC\xc0\xde" + b"\0" * 32),
                                  ("abc-sqlite3.o", native_member())]))
        self.assertEqual(rust_binary.rlib_exports(path), [])

    def test_a_bitcode_rcgu_member_refuses_and_names_the_rlib_and_member(self):
        path = self.write(gnu_ar([(LONG_RCGU, b"BC\xc0\xde" + b"\0" * 32)]))
        with self.assertRaises(rust_binary.ExportListError) as ctx:
            rust_binary.rlib_exports(path)
        self.assertIn(path, str(ctx.exception))
        self.assertIn(LONG_RCGU, str(ctx.exception))
        self.assertIn("bitcode", str(ctx.exception))

    def test_an_rlib_with_no_unmangled_fn_is_an_empty_list(self):
        only_mangled = elf_rel([(LEGACY, _GLOBAL, _FUNC, 1), (V0_SYM, _GLOBAL, _FUNC, 1)])
        path = self.write(gnu_ar([("lib.rmeta", b"m"), (LONG_RCGU, only_mangled)]))
        self.assertEqual(rust_binary.rlib_exports(path), [])

    def test_an_unreadable_or_malformed_rlib_refuses(self):
        with self.assertRaises(rust_binary.ExportListError):
            rust_binary.rlib_exports(os.path.join(self.root, "missing.rlib"))
        with self.assertRaises(rust_binary.ExportListError):
            rust_binary.rlib_exports(self.write(gnu_ar([(LONG_RCGU, b"not an object")])))


RUSTC = shutil.which("rustc")
_RLIB_SRC = r'''#[no_mangle] pub extern "C" fn rr_export(x: u32) -> u32 { x + 1 }
#[export_name = "rr_named"] pub extern "C" fn named(x: u32) -> u32 { x + 2 }
pub fn mangled(x: u32) -> u32 { x + 3 }
#[no_mangle] pub static RR_DATA: u32 = 7;
'''


class TestRealRlibs(unittest.TestCase):
    """rustc's own rlibs: BSD archives of Mach-O objects on darwin, GNU
    archives of ELF objects on linux. Skips visibly without rustc."""

    def setUp(self):
        if not RUSTC:
            self.skipTest("no `rustc` on PATH: real rlibs are NOT read on this machine.")
        self.tmp = tempfile.mkdtemp(prefix="tsn-rlib-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        with open(os.path.join(self.tmp, "rr.rs"), "w", encoding="utf-8") as f:
            f.write(_RLIB_SRC)

    def rlib(self, *flags):
        out = os.path.join(self.tmp, "librr%d.rlib" % len(flags))
        proc = subprocess.run([RUSTC, "--edition", "2021", "--crate-type", "rlib", "--crate-name",
                               "rr", *flags, "-o", out, os.path.join(self.tmp, "rr.rs")],
                              capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise AssertionError("rustc failed:\n" + proc.stderr)
        return out

    def test_an_rlib_lists_its_no_mangle_and_export_name_fns_only(self):
        self.assertEqual(rust_binary.rlib_exports(self.rlib()), ["rr_export", "rr_named"])

    def test_a_linker_plugin_lto_rlib_is_refused(self):
        with self.assertRaisesRegex(rust_binary.ExportListError, "bitcode"):
            rust_binary.rlib_exports(self.rlib("-C", "linker-plugin-lto"))


if __name__ == "__main__":
    unittest.main()
