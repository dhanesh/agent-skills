// test-safety-net's Rust I/O guard: a libc interposer, written in Rust and
// built by the analysed repo's own `rustc` (io_guard_rust.py `hook_library`):
//
//   rustc --edition 2021 --crate-type cdylib -C panic=abort \
//         -C force-unwind-tables=yes -O io_guard_rust_hook.rs
//
// No cargo, no crates. It is preloaded under a compiled test binary
// (DYLD_INSERT_LIBRARIES via `__DATA,__interpose` on macOS, LD_PRELOAD via
// `dlsym(RTLD_NEXT)` on Linux), walks `backtrace()` at every intercepted call
// to find which function made it, and `_exit(3)`s on a violation. The
// decision rule and the environment contract are documented in
// io_guard_rust.py's module docstring; the name tables below -- including
// each intercept's group -- are rendered from that file and must not be
// edited here.
//
// Constraints this file obeys, and why:
// - no `thread_local!`, no allocation, no std I/O, no fmt, no panics: the hook
//   runs inside libc (getenv during libSystem init, malloc paths), before or
//   beneath any runtime this image might want. Re-entrancy uses a pthread key
//   made in a constructor; readiness is a plain static atomic (no TLS). The
//   configuration is copied out of the environment ONCE, in the constructor,
//   while the process is still single-threaded.
// - C-variadic functions cannot be DEFINED on stable Rust (c_variadic is
//   unstable), only declared/called. `open`/`openat` are variadic, so the
//   replacements read `mode` where the platform ABI puts a variadic argument
//   (see my_open).
#![allow(non_camel_case_types, clippy::missing_safety_doc)]

use core::ffi::{c_char, c_int, c_uint, c_void, CStr};
#[cfg(target_os = "linux")]
use core::ffi::c_long;
use core::sync::atomic::{AtomicBool, Ordering};

type size_t = usize;
type ssize_t = isize;

#[cfg(target_os = "macos")]
type PthreadKey = usize;
#[cfg(target_os = "linux")]
type PthreadKey = c_uint;

#[repr(C)]
struct DlInfo {
    dli_fname: *const c_char,
    dli_fbase: *mut c_void,
    dli_sname: *const c_char,
    dli_saddr: *mut c_void,
}

impl DlInfo {
    fn empty() -> DlInfo {
        DlInfo { dli_fname: core::ptr::null(), dli_fbase: core::ptr::null_mut(), dli_sname: core::ptr::null(), dli_saddr: core::ptr::null_mut() }
    }
}

extern "C" {
    fn write(fd: c_int, buf: *const c_void, n: size_t) -> ssize_t;
    fn _exit(code: c_int) -> !;
    fn backtrace(buf: *mut *mut c_void, size: c_int) -> c_int;
    fn dladdr(addr: *const c_void, info: *mut DlInfo) -> c_int;
    fn pthread_key_create(key: *mut PthreadKey, dtor: Option<unsafe extern "C" fn(*mut c_void)>) -> c_int;
    fn pthread_getspecific(key: PthreadKey) -> *mut c_void;
    fn pthread_setspecific(key: PthreadKey, v: *const c_void) -> c_int;
}

#[cfg(target_os = "macos")]
extern "C" {
    fn pthread_main_np() -> c_int;
}
#[cfg(target_os = "linux")]
extern "C" {
    fn getpid() -> c_int;
    fn syscall(num: c_long, ...) -> c_long;
}

// Is the calling thread the process's main thread? By IDENTITY, never by
// frame names (ruling R15b): glibc's TLS-destructor runner leaves no marker
// frame, and libSystem's own pthread entry symbol is `thread_start`, which a
// name test would match for every thread. Neither call allocates.
#[cfg(target_os = "macos")]
unsafe fn is_main_thread() -> bool {
    pthread_main_np() != 0
}
#[cfg(target_os = "linux")]
unsafe fn is_main_thread() -> bool {
    // SYS_gettid: 178 on aarch64, 186 on x86_64 -- the two arches this guard
    // is built for. The main thread's tid equals the process's pid.
    #[cfg(target_arch = "aarch64")]
    const SYS_GETTID: c_long = 178;
    #[cfg(target_arch = "x86_64")]
    const SYS_GETTID: c_long = 186;
    syscall(SYS_GETTID) as c_int == getpid()
}

static READY: AtomicBool = AtomicBool::new(false);
static mut KEY: PthreadKey = 0;

// The configuration, read once by the constructor with the REAL getenv.
// ARMED is "TSN_BLOCKED is present"; without it every hook returns at once.
static ARMED: AtomicBool = AtomicBool::new(false);
static STDIN: AtomicBool = AtomicBool::new(false);
// A TSN_BLOCKED too long to copy blocks EVERY group: fail closed, never open.
static BLOCK_ALL: AtomicBool = AtomicBool::new(false);
const BLOCKED_CAP: usize = 512;
static mut BLOCKED: [u8; BLOCKED_CAP] = [0; BLOCKED_CAP];
static mut BLOCKED_LEN: usize = 0;
const TIER_CAP: usize = 8;
static mut TIER: [u8; TIER_CAP] = [0; TIER_CAP];
static mut TIER_LEN: usize = 0;
// The hook's own image: its frames are skipped by address, not by count, so
// inlining can never hand the attribution to the hook itself.
static mut SELF_BASE: *mut c_void = core::ptr::null_mut();

// @@TSN-TABLES-BEGIN@@
// Rendered by io_guard_rust.py render_tables(); edit the Python tables, not this block.
static TRANSPARENT: &[&[u8]] = &[b"std", b"core", b"alloc", b"panic_unwind", b"backtrace", b"hashbrown", b"std_detect"];
static RUNNER: &[&[u8]] = &[b"test"];
static SEED: &[&[u8]] = &[b"hashmap_random_keys"];
static TEST_BODY_BOUNDARY: &[&[u8]] = &[b"__rust_begin_short_backtrace", b"assert_test_result"];
static CONTROL: &[(&[u8], &[u8])] = &[(b"TsnControlEnv", b"environment"), (b"tsn_control_set_env", b"environment"), (b"tsn_control_temp_dir", b"filesystem")];
static SYSTEM_INTERNAL: &[&[u8]] = &[b"libsystem_malloc.dylib"];
#[cfg(target_os = "macos")]
static INTERCEPT: &[(&[u8], &[u8])] = &[(b"clock_gettime", b"clock"), (b"gettimeofday", b"clock"), (b"mach_absolute_time", b"clock"), (b"clock_gettime_nsec_np", b"clock"), (b"getenv", b"environment"), (b"setenv", b"environment"), (b"unsetenv", b"environment"), (b"getcwd", b"environment"), (b"chdir", b"environment"), (b"open", b"filesystem"), (b"openat", b"filesystem"), (b"stat", b"filesystem"), (b"lstat", b"filesystem"), (b"fstatat", b"filesystem"), (b"access", b"filesystem"), (b"mkdir", b"filesystem"), (b"unlink", b"filesystem"), (b"rename", b"filesystem"), (b"opendir", b"filesystem"), (b"readlink", b"filesystem"), (b"rmdir", b"filesystem"), (b"chmod", b"filesystem"), (b"fchmodat", b"filesystem"), (b"symlink", b"filesystem"), (b"realpath", b"filesystem"), (b"socket", b"network"), (b"connect", b"network"), (b"bind", b"network"), (b"getaddrinfo", b"network"), (b"getentropy", b"randomness"), (b"arc4random_buf", b"randomness"), (b"read", b"stdin"), (b"posix_spawn", b"subprocess"), (b"posix_spawnp", b"subprocess"), (b"fork", b"subprocess"), (b"execve", b"subprocess")];
#[cfg(target_os = "linux")]
static INTERCEPT: &[(&[u8], &[u8])] = &[(b"clock_gettime", b"clock"), (b"gettimeofday", b"clock"), (b"getenv", b"environment"), (b"setenv", b"environment"), (b"unsetenv", b"environment"), (b"getcwd", b"environment"), (b"chdir", b"environment"), (b"open", b"filesystem"), (b"openat", b"filesystem"), (b"stat", b"filesystem"), (b"lstat", b"filesystem"), (b"fstatat", b"filesystem"), (b"access", b"filesystem"), (b"mkdir", b"filesystem"), (b"unlink", b"filesystem"), (b"rename", b"filesystem"), (b"opendir", b"filesystem"), (b"readlink", b"filesystem"), (b"rmdir", b"filesystem"), (b"chmod", b"filesystem"), (b"fchmodat", b"filesystem"), (b"symlink", b"filesystem"), (b"realpath", b"filesystem"), (b"open64", b"filesystem"), (b"openat64", b"filesystem"), (b"stat64", b"filesystem"), (b"lstat64", b"filesystem"), (b"fstatat64", b"filesystem"), (b"statx", b"filesystem"), (b"socket", b"network"), (b"connect", b"network"), (b"bind", b"network"), (b"getaddrinfo", b"network"), (b"getrandom", b"randomness"), (b"getentropy", b"randomness"), (b"arc4random_buf", b"randomness"), (b"read", b"stdin"), (b"posix_spawn", b"subprocess"), (b"posix_spawnp", b"subprocess"), (b"fork", b"subprocess"), (b"execve", b"subprocess")];
// @@TSN-TABLES-END@@

/// Copies the value of `name` into `buf`; `None` when unset, else its full
/// length (which may exceed `cap`: the caller decides what that means).
unsafe fn copy_env(name: &CStr, buf: *mut u8, cap: usize) -> Option<usize> {
    let p = real_getenv(name.as_ptr());
    if p.is_null() {
        return None;
    }
    let v = CStr::from_ptr(p).to_bytes();
    core::ptr::copy_nonoverlapping(v.as_ptr(), buf, v.len().min(cap));
    Some(v.len())
}

extern "C" fn tsn_init() {
    unsafe {
        pthread_key_create(&raw mut KEY, None);
        let mut info = DlInfo::empty();
        if dladdr(tsn_init as *const c_void, &mut info) != 0 {
            SELF_BASE = info.dli_fbase;
        }
        if let Some(n) = copy_env(c"TSN_BLOCKED", (&raw mut BLOCKED) as *mut u8, BLOCKED_CAP) {
            if n > BLOCKED_CAP {
                BLOCK_ALL.store(true, Ordering::Relaxed);
            } else {
                BLOCKED_LEN = n;
            }
            TIER_LEN = copy_env(c"TSN_TIER", (&raw mut TIER) as *mut u8, TIER_CAP).map(|n| n.min(TIER_CAP)).unwrap_or(0);
            let s = real_getenv(c"TSN_STDIN".as_ptr());
            STDIN.store(!s.is_null() && CStr::from_ptr(s).to_bytes() == b"1", Ordering::Relaxed);
            // Warm the unwinder (glibc's backtrace dlopens libgcc_s on first
            // use) and read the .symtab now, single-threaded, rather than
            // racing to do either inside the first intercepted call.
            let mut pcs = [core::ptr::null_mut::<c_void>(); 4];
            backtrace(pcs.as_mut_ptr(), 4);
            #[cfg(target_os = "linux")]
            symtab::load();
            ARMED.store(true, Ordering::Release);
            out(b"tsn-hook: armed\n");
        }
    }
    READY.store(true, Ordering::Release);
}

#[cfg(target_os = "macos")]
#[used]
#[link_section = "__DATA,__mod_init_func"]
static INIT: extern "C" fn() = tsn_init;
#[cfg(target_os = "linux")]
#[used]
#[link_section = ".init_array"]
static INIT: extern "C" fn() = tsn_init;

fn out(b: &[u8]) {
    unsafe { write(2, b.as_ptr() as *const c_void, b.len()) };
}

fn contains(hay: &[u8], needle: &[u8]) -> bool {
    !needle.is_empty() && hay.windows(needle.len()).any(|w| w == needle)
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() || hay.len() < needle.len() {
        return None;
    }
    hay.windows(needle.len()).position(|w| w == needle)
}

fn transparent(k: &[u8]) -> bool {
    k.is_empty() || TRANSPARENT.iter().any(|t| *t == k)
}

/// A generic type parameter (`T`, `R`, `F`, `K`...): all ASCII uppercase
/// letters. It names no crate, so a blanket impl over it is transparent.
fn generic_param(seg: &[u8]) -> bool {
    !seg.is_empty() && seg.iter().all(|c| c.is_ascii_uppercase())
}

/// Bytes of the markers that can lead a mangled type: `&` $RF$, `*` $BP$,
/// `mut ` mut$u20$, `const ` const$u20$, a space $u20$.
fn skip_markers(t: &[u8]) -> usize {
    let mut i = 0;
    loop {
        let r = &t[i..];
        if r.starts_with(b"$RF$") || r.starts_with(b"$BP$") {
            i += 4;
        } else if r.starts_with(b"mut$u20$") {
            i += 8;
        } else if r.starts_with(b"const$u20$") {
            i += 10;
        } else if r.starts_with(b"$u20$") {
            i += 5;
        } else {
            return i;
        }
    }
}

/// The deciding crate of the mangled TYPE starting at `t`, or `None` when it
/// names no deciding crate: an empty segment, a generic parameter, or a
/// tuple none of whose elements lies in a non-transparent crate. A tuple
/// `(A, B)` mangles `$LP$A$C$$u20$B$RP$`; its first element in a
/// non-transparent crate decides (ruling R17b). Otherwise the crate is the
/// type path's first `..`-separated segment, ending at `..`, `$` (` as `,
/// `>`, or the type's own generic `<`) or the end.
fn type_crate(t: &[u8]) -> Option<&[u8]> {
    let mut i = skip_markers(t);
    if t[i..].starts_with(b"$LP$") {
        i += 4;
        let mut depth = 0usize;
        let mut elem = true;
        while i < t.len() {
            if elem {
                elem = false;
                if let Some(k) = type_crate(&t[i..]) {
                    if !transparent(k) {
                        return Some(k);
                    }
                }
            }
            let r = &t[i..];
            if r.starts_with(b"$LT$") || r.starts_with(b"$LP$") {
                depth += 1;
                i += 4;
            } else if r.starts_with(b"$GT$") || r.starts_with(b"$RP$") {
                if depth == 0 {
                    return None; // the tuple closed with no deciding element
                }
                depth -= 1;
                i += 4;
            } else if depth == 0 && r.starts_with(b"$C$") {
                i += 3;
                elem = true;
            } else {
                i += 1;
            }
        }
        return None;
    }
    let start = i;
    while i < t.len() && t[i] != b'$' && !(t[i] == b'.' && i + 1 < t.len() && t[i + 1] == b'.') {
        i += 1;
    }
    let seg = &t[start..i];
    if seg.is_empty() || generic_param(seg) {
        None
    } else {
        Some(seg)
    }
}

/// The deciding crate of a mangled first path component `seg`. A plain path
/// component IS the crate (`_ZN`'s first component is `<len>crate`). An impl
/// component (`_$LT$...$GT$`, from `<T as Trait>::m` or `<T>::m`) is decided
/// by the crate of the SELF TYPE T (ruling R14); a generic-parameter T names
/// no crate and reads as transparent.
fn seg_crate(seg: &[u8]) -> &[u8] {
    let i = if seg.first() == Some(&b'_') { 1 } else { 0 };
    if !seg[i..].starts_with(b"$LT$") {
        return seg; // a plain path component: the crate itself
    }
    type_crate(&seg[i + 4..]).unwrap_or(&[])
}

/// The deciding crate of a legacy-mangled Rust symbol, or `None` when `s` is
/// not one (no `17h<16 hex>E` hash: a C or C++ symbol). A
/// `core::ptr::drop_in_place<T>` frame is decided by T's crate (ruling
/// R17b): libtest dropping a crate's panic payload runs that crate's Drop.
fn crate_of(s: &[u8]) -> Option<&[u8]> {
    let n = s.len();
    if n < 20 || s[n - 1] != b'E' || &s[n - 20..n - 17] != b"17h" {
        return None;
    }
    if !s[n - 17..n - 1].iter().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    let mut i = 0;
    while i < n && s[i] == b'_' {
        i += 1;
    }
    if i + 1 >= n || s[i] != b'Z' || s[i + 1] != b'N' {
        return None;
    }
    i += 2;
    let mut len = 0usize;
    while i < n && s[i].is_ascii_digit() {
        len = len * 10 + (s[i] - b'0') as usize;
        i += 1;
    }
    if len == 0 || i + len > n {
        return None;
    }
    let mut krate = seg_crate(&s[i..i + len]);
    if transparent(krate) {
        const DIP: &[u8] = b"drop_in_place$LT$";
        if let Some(p) = find(s, DIP) {
            if let Some(k) = type_crate(&s[p + DIP.len()..]) {
                krate = k;
            }
        }
    }
    Some(krate)
}

// 0 transparent, 1 crate code, 2 libtest runner, 3 std seeding its HashMap.
fn classify(s: &[u8]) -> u8 {
    if SEED.iter().any(|m| contains(s, m)) {
        return 3;
    }
    match crate_of(s) {
        None => 0,
        Some(k) if transparent(k) => 0,
        Some(k) if RUNNER.iter().any(|t| *t == k) => 2,
        Some(_) => 1,
    }
}

/// A body-side boundary (rulings R13, R17a): a TEST_BODY_BOUNDARY frame in
/// libtest's own `test` crate. std's `__rust_begin_short_backtrace` (thread
/// spawn, lang_start) is not one.
fn test_boundary(s: &[u8]) -> bool {
    match crate_of(s) {
        Some(k) if RUNNER.iter().any(|t| *t == k) => TEST_BODY_BOUNDARY.iter().any(|m| contains(s, m)),
        _ => false,
    }
}

/// Does the mangled symbol `s` name the item `name`? Either as a legacy path
/// segment (`<len><name>`, as in `_ZN1p20tsn_control_temp_dir17h…E`) or as a
/// path inside a generic or an impl (`..TsnControlEnv$u20$as…`,
/// `drop_in_place$LT$p..TsnControlEnv$GT$`).
fn names(s: &[u8], name: &[u8]) -> bool {
    let (n, m) = (s.len(), name.len());
    if m == 0 || n < m {
        return false;
    }
    let mut i = 0;
    while i + m <= n {
        if &s[i..i + m] == name {
            let (mut j, mut len, mut mul) = (i, 0usize, 1usize);
            while j > 0 && i - j < 4 && s[j - 1].is_ascii_digit() {
                len += (s[j - 1] - b'0') as usize * mul;
                mul *= 10;
                j -= 1;
            }
            if j < i && len == m {
                return true;
            }
            let before = (i >= 2 && &s[i - 2..i] == b"..") || (i >= 1 && s[i - 1] == b'$');
            let after = i + m == n || s[i + m] == b'$' || s[i + m] == b'.';
            if before && after {
                return true;
            }
        }
        i += 1;
    }
    false
}

fn control(s: &[u8]) -> Option<&'static [u8]> {
    CONTROL.iter().find(|(name, _)| names(s, name)).map(|&(_, group)| group)
}

/// Ruling R11: is `fname` (dladdr's `dli_fname`) an image in SYSTEM_INTERNAL,
/// by basename? Allocator bookkeeping is not I/O a unit chose: macOS 27's
/// xzone malloc reads `mach_absolute_time` building a thread's cache, under
/// std's thread plumbing and under any allocating crate function. The list
/// names the images whose own internal calls are infrastructure; adding one
/// needs a measured reason. Inert on linux, where glibc's malloc makes no
/// interposable calls.
unsafe fn system_internal(fname: *const c_char) -> bool {
    if fname.is_null() {
        return false;
    }
    let path = CStr::from_ptr(fname).to_bytes();
    let base = match path.iter().rposition(|&c| c == b'/') {
        Some(i) => &path[i + 1..],
        None => path,
    };
    SYSTEM_INTERNAL.iter().any(|&img| img == base)
}

unsafe fn is_blocked(group: &[u8]) -> bool {
    if group == b"stdin" {
        return STDIN.load(Ordering::Relaxed);
    }
    if BLOCK_ALL.load(Ordering::Relaxed) {
        return true;
    }
    let list = core::slice::from_raw_parts((&raw const BLOCKED) as *const u8, BLOCKED_LEN);
    list.split(|&c| c == b',').any(|g| g == group)
}

unsafe fn judge(group: &[u8], what: &[u8], who: &[u8]) {
    if !is_blocked(group) {
        return;
    }
    let tier = core::slice::from_raw_parts((&raw const TIER) as *const u8, TIER_LEN);
    // The leading newline: libtest has printed `test <name> ... ` without
    // one, and the wrapper reads a line that STARTS with IOGuardViolation.
    out(b"\nIOGuardViolation: a tier ");
    out(if tier.is_empty() { b"?" } else { tier });
    out(b" candidate reached ");
    out(group);
    out(b" I/O via ");
    out(what);
    out(b" from ");
    out(who);
    out(b"\n");
    _exit(3);
}

/// Ruling R1: a walk the hook cannot read is never a pass.
fn cannot(why: &[u8]) -> ! {
    out(b"\ntsn-hook: cannot attribute (");
    out(why);
    out(b")\n");
    unsafe { _exit(2) }
}

#[cfg(target_os = "linux")]
mod symtab {
    use super::*;
    extern "C" {
        fn dlsym(h: *mut c_void, s: *const c_char) -> *mut c_void;
        fn mmap(a: *mut c_void, l: size_t, p: c_int, f: c_int, fd: c_int, o: i64) -> *mut c_void;
        fn lseek(fd: c_int, o: i64, w: c_int) -> i64;
        fn close(fd: c_int) -> c_int;
        fn dl_iterate_phdr(cb: unsafe extern "C" fn(*mut c_void, size_t, *mut c_void) -> c_int, d: *mut c_void) -> c_int;
    }
    static mut BASE: u64 = 0;
    static mut MAP: *const u8 = core::ptr::null();
    static mut SYMS: (usize, usize, usize) = (0, 0, 0); // (sym off, count, str off)
    pub static FOUND: AtomicBool = AtomicBool::new(false);

    unsafe extern "C" fn base_cb(info: *mut c_void, _: size_t, _: *mut c_void) -> c_int {
        BASE = *(info as *const u64); // dl_phdr_info.dlpi_addr is the first field
        1
    }
    unsafe fn rd<T: Copy>(off: usize) -> T {
        core::ptr::read_unaligned(MAP.add(off) as *const T)
    }
    // Called once, by the constructor, when armed.
    pub unsafe fn load() {
        dl_iterate_phdr(base_cb, core::ptr::null_mut());
        let ropen: unsafe extern "C" fn(*const c_char, c_int, ...) -> c_int =
            core::mem::transmute(dlsym(-1isize as *mut c_void, c"open".as_ptr()));
        let fd = ropen(c"/proc/self/exe".as_ptr(), 0);
        if fd < 0 {
            return;
        }
        let size = lseek(fd, 0, 2);
        let m = mmap(core::ptr::null_mut(), size as usize, 1, 2, fd, 0); // PROT_READ, MAP_PRIVATE
        close(fd);
        if m as isize == -1 {
            return;
        }
        MAP = m as *const u8;
        let shoff: u64 = rd(0x28);
        let shnum: u16 = rd(0x3C);
        for k in 0..shnum as usize {
            let sh = shoff as usize + k * 64;
            if rd::<u32>(sh + 4) == 2 {
                // SHT_SYMTAB
                let link: u32 = rd(sh + 40);
                let str_sh = shoff as usize + link as usize * 64;
                SYMS = (rd::<u64>(sh + 24) as usize, rd::<u64>(sh + 32) as usize / 24, rd::<u64>(str_sh + 24) as usize);
                FOUND.store(true, Ordering::Relaxed);
            }
        }
    }
    pub unsafe fn lookup(pc: *mut c_void) -> Option<&'static [u8]> {
        if !FOUND.load(Ordering::Relaxed) {
            return None;
        }
        let (so, n, stro) = SYMS;
        let a = pc as u64;
        for j in 0..n {
            let e = so + j * 24;
            let info: u8 = rd(e + 4);
            let val: u64 = rd(e + 8);
            if info & 0xf != 2 || val == 0 {
                continue;
            }
            let lo = BASE + val;
            let size: u64 = rd(e + 16);
            if a >= lo && a < lo + size.max(1) {
                let name: u32 = rd(e);
                return Some(CStr::from_ptr(MAP.add(stro + name as usize) as *const c_char).to_bytes());
            }
        }
        None
    }
}

const CAP: usize = 1024;

unsafe fn decide(what: &'static [u8]) {
    // Each intercept's group comes from the rendered INTERCEPT table, never
    // from a literal beside the hook. A hooked name with no row fails closed.
    let group = match INTERCEPT.iter().find(|&&(name, _)| name == what) {
        Some(&(_, g)) => g,
        None => cannot(b"an intercept has no group in the rendered table"),
    };
    // 1024 frames, a stack array, no allocation (ruling R15a). A walk that
    // fills the buffer without deciding is judged below, not exempted.
    let mut pcs = [core::ptr::null_mut::<c_void>(); CAP];
    let n = backtrace(pcs.as_mut_ptr(), CAP as c_int) as usize;
    if n < 3 {
        cannot(b"backtrace() returned fewer than 3 frames");
    }
    let base = SELF_BASE;
    if base.is_null() {
        cannot(b"the hook could not locate its own image");
    }
    #[cfg(target_os = "linux")]
    if !symtab::FOUND.load(Ordering::Relaxed) {
        cannot(b"no .symtab in /proc/self/exe");
    }
    let mut resolved = false;
    let mut first = true;
    for &pc in pcs.iter().take(n) {
        let mut info = DlInfo::empty();
        let found = dladdr(pc, &mut info) != 0;
        if found && info.dli_fbase == base {
            continue;
        }
        // The call's immediate caller: a system-internal image's own work is
        // infrastructure, exempt (R11).
        if first {
            first = false;
            if found && system_internal(info.dli_fname) {
                return;
            }
        }
        let mut s: Option<&[u8]> = None;
        if found && !info.dli_sname.is_null() {
            s = Some(CStr::from_ptr(info.dli_sname).to_bytes());
        }
        #[cfg(target_os = "linux")]
        if s.map(|x| classify(x) == 0 && !x.ends_with(b"E")).unwrap_or(true) {
            if let Some(t) = symtab::lookup(pc) {
                s = Some(t);
            }
        }
        let s = match s {
            Some(s) => s,
            None => continue,
        };
        resolved = true;
        // A control helper decides, with the group it stands for.
        if let Some(g) = control(s) {
            judge(g, what, s);
            return;
        }
        // Rulings R13 and R17a: libtest calls back into the test's own code
        // through `test::__rust_begin_short_backtrace` (the test body) and
        // `test::assert_test_result<T>` (its `Termination::report`).
        // Reaching one before any crate frame (a crate frame would have
        // decided and returned already) means that code was inlined into
        // libtest's generic -- the optimized body under call_once, an
        // `#[inline(always)]` report. Checked BEFORE the runner exemption,
        // because both frames mangle into the `test` crate, which classify()
        // reads as runner work. libtest's own bookkeeping never runs under
        // either frame.
        if test_boundary(s) {
            judge(group, what, b"(test body, inlined)");
            return;
        }
        match classify(s) {
            1 => {
                judge(group, what, s);
                return;
            }
            2 | 3 => return,
            _ => {}
        }
    }
    // Ruling R15a: the buffer filled and nothing decided -- a stack too deep
    // to attribute. Fail closed, as go does past its cap.
    if n == CAP {
        judge(group, what, b"(a stack deeper than 1024 frames)");
    }
    if !resolved {
        cannot(b"no frame resolved to a symbol");
    }
    // Ruling R15b: no crate frame on this stack. Off the main thread it is a
    // spawned thread or a thread-local destructor running std-only code, and
    // it is JUDGED. On the main thread it is pre-`main` libc/dyld init and
    // stays exempt.
    if !is_main_thread() {
        judge(group, what, b"(a thread with no crate frame)");
    }
}

fn guard(what: &'static [u8]) {
    if !READY.load(Ordering::Acquire) || !ARMED.load(Ordering::Acquire) {
        return;
    }
    unsafe {
        if !pthread_getspecific(KEY).is_null() {
            return;
        }
        pthread_setspecific(KEY, 1 as *const c_void);
        decide(what);
        pthread_setspecific(KEY, core::ptr::null());
    }
}

// ---------------- macOS: __DATA,__interpose ----------------
#[cfg(target_os = "macos")]
mod plat {
    use super::*;
    #[repr(C)]
    pub struct Interpose {
        new: *const c_void,
        old: *const c_void,
    }
    unsafe impl Sync for Interpose {}
    extern "C" {
        // Inside the interposing image these bind to the REAL functions.
        pub fn getenv(n: *const c_char) -> *mut c_char;
        fn open(p: *const c_char, f: c_int, ...) -> c_int;
        fn openat(d: c_int, p: *const c_char, f: c_int, ...) -> c_int;
        fn read(fd: c_int, b: *mut c_void, n: size_t) -> ssize_t;
    }
    unsafe extern "C" fn my_getenv(n: *const c_char) -> *mut c_char {
        guard(b"getenv");
        getenv(n)
    }
    // Apple arm64 passes EVERY variadic argument on the stack. A non-variadic
    // function reads its 9th integer argument from the first stack slot, which
    // is exactly where the caller put `mode`. (x86_64 macOS would read rdx.)
    #[cfg(target_arch = "aarch64")]
    unsafe extern "C" fn my_open(p: *const c_char, f: c_int, _2: u64, _3: u64, _4: u64, _5: u64, _6: u64, _7: u64, mode: u64) -> c_int {
        guard(b"open");
        open(p, f, mode as c_uint)
    }
    #[cfg(target_arch = "x86_64")]
    unsafe extern "C" fn my_open(p: *const c_char, f: c_int, mode: c_uint) -> c_int {
        guard(b"open");
        open(p, f, mode)
    }
    #[cfg(target_arch = "aarch64")]
    unsafe extern "C" fn my_openat(d: c_int, p: *const c_char, f: c_int, _3: u64, _4: u64, _5: u64, _6: u64, _7: u64, mode: u64) -> c_int {
        guard(b"openat");
        openat(d, p, f, mode as c_uint)
    }
    #[cfg(target_arch = "x86_64")]
    unsafe extern "C" fn my_openat(d: c_int, p: *const c_char, f: c_int, mode: c_uint) -> c_int {
        guard(b"openat");
        openat(d, p, f, mode)
    }
    unsafe extern "C" fn my_read(fd: c_int, b: *mut c_void, n: size_t) -> ssize_t {
        if fd == 0 {
            guard(b"read");
        }
        read(fd, b, n)
    }
    macro_rules! interpose {
        ($($id:ident: $new:ident => $old:ident),* $(,)?) => { $(
            #[used]
            #[link_section = "__DATA,__interpose"]
            static $id: Interpose = Interpose { new: $new as *const c_void, old: $old as *const c_void };
        )* };
    }
    interpose! {
        I_GETENV: my_getenv => getenv,
        I_OPEN: my_open => open,
        I_OPENAT: my_openat => openat,
        I_READ: my_read => read,
    }
    // One fixed-argument intercept: the real function, its replacement and
    // its interpose entry. `inode64` names the x86_64 `$INODE64` spelling the
    // SDK's headers select for the stat family.
    macro_rules! hook {
        ($name:ident, $my:ident, $slot:ident, $(inode64 = $ln:literal,)? ($($a:ident: $t:ty),*) -> $r:ty) => {
            extern "C" {
                $(#[cfg_attr(target_arch = "x86_64", link_name = $ln)])?
                fn $name($($a: $t),*) -> $r;
            }
            unsafe extern "C" fn $my($($a: $t),*) -> $r {
                guard(stringify!($name).as_bytes());
                $name($($a),*)
            }
            #[used]
            #[link_section = "__DATA,__interpose"]
            static $slot: Interpose = Interpose { new: $my as *const c_void, old: $name as *const c_void };
        };
    }
    hook!(stat, my_stat, I_STAT, inode64 = "stat$INODE64", (p: *const c_char, b: *mut c_void) -> c_int);
    hook!(lstat, my_lstat, I_LSTAT, inode64 = "lstat$INODE64", (p: *const c_char, b: *mut c_void) -> c_int);
    hook!(fstatat, my_fstatat, I_FSTATAT, inode64 = "fstatat$INODE64", (d: c_int, p: *const c_char, b: *mut c_void, f: c_int) -> c_int);
    hook!(access, my_access, I_ACCESS, (p: *const c_char, m: c_int) -> c_int);
    hook!(mkdir, my_mkdir, I_MKDIR, (p: *const c_char, m: c_uint) -> c_int);
    hook!(unlink, my_unlink, I_UNLINK, (p: *const c_char) -> c_int);
    hook!(rename, my_rename, I_RENAME, (a: *const c_char, b: *const c_char) -> c_int);
    hook!(opendir, my_opendir, I_OPENDIR, inode64 = "opendir$INODE64", (p: *const c_char) -> *mut c_void);
    hook!(readlink, my_readlink, I_READLINK, (p: *const c_char, b: *mut c_char, n: size_t) -> ssize_t);
    hook!(rmdir, my_rmdir, I_RMDIR, (p: *const c_char) -> c_int);
    hook!(chmod, my_chmod, I_CHMOD, (p: *const c_char, m: c_uint) -> c_int);
    hook!(fchmodat, my_fchmodat, I_FCHMODAT, (d: c_int, p: *const c_char, m: c_uint, f: c_int) -> c_int);
    hook!(symlink, my_symlink, I_SYMLINK, (a: *const c_char, b: *const c_char) -> c_int);
    hook!(chdir, my_chdir, I_CHDIR, (p: *const c_char) -> c_int);
    hook!(realpath, my_realpath, I_REALPATH, (p: *const c_char, r: *mut c_char) -> *mut c_char);
    hook!(getcwd, my_getcwd, I_GETCWD, (b: *mut c_char, n: size_t) -> *mut c_char);
    hook!(setenv, my_setenv, I_SETENV, (n: *const c_char, v: *const c_char, o: c_int) -> c_int);
    hook!(unsetenv, my_unsetenv, I_UNSETENV, (n: *const c_char) -> c_int);
    hook!(socket, my_socket, I_SOCKET, (a: c_int, b: c_int, c: c_int) -> c_int);
    hook!(connect, my_connect, I_CONNECT, (fd: c_int, a: *const c_void, l: c_uint) -> c_int);
    hook!(bind, my_bind, I_BIND, (fd: c_int, a: *const c_void, l: c_uint) -> c_int);
    hook!(getaddrinfo, my_getaddrinfo, I_GETADDRINFO, (n: *const c_char, s: *const c_char, h: *const c_void, r: *mut *mut c_void) -> c_int);
    hook!(clock_gettime, my_clock_gettime, I_CLOCK_GETTIME, (c: c_uint, t: *mut c_void) -> c_int);
    hook!(gettimeofday, my_gettimeofday, I_GETTIMEOFDAY, (tv: *mut c_void, tz: *mut c_void) -> c_int);
    hook!(mach_absolute_time, my_mach_absolute_time, I_MACH_ABSOLUTE_TIME, () -> u64);
    hook!(clock_gettime_nsec_np, my_clock_gettime_nsec_np, I_CLOCK_GETTIME_NSEC_NP, (c: c_uint) -> u64);
    hook!(getentropy, my_getentropy, I_GETENTROPY, (b: *mut c_void, n: size_t) -> c_int);
    hook!(arc4random_buf, my_arc4random_buf, I_ARC4RANDOM_BUF, (b: *mut c_void, n: size_t) -> ());
    hook!(posix_spawn, my_posix_spawn, I_POSIX_SPAWN, (p: *mut c_int, f: *const c_char, fa: *const c_void, at: *const c_void, av: *const *const c_char, ev: *const *const c_char) -> c_int);
    hook!(posix_spawnp, my_posix_spawnp, I_POSIX_SPAWNP, (p: *mut c_int, f: *const c_char, fa: *const c_void, at: *const c_void, av: *const *const c_char, ev: *const *const c_char) -> c_int);
    hook!(fork, my_fork, I_FORK, () -> c_int);
    hook!(execve, my_execve, I_EXECVE, (p: *const c_char, av: *const *const c_char, ev: *const *const c_char) -> c_int);
}
#[cfg(target_os = "macos")]
unsafe fn real_getenv(n: *const c_char) -> *mut c_char {
    plat::getenv(n)
}

// ---------------- Linux: LD_PRELOAD + dlsym(RTLD_NEXT) ----------------
#[cfg(target_os = "linux")]
mod plat {
    use super::*;
    extern "C" {
        fn dlsym(h: *mut c_void, s: *const c_char) -> *mut c_void;
    }
    const RTLD_NEXT: *mut c_void = -1isize as *mut c_void;
    // A libc without the function (arc4random_buf before glibc 2.36) cannot
    // have a caller linked against it -- unless the preload itself is what
    // satisfied the link. Exit 2 rather than jump to null.
    pub unsafe fn next(name: *const c_char) -> *mut c_void {
        let p = dlsym(RTLD_NEXT, name);
        if p.is_null() {
            out(b"\ntsn-hook: libc has no ");
            out(CStr::from_ptr(name).to_bytes());
            out(b"\n");
            _exit(2);
        }
        p
    }
    macro_rules! real {
        ($name:expr, $ty:ty) => {{
            let p = next($name.as_ptr() as *const c_char);
            core::mem::transmute::<*mut c_void, $ty>(p)
        }};
    }
    pub unsafe fn real_getenv(n: *const c_char) -> *mut c_char {
        real!(c"getenv", unsafe extern "C" fn(*const c_char) -> *mut c_char)(n)
    }
    #[no_mangle]
    pub unsafe extern "C" fn getenv(n: *const c_char) -> *mut c_char {
        guard(b"getenv");
        real_getenv(n)
    }
    // SysV x86_64 and AAPCS64-on-Linux pass a variadic int in the same register
    // as a fixed argument in that position, so a fixed definition reads `mode`.
    #[no_mangle]
    pub unsafe extern "C" fn open(p: *const c_char, f: c_int, mode: c_uint) -> c_int {
        guard(b"open");
        real!(c"open", unsafe extern "C" fn(*const c_char, c_int, ...) -> c_int)(p, f, mode)
    }
    #[no_mangle]
    pub unsafe extern "C" fn open64(p: *const c_char, f: c_int, mode: c_uint) -> c_int {
        guard(b"open64");
        real!(c"open64", unsafe extern "C" fn(*const c_char, c_int, ...) -> c_int)(p, f, mode)
    }
    #[no_mangle]
    pub unsafe extern "C" fn openat(d: c_int, p: *const c_char, f: c_int, mode: c_uint) -> c_int {
        guard(b"openat");
        real!(c"openat", unsafe extern "C" fn(c_int, *const c_char, c_int, ...) -> c_int)(d, p, f, mode)
    }
    #[no_mangle]
    pub unsafe extern "C" fn openat64(d: c_int, p: *const c_char, f: c_int, mode: c_uint) -> c_int {
        guard(b"openat64");
        real!(c"openat64", unsafe extern "C" fn(c_int, *const c_char, c_int, ...) -> c_int)(d, p, f, mode)
    }
    #[no_mangle]
    pub unsafe extern "C" fn read(fd: c_int, b: *mut c_void, n: size_t) -> ssize_t {
        if fd == 0 {
            guard(b"read");
        }
        real!(c"read", unsafe extern "C" fn(c_int, *mut c_void, size_t) -> ssize_t)(fd, b, n)
    }
    // One fixed-argument intercept, forwarding to the next definition.
    macro_rules! hook {
        ($name:ident, ($($a:ident: $t:ty),*) -> $r:ty) => {
            #[no_mangle]
            pub unsafe extern "C" fn $name($($a: $t),*) -> $r {
                guard(stringify!($name).as_bytes());
                real!(concat!(stringify!($name), "\0"), unsafe extern "C" fn($($t),*) -> $r)($($a),*)
            }
        };
    }
    hook!(stat, (p: *const c_char, b: *mut c_void) -> c_int);
    hook!(stat64, (p: *const c_char, b: *mut c_void) -> c_int);
    hook!(lstat, (p: *const c_char, b: *mut c_void) -> c_int);
    hook!(lstat64, (p: *const c_char, b: *mut c_void) -> c_int);
    hook!(fstatat, (d: c_int, p: *const c_char, b: *mut c_void, f: c_int) -> c_int);
    hook!(fstatat64, (d: c_int, p: *const c_char, b: *mut c_void, f: c_int) -> c_int);
    hook!(statx, (d: c_int, p: *const c_char, f: c_int, m: c_uint, b: *mut c_void) -> c_int);
    hook!(access, (p: *const c_char, m: c_int) -> c_int);
    hook!(mkdir, (p: *const c_char, m: c_uint) -> c_int);
    hook!(unlink, (p: *const c_char) -> c_int);
    hook!(rename, (a: *const c_char, b: *const c_char) -> c_int);
    hook!(opendir, (p: *const c_char) -> *mut c_void);
    hook!(readlink, (p: *const c_char, b: *mut c_char, n: size_t) -> ssize_t);
    hook!(rmdir, (p: *const c_char) -> c_int);
    hook!(chmod, (p: *const c_char, m: c_uint) -> c_int);
    hook!(fchmodat, (d: c_int, p: *const c_char, m: c_uint, f: c_int) -> c_int);
    hook!(symlink, (a: *const c_char, b: *const c_char) -> c_int);
    hook!(chdir, (p: *const c_char) -> c_int);
    hook!(realpath, (p: *const c_char, r: *mut c_char) -> *mut c_char);
    hook!(getcwd, (b: *mut c_char, n: size_t) -> *mut c_char);
    hook!(setenv, (n: *const c_char, v: *const c_char, o: c_int) -> c_int);
    hook!(unsetenv, (n: *const c_char) -> c_int);
    hook!(socket, (a: c_int, b: c_int, c: c_int) -> c_int);
    hook!(connect, (fd: c_int, a: *const c_void, l: c_uint) -> c_int);
    hook!(bind, (fd: c_int, a: *const c_void, l: c_uint) -> c_int);
    hook!(getaddrinfo, (n: *const c_char, s: *const c_char, h: *const c_void, r: *mut *mut c_void) -> c_int);
    hook!(clock_gettime, (c: c_int, t: *mut c_void) -> c_int);
    hook!(gettimeofday, (tv: *mut c_void, tz: *mut c_void) -> c_int);
    hook!(getrandom, (b: *mut c_void, n: size_t, f: c_uint) -> ssize_t);
    hook!(getentropy, (b: *mut c_void, n: size_t) -> c_int);
    hook!(arc4random_buf, (b: *mut c_void, n: size_t) -> ());
    hook!(posix_spawn, (p: *mut c_int, f: *const c_char, fa: *const c_void, at: *const c_void, av: *const *const c_char, ev: *const *const c_char) -> c_int);
    hook!(posix_spawnp, (p: *mut c_int, f: *const c_char, fa: *const c_void, at: *const c_void, av: *const *const c_char, ev: *const *const c_char) -> c_int);
    hook!(fork, () -> c_int);
    hook!(execve, (p: *const c_char, av: *const *const c_char, ev: *const *const c_char) -> c_int);
}
#[cfg(target_os = "linux")]
unsafe fn real_getenv(n: *const c_char) -> *mut c_char {
    plat::real_getenv(n)
}
