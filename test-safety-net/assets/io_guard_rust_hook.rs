// test-safety-net's Rust I/O guard: a libc interposer, written in Rust and
// built by the analysed repo's own `rustc` (io_guard_rust.py `hook_library`):
//
//   rustc --edition 2021 --crate-type cdylib -C panic=abort \
//         -C force-unwind-tables=yes -O io_guard_rust_hook.rs
//
// No cargo, no crates. It is preloaded under a compiled test binary
// (DYLD_INSERT_LIBRARIES via `__DATA,__interpose` on macOS, LD_PRELOAD via
// `dlsym(RTLD_NEXT)` on Linux), walks `backtrace()` at every intercepted call
// to find which function made it, and `_exit(3)`s on a violation. It also
// intercepts libc `exit` (ruling R19): an exit a crate frame is responsible
// for writes `tsn-hook: early exit (<symbol>)` before the real exit, so a
// test that ends the process before libtest reports is never read as a
// pass. `_exit`/`_Exit` are NEVER hooked: this image's own `_exit(3)` and
// `_exit(2)` must not recurse into a hook. The
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
// Ruling R42: the crate's own unmangled fns. The wrapper writes them, one
// `<name>\t<label>\n` line each, to a file outside the repo and names it in
// TSN_EXPORTS; the constructor copies it here ONCE, before arming. A list
// that cannot be read, or does not fit, is `cannot attribute` (exit 2).
// MAIN_BASE is the executable's own image: only a frame there can be one.
static mut EXPORTS: [u8; EXPORTS_CAP] = [0; EXPORTS_CAP];
static mut EXPORTS_LEN: usize = 0;
static mut MAIN_BASE: *mut c_void = core::ptr::null_mut();
const EXPORTS_PATH_CAP: usize = 4096;

// @@TSN-TABLES-BEGIN@@
// Rendered by io_guard_rust.py render_tables(); edit the Python tables, not this block.
static TRANSPARENT: &[&[u8]] = &[b"std", b"core", b"alloc", b"panic_unwind", b"backtrace", b"hashbrown", b"std_detect"];
static RUNNER: &[&[u8]] = &[b"test"];
static SEED: &[&[u8]] = &[b"hashmap_random_keys"];
static SEED_CRATE: &[u8] = b"std";
static TEST_BODY_BOUNDARY: &[&[u8]] = &[b"__rust_begin_short_backtrace", b"assert_test_result"];
static CONTROL: &[(&[u8], &[u8])] = &[(b"TsnControlEnv", b"environment"), (b"tsn_control_set_env", b"environment"), (b"tsn_control_temp_dir", b"filesystem")];
static SYSTEM_INTERNAL: &[&[u8]] = &[b"libsystem_malloc.dylib"];
static EARLY_EXIT_STATUS: c_int = 125;
const EXPORTS_CAP: usize = 1048576;
#[cfg(target_os = "macos")]
static INTERCEPT: &[(&[u8], &[u8])] = &[(b"clock_gettime", b"clock"), (b"gettimeofday", b"clock"), (b"mach_absolute_time", b"clock"), (b"clock_gettime_nsec_np", b"clock"), (b"getenv", b"environment"), (b"setenv", b"environment"), (b"unsetenv", b"environment"), (b"getcwd", b"environment"), (b"chdir", b"environment"), (b"open", b"filesystem"), (b"openat", b"filesystem"), (b"stat", b"filesystem"), (b"lstat", b"filesystem"), (b"fstatat", b"filesystem"), (b"access", b"filesystem"), (b"mkdir", b"filesystem"), (b"unlink", b"filesystem"), (b"rename", b"filesystem"), (b"opendir", b"filesystem"), (b"readlink", b"filesystem"), (b"rmdir", b"filesystem"), (b"chmod", b"filesystem"), (b"fchmodat", b"filesystem"), (b"symlink", b"filesystem"), (b"realpath", b"filesystem"), (b"socket", b"network"), (b"connect", b"network"), (b"bind", b"network"), (b"getaddrinfo", b"network"), (b"exit", b"process-exit"), (b"quick_exit", b"process-exit"), (b"getentropy", b"randomness"), (b"arc4random_buf", b"randomness"), (b"read", b"stdin"), (b"posix_spawn", b"subprocess"), (b"posix_spawnp", b"subprocess"), (b"fork", b"subprocess"), (b"execve", b"subprocess"), (b"execv", b"subprocess"), (b"execvp", b"subprocess"), (b"execl", b"subprocess"), (b"execlp", b"subprocess")];
#[cfg(target_os = "linux")]
static INTERCEPT: &[(&[u8], &[u8])] = &[(b"clock_gettime", b"clock"), (b"gettimeofday", b"clock"), (b"getenv", b"environment"), (b"setenv", b"environment"), (b"unsetenv", b"environment"), (b"getcwd", b"environment"), (b"chdir", b"environment"), (b"open", b"filesystem"), (b"openat", b"filesystem"), (b"stat", b"filesystem"), (b"lstat", b"filesystem"), (b"fstatat", b"filesystem"), (b"access", b"filesystem"), (b"mkdir", b"filesystem"), (b"unlink", b"filesystem"), (b"rename", b"filesystem"), (b"opendir", b"filesystem"), (b"readlink", b"filesystem"), (b"rmdir", b"filesystem"), (b"chmod", b"filesystem"), (b"fchmodat", b"filesystem"), (b"symlink", b"filesystem"), (b"realpath", b"filesystem"), (b"open64", b"filesystem"), (b"openat64", b"filesystem"), (b"stat64", b"filesystem"), (b"lstat64", b"filesystem"), (b"fstatat64", b"filesystem"), (b"statx", b"filesystem"), (b"socket", b"network"), (b"connect", b"network"), (b"bind", b"network"), (b"getaddrinfo", b"network"), (b"exit", b"process-exit"), (b"quick_exit", b"process-exit"), (b"getrandom", b"randomness"), (b"getentropy", b"randomness"), (b"arc4random_buf", b"randomness"), (b"read", b"stdin"), (b"posix_spawn", b"subprocess"), (b"posix_spawnp", b"subprocess"), (b"fork", b"subprocess"), (b"execve", b"subprocess"), (b"execv", b"subprocess"), (b"execvp", b"subprocess"), (b"execvpe", b"subprocess"), (b"execl", b"subprocess"), (b"execlp", b"subprocess"), (b"fexecve", b"subprocess")];
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
            if let Err(why) = load_exports() {
                cannot(why);
            }
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

/// Ruling R42: copy the file TSN_EXPORTS names into EXPORTS, once, in the
/// constructor (single-threaded, unarmed, so no intercept judges these
/// reads). Unset: no list, and nothing changes. Anything else that goes
/// wrong -- a path too long, a file that will not open or read, a list
/// larger than EXPORTS_CAP, an executable image that cannot be located --
/// is a reason, and the caller writes `cannot attribute` and exits 2.
unsafe fn load_exports() -> Result<(), &'static [u8]> {
    let mut path = [0u8; EXPORTS_PATH_CAP + 1];
    let n = match copy_env(c"TSN_EXPORTS", path.as_mut_ptr(), EXPORTS_PATH_CAP) {
        None => return Ok(()),
        Some(n) => n,
    };
    if n == 0 || n > EXPORTS_PATH_CAP {
        return Err(b"the crate-export list's path (TSN_EXPORTS) is empty or too long");
    }
    path[n] = 0;
    EXPORTS_LEN = plat::read_file(path.as_ptr() as *const c_char, (&raw mut EXPORTS) as *mut u8,
                                  EXPORTS_CAP)?;
    if EXPORTS_LEN > 0 {
        MAIN_BASE = main_base();
        if MAIN_BASE.is_null() {
            return Err(b"the executable's own image could not be located");
        }
    }
    Ok(())
}

/// The executable's own image, as `dladdr` reports it in `dli_fbase`. On
/// darwin, the ONE loaded image whose Mach-O header says MH_EXECUTE --
/// measured: inside an inserted library's initializer, dyld's image 0 is not
/// the executable, so the index cannot be trusted. On linux, the image
/// holding the program headers the kernel handed the loader (AT_PHDR).
#[cfg(target_os = "macos")]
unsafe fn main_base() -> *mut c_void {
    extern "C" {
        fn _dyld_image_count() -> u32;
        fn _dyld_get_image_header(i: u32) -> *const c_void;
    }
    const MH_EXECUTE: u32 = 2;
    let mut found: *mut c_void = core::ptr::null_mut();
    let count = _dyld_image_count().min(65_536);
    for i in 0..count {
        let h = _dyld_get_image_header(i);
        // mach_header_64: magic, cputype, cpusubtype, then filetype at +12.
        if !h.is_null() && core::ptr::read_unaligned((h as *const u8).add(12) as *const u32) == MH_EXECUTE {
            if !found.is_null() {
                return core::ptr::null_mut(); // two executables: cannot say which
            }
            found = h as *mut c_void;
        }
    }
    found
}
#[cfg(target_os = "linux")]
unsafe fn main_base() -> *mut c_void {
    extern "C" {
        fn getauxval(t: core::ffi::c_ulong) -> core::ffi::c_ulong;
    }
    const AT_PHDR: core::ffi::c_ulong = 3;
    let phdr = getauxval(AT_PHDR);
    let mut info = DlInfo::empty();
    if phdr == 0 || dladdr(phdr as *const c_void, &mut info) == 0 {
        return core::ptr::null_mut();
    }
    info.dli_fbase
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

// @@TSN-CLASSIFY-BEGIN@@
// The frame classifier: pure functions of a symbol name and the rendered
// tables -- no libc, no TLS, no allocation, no fmt, no panics. The test
// suite compiles this region ALONE (test_io_guard_rust.TestClassifier) and
// feeds it real symbol names, so every rule below is proven on any
// toolchain. A frame's symbol is decided PER SYMBOL (ruling R26): v0
// (`_R…`, Mach-O `__R…`) by `mod v0`, anything else by the legacy rules.

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

fn runner(k: &[u8]) -> bool {
    RUNNER.iter().any(|t| *t == k)
}

/// The first non-transparent, non-runner CRATE named in mangled text `t`, or
/// `None` (rulings R17b amended, R18). A crate always prints as a path
/// `crate..Type`: a segment that is the START of a `..`-joined path (the byte
/// before it is not part of a `..`) and is itself immediately followed by
/// `..`. A bare segment -- a generic parameter `T`, a primitive `u32`,
/// `dyn`, `const` -- is never followed by `..`, so it names no crate and is
/// skipped. This finds the payload crate wherever it sits: a generic
/// argument (`alloc..vec..Vec$LT$a2..P$GT$` -> `a2`), a slice or array
/// element (`$u5b$a2..P...`), a tuple element (`$LP$u32$C$$u20$a2..P$RP$`),
/// or behind a `Box$LT$dyn$u20$a2..Tr$GT$`. Transparent and runner crate
/// paths are stepped over so neither a std container nor a `test::` type
/// exempts.
fn first_crate(t: &[u8]) -> Option<&[u8]> {
    let mut i = 0;
    while i + 1 < t.len() {
        if t[i] == b'.' && t[i + 1] == b'.' {
            let mut start = i;
            while start > 0 && (t[start - 1].is_ascii_alphanumeric() || t[start - 1] == b'_') {
                start -= 1;
            }
            let path_start = start == 0 || t[start - 1] != b'.';
            let seg = &t[start..i];
            if path_start && !seg.is_empty() && !transparent(seg) && !runner(seg) {
                return Some(seg);
            }
            i += 2;
        } else {
            i += 1;
        }
    }
    None
}

/// The deciding crate of a mangled first path component `seg`. A plain path
/// component IS the crate (`_ZN`'s first component is `<len>crate`). An impl
/// component (`_$LT$...$GT$`, from `<T as Trait>::m` or `<T>::m`) is decided
/// by the SELF TYPE's crate (ruling R14), searched up to the ` as Trait`
/// boundary so the trait's crate is never picked -- a blanket `impl<T>` over
/// a bare `T` thus names no crate and reads transparent (ruling R18).
fn seg_crate(seg: &[u8]) -> &[u8] {
    let i = if seg.first() == Some(&b'_') { 1 } else { 0 };
    if !seg[i..].starts_with(b"$LT$") {
        return seg; // a plain path component: the crate itself
    }
    let inner = &seg[i + 4..];
    let end = find(inner, b"$u20$as$u20$").unwrap_or(inner.len());
    first_crate(&inner[..end]).unwrap_or(&[])
}

/// The bytes after a v0 symbol's `_R` (Mach-O: `__R`), or `None` when `s` is
/// not spelled as one.
fn v0_body(s: &[u8]) -> Option<&[u8]> {
    if s.starts_with(b"__R") {
        Some(&s[3..])
    } else if s.starts_with(b"_R") {
        Some(&s[2..])
    } else {
        None
    }
}

/// The deciding crate of a Rust symbol, or `None` when `s` is not one this
/// hook can read. v0: `mod v0`. Legacy: `None` without the `17h<16 hex>E`
/// hash (a C or C++ symbol). A `core::ptr::drop_in_place<T>` frame is decided
/// by the first non-transparent crate anywhere in T (ruling R17b amended):
/// libtest dropping a crate's panic payload -- plain or wrapped in a std
/// container -- runs that crate's Drop.
fn crate_of(s: &[u8]) -> Option<&[u8]> {
    if let Some(body) = v0_body(s) {
        return v0::facts(body).map(|f| f.deciding());
    }
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
            if let Some(k) = first_crate(&s[p + DIP.len()..]) {
                krate = k;
            }
        }
        // Rc/Arc's DEFERRED drop (ruling R17b amended, extended). At opt the
        // compiler shares one `Arc$LT$T$C$A$GT$..drop_slow` across every T and
        // inlines the payload's own Drop into it, so the payload crate is
        // erased from the stack -- `Arc::new(P)` then escaped. `drop_slow` is
        // std's refcount-zero destructor path and nothing else; treat it as a
        // drop boundary. It can only trip when the dropped value's destructor
        // makes a blocked call -- a pure refcounted drop merely frees memory
        // (its allocator clock read is R11-exempt), so no pure test trips.
        if transparent(krate) {
            if let Some(p) = find(s, b"drop_slow") {
                krate = &s[p..p + 9];
            }
        }
    }
    Some(krate)
}

/// Ruling R28: std seeding its HashMap, and nothing else. A legacy Rust
/// symbol whose FIRST path segment is SEED_CRATE and one of whose later
/// plain segments names a SEED marker (`_ZN3std3sys6random19hashmap_random_
/// keys17h…E`). A crate fn or test that merely contains the name, and an
/// impl segment's type text (`_$LT$…$GT$`), are not std's seeding.
fn legacy_seed(s: &[u8]) -> bool {
    let n = s.len();
    if n < 20 || s[n - 1] != b'E' || &s[n - 20..n - 17] != b"17h" {
        return false;
    }
    if !s[n - 17..n - 1].iter().all(|c| c.is_ascii_hexdigit()) {
        return false;
    }
    let mut i = 0;
    while i < n && s[i] == b'_' {
        i += 1;
    }
    if i + 1 >= n || s[i] != b'Z' || s[i + 1] != b'N' {
        return false;
    }
    i += 2;
    let end = n - 20; // where the `17h<hash>` segment begins
    let mut first = true;
    while i < end {
        let start = i;
        let mut len = 0usize;
        while i < end && s[i].is_ascii_digit() {
            len = len.saturating_mul(10).saturating_add((s[i] - b'0') as usize);
            i += 1;
        }
        if i == start || len == 0 || len > end - i {
            return false;
        }
        let seg = &s[i..i + len];
        i += len;
        if first {
            if seg != SEED_CRATE {
                return false;
            }
            first = false;
        } else if !seg.starts_with(b"_$") && !seg.starts_with(b"$")
            && SEED.iter().any(|m| contains(seg, m))
        {
            return true;
        }
    }
    false
}

// 0 transparent, 1 crate code, 2 libtest runner, 3 std seeding its HashMap.
// A v0 symbol that cannot be read is transparent, never exempt.
fn classify(s: &[u8]) -> u8 {
    let krate = match v0_body(s) {
        Some(body) => match v0::facts(body) {
            None => return 0,
            Some(f) if f.seed => return 3,
            Some(f) => Some(f.deciding()),
        },
        None => {
            if legacy_seed(s) {
                return 3;
            }
            crate_of(s)
        }
    };
    match krate {
        None => 0,
        Some(k) if transparent(k) => 0,
        Some(k) if RUNNER.iter().any(|t| *t == k) => 2,
        Some(_) => 1,
    }
}

/// A body-side boundary (rulings R13, R17a): a TEST_BODY_BOUNDARY frame in
/// libtest's own `test` crate. std's `__rust_begin_short_backtrace` (thread
/// spawn, lang_start) is not one. v0: a DECODED identifier of the main path
/// (`28___rust_begin_short_backtrace` is `__rust_begin_short_backtrace`).
fn test_boundary(s: &[u8]) -> bool {
    if let Some(body) = v0_body(s) {
        return match v0::facts(body) {
            Some(f) => runner(f.deciding()) && f.boundary,
            None => false,
        };
    }
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

/// Ruling R42: the label of `s` when it is one of the crate's own unmangled
/// fns in `list` (the wrapper's `<name>\t<label>\n` lines), else `None`. A
/// Rust-mangled name (legacy `_ZN`, v0 `_R`, Mach-O's extra `_` allowed) is
/// never on the list and is not looked up. The walk is bounded by the list:
/// each step moves past one line.
fn exported<'a>(list: &'a [u8], s: &[u8]) -> Option<&'a [u8]> {
    if s.is_empty() || s.starts_with(b"_ZN") || s.starts_with(b"__ZN") {
        return None;
    }
    // Ruling R46: a `_R` name is v0 only when the v0 reader reads it; a
    // `#[no_mangle] fn _Rfoo` is a plain name, as rust_binary lists it.
    if let Some(body) = v0_body(s) {
        if v0::facts(body).is_some() {
            return None;
        }
    }
    let mut rest = list;
    while !rest.is_empty() {
        let end = rest.iter().position(|&c| c == b'\n').unwrap_or(rest.len());
        let line = &rest[..end];
        let tab = line.iter().position(|&c| c == b'\t').unwrap_or(line.len());
        if &line[..tab] == s {
            return Some(if tab < line.len() { &line[tab + 1..] } else { line });
        }
        rest = if end < rest.len() { &rest[end + 1..] } else { &[] };
    }
    None
}

fn control(s: &[u8]) -> Option<&'static [u8]> {
    if let Some(body) = v0_body(s) {
        return v0::facts(body).and_then(|f| CONTROL.get(f.control)).map(|&(_, group)| group);
    }
    CONTROL.iter().find(|(name, _)| names(s, name)).map(|&(_, group)| group)
}

// ── v0 symbol mangling (ruling R26) ──────────────────────────────────────
//
// rustc 1.98 mangles with v0 by default, and std, core, alloc and libtest
// ship precompiled with it, so on 1.98 every frame is v0; on older
// toolchains every frame is legacy unless the crate opted in. Grammar: the
// official "Symbol grammar summary" of
// https://doc.rust-lang.org/rustc/symbol-mangling/v0.html, plus what
// rustc-demangle (the reference parser) also accepts: a type's `w` prefix,
// `W <type> <pattern>`, and the extended `const` forms. Back-references
// `B <base-62>` are offsets "starting from just after the `_R` prefix"
// (spec, "Backref"); only an EARLIER position is followed.
//
// Every classification rule the legacy reader applies holds here:
//   * a path's crate is its root `C [s<base-62>_] <len> [_] <ident>`;
//   * an impl (`M <impl-path> <type>`, `X <impl-path> <type> <trait>`) is
//     decided by its SELF TYPE (R14): the first crate in it that is neither
//     transparent nor the runner, or none (R18: a primitive such as `h`
//     (u8), the placeholder `p`, a std type); never the impl's own path and
//     never the trait. A provided method (`Y <type> <trait>`) is its self
//     type's, else its trait's -- the legacy name of the item is the trait's;
//   * `core::ptr::drop_glue<T>` / `drop_in_place<T>` is decided by the first
//     crate anywhere in T -- generics, tuples, arrays, slices, `dyn` (R17b);
//     only under the root crate `core` (R39): a crate's own fn or method so
//     named is its own frame, as legacy prints it without generic arguments;
//   * a main-path identifier containing `drop_slow` is Rc/Arc's deferred
//     drop, a boundary (R17b extended); a TEST_BODY_BOUNDARY one, in the
//     `test` crate, is libtest's call into the test (R13, R17a);
//   * the name tables match DECODED identifiers in LEGACY's scope exactly
//     (ruling R28): SEED only in the main path of a symbol whose crate is
//     SEED_CRATE (std); CONTROL only in the main path, an inherent (`M`)
//     or `core::ops::drop::Drop` (`X`) impl's self type -- never any other
//     trait impl's, which may be blanket (R37) -- by ITS OWN path (R34: never its generic arguments --
//     legacy prints `Result<T, E>`, not `Result<&str, p::TsnControlEnv>`),
//     or drop glue's payload. Never an ordinary fn's generic
//     arguments -- legacy never spells them, and `std::fs::read::<p::
//     TsnControlEnv>` is std's read, not the control -- and never a
//     provided method's (`Y`) self type.
// The instantiating-crate suffix never decides. Malformed, truncated, or
// nested past MAX_DEPTH (or MAX_STEPS nodes): `None`, read as transparent.
// rust_binary.py's `v0_facts` is the independent Python twin of this reader.
mod v0 {
    use super::{contains, runner, transparent, CONTROL, SEED, SEED_CRATE, TEST_BODY_BOUNDARY};

    /// Recursion past this reads the symbol transparent: a fixed, small stack.
    /// `N` chains are read iteratively; only generics and types nest.
    const MAX_DEPTH: u32 = 64;
    /// Nodes parsed, back-references re-read included: past it, transparent.
    const MAX_STEPS: u32 = 10_000;
    /// CONTROL's scope while a type is read (rulings R28, R34): off; an M/X
    /// self type's OWN path (its crate root and `N` chain, through an `I`
    /// node's inner path -- never its generic arguments, never behind `&`,
    /// `*`, a tuple, array, slice, fn pointer or `dyn`, never a nested impl);
    /// or all of it (drop glue's payload, as legacy's `drop_in_place<T>`).
    const SCAN_OFF: u8 = 0;
    const SCAN_OWN: u8 = 1;
    const SCAN_FULL: u8 = 2;

    pub struct Facts<'a> {
        /// The main path's deciding crate; empty when it names none.
        pub krate: &'a [u8],
        pub boundary: bool,
        pub drop_slow: bool,
        pub drop_crate: Option<&'a [u8]>,
        /// Index of the first-listed CONTROL helper named in CONTROL's scope
        /// (R28), else usize::MAX.
        pub control: usize,
        /// std's own frame, its main path naming a SEED marker (R28).
        pub seed: bool,
    }

    impl<'a> Facts<'a> {
        /// The crate the frame is judged by, the drop rules applied (R17b).
        pub fn deciding(&self) -> &'a [u8] {
            let mut k = self.krate;
            if transparent(k) {
                if let Some(d) = self.drop_crate {
                    k = d;
                }
                if transparent(k) && self.drop_slow {
                    k = b"drop_slow";
                }
            }
            k
        }
    }

    struct P<'a> {
        s: &'a [u8],
        pos: usize,
        depth: u32,
        steps: u32,
        search: bool,
        found: Option<&'a [u8]>,
        /// CONTROL's scope while reading a type: SCAN_OFF, SCAN_OWN (an M/X
        /// self type's OWN path: crate root and `N` chain, R34) or SCAN_FULL
        /// (drop glue's payload, legacy's `drop_in_place<T>` text).
        scan: u8,
        last: &'a [u8],
        /// The main path's root crate as `path_main` last read it: its `C`
        /// identifier, or empty under an impl (`M`/`X`/`Y`) (ruling R39).
        root: &'a [u8],
        boundary: bool,
        drop_slow: bool,
        drop_crate: Option<&'a [u8]>,
        control: usize,
        /// A main-path identifier names a SEED marker (std's crate is checked last).
        seed: bool,
    }

    /// The facts of the v0 symbol whose bytes after `_R` are `body`, or `None`.
    pub fn facts(body: &[u8]) -> Option<Facts<'_>> {
        let mut p = P {
            s: body,
            pos: 0,
            depth: 0,
            steps: 0,
            search: false,
            found: None,
            scan: SCAN_OFF,
            last: &[],
            root: &[],
            boundary: false,
            drop_slow: false,
            drop_crate: None,
            control: usize::MAX,
            seed: false,
        };
        if p.peek()?.is_ascii_digit() {
            p.decimal()?; // the encoding version: never emitted today
        }
        let krate = p.path_main()?;
        match p.peek() {
            None | Some(b'.') | Some(b'$') => {}
            Some(_) => {
                p.path_any()?; // the instantiating crate
                if !matches!(p.peek(), None | Some(b'.') | Some(b'$')) {
                    return None;
                }
            }
        }
        Some(Facts {
            krate,
            boundary: p.boundary,
            drop_slow: p.drop_slow,
            drop_crate: p.drop_crate,
            control: p.control,
            seed: p.seed && krate == SEED_CRATE,
        })
    }

    impl<'a> P<'a> {
        fn peek(&self) -> Option<u8> {
            self.s.get(self.pos).copied()
        }

        fn next(&mut self) -> Option<u8> {
            let c = self.peek()?;
            self.pos += 1;
            Some(c)
        }

        fn eat(&mut self, c: u8) -> bool {
            if self.peek() == Some(c) {
                self.pos += 1;
                true
            } else {
                false
            }
        }

        fn enter(&mut self) -> Option<()> {
            self.depth += 1;
            self.steps += 1;
            if self.depth > MAX_DEPTH || self.steps > MAX_STEPS {
                None
            } else {
                Some(())
            }
        }

        fn leave(&mut self) {
            self.depth = self.depth.saturating_sub(1);
        }

        /// `decimal-number`: `0`, or a non-zero digit and any digits.
        fn decimal(&mut self) -> Option<u64> {
            let c = self.next()?;
            if !c.is_ascii_digit() {
                return None;
            }
            let mut v = (c - b'0') as u64;
            if v == 0 {
                return Some(0);
            }
            while let Some(d) = self.peek() {
                if !d.is_ascii_digit() {
                    break;
                }
                self.pos += 1;
                v = v.checked_mul(10)?.checked_add((d - b'0') as u64)?;
            }
            Some(v)
        }

        /// `base-62-number`: `_` is 0, else digits then `_`, plus one.
        fn base62(&mut self) -> Option<u64> {
            if self.eat(b'_') {
                return Some(0);
            }
            let mut v: u64 = 0;
            loop {
                let c = self.next()?;
                let d = match c {
                    b'0'..=b'9' => c - b'0',
                    b'a'..=b'z' => c - b'a' + 10,
                    b'A'..=b'Z' => c - b'A' + 36,
                    b'_' => return v.checked_add(1),
                    _ => return None,
                };
                v = v.checked_mul(62)?.checked_add(d as u64)?;
            }
        }

        /// `undisambiguated-identifier`: [`u`] <len> [`_`] <bytes>. The `_`
        /// separates a length from bytes that begin with a digit or `_`
        /// (spec, "Identifier"). Inside an M/X self type or a drop-glue
        /// payload (`scan`) it is checked against CONTROL (ruling R28).
        fn undis(&mut self) -> Option<&'a [u8]> {
            self.eat(b'u');
            let n = usize::try_from(self.decimal()?).ok()?;
            self.eat(b'_');
            let s = self.s;
            let id = s.get(self.pos..self.pos.checked_add(n)?)?;
            self.pos += n;
            if self.scan != SCAN_OFF {
                self.note_control(id);
            }
            Some(id)
        }

        /// CONTROL's scope is legacy's (ruling R28): the main path's own
        /// identifiers (path_main), an M/X self type and drop glue's payload
        /// (`scan`). Never an ordinary fn's generic arguments, never a `Y`
        /// self type.
        fn note_control(&mut self, id: &[u8]) {
            if let Some(i) = CONTROL.iter().position(|&(name, _)| name == id) {
                if i < self.control {
                    self.control = i;
                }
            }
        }

        /// `identifier`: an optional `s<base-62>` disambiguator, then the name.
        fn ident(&mut self) -> Option<&'a [u8]> {
            if self.eat(b's') {
                self.base62()?;
            }
            self.undis()
        }

        /// After a consumed `B`: (target, resume). The target must lie
        /// strictly before the `B`, so every walk terminates.
        fn backref(&mut self) -> Option<(usize, usize)> {
            let at = self.pos.checked_sub(1)?;
            let t = usize::try_from(self.base62()?).ok()?;
            if t >= at {
                return None;
            }
            Some((t, self.pos))
        }

        /// A run of `N<namespace>`: the identifiers that follow the inner path.
        fn nest(&mut self) -> Option<usize> {
            let mut k = 0usize;
            while self.peek() == Some(b'N') {
                self.pos += 1;
                if !self.next()?.is_ascii_alphabetic() {
                    return None;
                }
                k += 1;
            }
            Some(k)
        }

        /// One type (or generic argument) with the crate search on: the first
        /// crate root in it that is neither transparent nor the runner. With
        /// `scan`, its identifiers are in CONTROL's scope (R28).
        fn searching(&mut self, arg: bool, scan: u8) -> Option<Option<&'a [u8]>> {
            let (search, found, scanning) = (self.search, self.found, self.scan);
            self.search = true;
            self.found = None;
            self.scan = scan;
            let ok = if arg { self.generic_arg() } else { self.type_any() };
            let hit = self.found;
            self.search = search;
            self.found = found;
            self.scan = scanning;
            ok?;
            Some(hit)
        }

        /// The MAIN path: returns its deciding crate, and notes its own
        /// identifiers (boundary, drop_slow) and drop glue's payload crate.
        fn path_main(&mut self) -> Option<&'a [u8]> {
            self.enter()?;
            let nest = self.nest()?;
            let krate: &'a [u8] = match self.next()? {
                b'C' => {
                    let id = self.ident()?;
                    self.last = &[];
                    self.root = id;
                    id
                }
                b'M' => {
                    self.impl_path()?;
                    self.last = &[];
                    self.root = &[];
                    self.searching(false, SCAN_OWN)?.unwrap_or(&[])
                }
                b'X' => {
                    self.impl_path()?;
                    let at = self.pos;
                    let own = self.searching(false, SCAN_OFF)?.unwrap_or(&[]);
                    let trait_at = self.pos;
                    self.path_any()?;
                    // Ruling R37: v0 substitutes an impl's generics into its
                    // self type, so a blanket `impl<T> Tr for T` called on the
                    // helper reads `<TsnControlEnv as Tr>` -- legacy prints
                    // `<T as Tr>`, no crate. Only `core::ops::drop::Drop`,
                    // which cannot be blanket, lends its self type a name.
                    if self.is_core_drop(trait_at) {
                        let end = self.pos;
                        self.pos = at;
                        self.searching(false, SCAN_OWN)?;
                        self.pos = end;
                    }
                    self.last = &[];
                    self.root = &[];
                    own
                }
                b'Y' => {
                    let own = self.searching(false, SCAN_OFF)?.unwrap_or(&[]);
                    let trait_crate = self.path_main()?;
                    self.root = &[];
                    if own.is_empty() {
                        trait_crate
                    } else {
                        own
                    }
                }
                b'I' => {
                    let krate = self.path_main()?;
                    // Ruling R39: only CORE's drop glue lends its payload a
                    // control name. Legacy looks drop_in_place up only under
                    // a transparent root (crate_of) and prints a crate's own
                    // fn so named without generic arguments, so
                    // `r3::drop_in_place::<TsnControlEnv>` is r3's frame.
                    let drop = self.root == &b"core"[..]
                        && (self.last == &b"drop_glue"[..] || self.last == &b"drop_in_place"[..]);
                    while !self.eat(b'E') {
                        if drop {
                            let hit = self.searching(true, SCAN_FULL)?;
                            if self.drop_crate.is_none() {
                                self.drop_crate = hit;
                            }
                        } else {
                            self.generic_arg()?;
                        }
                    }
                    krate
                }
                b'B' => {
                    let (t, back) = self.backref()?;
                    self.pos = t;
                    let krate = self.path_main()?;
                    self.pos = back;
                    krate
                }
                _ => return None,
            };
            for _ in 0..nest {
                let id = self.ident()?;
                self.note_control(id);
                if SEED.iter().any(|m| contains(id, m)) {
                    self.seed = true;
                }
                if TEST_BODY_BOUNDARY.iter().any(|m| contains(id, m)) {
                    self.boundary = true;
                }
                if contains(id, b"drop_slow") {
                    self.drop_slow = true;
                }
                self.last = id;
            }
            self.leave();
            Some(krate)
        }

        /// Any other path: read through; in search mode its crate roots count.
        fn path_any(&mut self) -> Option<()> {
            self.enter()?;
            let nest = self.nest()?;
            match self.next()? {
                b'C' => {
                    let id = self.ident()?;
                    if self.search && self.found.is_none() && !transparent(id) && !runner(id) {
                        self.found = Some(id);
                    }
                }
                b'M' => {
                    let scan = self.own_off();
                    self.impl_path()?;
                    self.type_any()?;
                    self.scan = scan;
                }
                b'X' => {
                    let scan = self.own_off();
                    self.impl_path()?;
                    self.type_any()?;
                    self.path_any()?;
                    self.scan = scan;
                }
                b'Y' => {
                    let scan = self.own_off();
                    self.type_any()?;
                    self.path_any()?;
                    self.scan = scan;
                }
                b'I' => {
                    self.path_any()?;
                    let scan = self.own_off();
                    while !self.eat(b'E') {
                        self.generic_arg()?;
                    }
                    self.scan = scan;
                }
                b'B' => {
                    let (t, back) = self.backref()?;
                    self.pos = t;
                    self.path_any()?;
                    self.pos = back;
                }
                _ => return None,
            }
            for _ in 0..nest {
                self.ident()?;
            }
            self.leave();
            Some(())
        }

        fn impl_path(&mut self) -> Option<()> {
            if self.eat(b's') {
                self.base62()?;
            }
            self.path_any()
        }

        /// Leaving an M/X self type's OWN path (R34): a generic argument, a
        /// type constructor or a nested impl is read with CONTROL off. Returns
        /// the scope to restore.
        fn own_off(&mut self) -> u8 {
            let scan = self.scan;
            if scan == SCAN_OWN {
                self.scan = SCAN_OFF;
            }
            scan
        }

        /// Ruling R37: is the path at `at` exactly `core::ops::drop::Drop`,
        /// read through back-references? The cursor is restored.
        fn is_core_drop(&mut self, at: usize) -> bool {
            let back = self.pos;
            self.pos = at;
            let hit = self.drop_seq(0) == Some(4);
            self.pos = back;
            hit
        }

        /// How many leading identifiers of `core::ops::drop::Drop` the plain
        /// path here spells (its crate root, then its `N` chain), or `None`
        /// when it is anything else. Back-references nest at most 8 deep.
        fn drop_seq(&mut self, depth: u32) -> Option<usize> {
            const DROP: [&[u8]; 4] = [b"core", b"ops", b"drop", b"Drop"];
            if depth > 8 {
                return None;
            }
            let nest = self.nest()?;
            let mut n = match self.next()? {
                b'C' => {
                    if self.ident()? != DROP[0] {
                        return None;
                    }
                    1
                }
                b'B' => {
                    let (t, back) = self.backref()?;
                    self.pos = t;
                    let n = self.drop_seq(depth + 1)?;
                    self.pos = back;
                    n
                }
                _ => return None,
            };
            for _ in 0..nest {
                let id = self.ident()?;
                if n >= DROP.len() || id != DROP[n] {
                    return None;
                }
                n += 1;
            }
            Some(n)
        }

        fn generic_arg(&mut self) -> Option<()> {
            if self.eat(b'L') {
                self.base62()?;
                return Some(());
            }
            if self.eat(b'K') {
                return self.const_any();
            }
            self.type_any()
        }

        fn type_any(&mut self) -> Option<()> {
            self.eat(b'w');
            let tag = self.next()?;
            // basic types (a primitive, or the placeholder `p`) name no crate
            if matches!(tag, b'a'..=b'f' | b'h'..=b'j' | b'l'..=b'p' | b's'..=b'v' | b'x'..=b'z') {
                return Some(());
            }
            self.enter()?;
            // A path (or a back-reference to one) keeps the scope; any type
            // constructor -- `&`, `*`, tuple, array, slice, fn, `dyn` -- leaves
            // an M/X self type's own path (R34).
            let scan = if matches!(tag, b'B' | b'C' | b'N' | b'M' | b'X' | b'Y' | b'I') {
                self.scan
            } else {
                self.own_off()
            };
            match tag {
                b'R' | b'Q' => {
                    if self.eat(b'L') {
                        self.base62()?;
                    }
                    self.type_any()?;
                }
                b'P' | b'O' | b'S' => self.type_any()?,
                b'A' => {
                    self.type_any()?;
                    self.const_any()?;
                }
                b'T' => {
                    while !self.eat(b'E') {
                        self.type_any()?;
                    }
                }
                b'F' => {
                    if self.eat(b'G') {
                        self.base62()?;
                    }
                    self.eat(b'U');
                    if self.eat(b'K') && !self.eat(b'C') {
                        self.undis()?;
                    }
                    while !self.eat(b'E') {
                        self.type_any()?;
                    }
                    self.type_any()?;
                }
                b'D' => {
                    if self.eat(b'G') {
                        self.base62()?;
                    }
                    while !self.eat(b'E') {
                        self.path_any()?;
                        while self.eat(b'p') {
                            self.undis()?;
                            if self.eat(b'K') {
                                self.const_any()?;
                            } else {
                                self.type_any()?;
                            }
                        }
                    }
                    if !self.eat(b'L') {
                        return None;
                    }
                    self.base62()?;
                }
                b'W' => {
                    self.type_any()?;
                    self.pattern()?;
                }
                b'B' => {
                    let (t, back) = self.backref()?;
                    self.pos = t;
                    self.type_any()?;
                    self.pos = back;
                }
                b'C' | b'N' | b'M' | b'X' | b'Y' | b'I' => {
                    self.pos -= 1;
                    self.path_any()?;
                }
                _ => return None,
            }
            self.scan = scan;
            self.leave();
            Some(())
        }

        fn const_any(&mut self) -> Option<()> {
            let tag = self.next()?;
            self.enter()?;
            match tag {
                b'p' => {}
                b'h' | b't' | b'm' | b'y' | b'o' | b'j' | b'b' | b'c' | b'e' => self.hex()?,
                b'a' | b's' | b'l' | b'x' | b'n' | b'i' => {
                    self.eat(b'n');
                    self.hex()?;
                }
                b'R' | b'Q' => {
                    if tag == b'R' && self.eat(b'e') {
                        self.hex()?;
                    } else {
                        self.const_any()?;
                    }
                }
                b'A' | b'T' => {
                    while !self.eat(b'E') {
                        self.const_any()?;
                    }
                }
                b'V' => {
                    self.path_any()?;
                    match self.next()? {
                        b'U' => {}
                        b'T' => {
                            while !self.eat(b'E') {
                                self.const_any()?;
                            }
                        }
                        b'S' => {
                            while !self.eat(b'E') {
                                self.ident()?;
                                self.const_any()?;
                            }
                        }
                        _ => return None,
                    }
                }
                b'B' => {
                    let (t, back) = self.backref()?;
                    self.pos = t;
                    self.const_any()?;
                    self.pos = back;
                }
                _ => return None,
            }
            self.leave();
            Some(())
        }

        fn hex(&mut self) -> Option<()> {
            loop {
                match self.next()? {
                    b'_' => return Some(()),
                    b'0'..=b'9' | b'a'..=b'f' => {}
                    _ => return None,
                }
            }
        }

        fn pattern(&mut self) -> Option<()> {
            match self.next()? {
                b'R' => {
                    self.const_any()?;
                    self.const_any()?;
                }
                b'N' => {}
                b'O' => {
                    self.enter()?;
                    self.pattern()?;
                    while !self.eat(b'E') {
                        self.pattern()?;
                    }
                    self.leave();
                }
                _ => return None,
            }
            Some(())
        }
    }
}
// @@TSN-CLASSIFY-END@@

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

type ReadFn = unsafe extern "C" fn(c_int, *mut c_void, size_t) -> ssize_t;

/// Ruling R42: read `fd` to its end into `buf[..cap]` with the REAL `read`
/// (`rd`): the byte count, or a reason. Each pass ends or moves at least one
/// byte and the passes are capped, so the loop is bounded; a byte past `cap`
/// is an overflow, never a silent truncation.
unsafe fn fill(fd: c_int, buf: *mut u8, cap: usize, rd: ReadFn) -> Result<usize, &'static [u8]> {
    const UNREADABLE: &[u8] = b"the crate-export list (TSN_EXPORTS) could not be read";
    let mut len = 0usize;
    let mut passes = 0usize;
    while passes <= cap + 1 {
        passes += 1;
        if len >= cap {
            let mut one = [0u8; 1];
            return match rd(fd, one.as_mut_ptr() as *mut c_void, 1) {
                0 => Ok(len),
                n if n > 0 => Err(b"the crate-export list is larger than the hook reads"),
                _ => Err(UNREADABLE),
            };
        }
        let n = rd(fd, buf.add(len) as *mut c_void, cap - len);
        if n < 0 {
            return Err(UNREADABLE);
        }
        if n == 0 {
            return Ok(len);
        }
        len += (n as usize).min(cap - len);
    }
    Err(UNREADABLE)
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

/// What one frame walk decided. The walk IS the decision rule, shared by
/// every I/O intercept (`decide`) and by `exit` (`exit_guard`, ruling R19).
enum Walk {
    /// libtest's own work, std seeding its HashMap, pre-main init: no verdict.
    Exempt,
    /// a crate frame -- or a stand-in label -- is responsible for the call.
    Crate(&'static [u8]),
    /// a control helper is responsible, with the group it stands for.
    Control(&'static [u8], &'static [u8]),
    /// the walk could not be read (ruling R1).
    Cannot(&'static [u8]),
}

/// Walk `backtrace()` from the innermost frame outward, the hook's own image
/// skipped. `exempt_system_internal` applies ruling R11 (an allocator's own
/// calls); it means nothing for `exit`.
unsafe fn walk(exempt_system_internal: bool) -> Walk {
    // 1024 frames, a stack array, no allocation (ruling R15a). A walk that
    // fills the buffer without deciding is judged below, not exempted.
    let mut pcs = [core::ptr::null_mut::<c_void>(); CAP];
    let n = backtrace(pcs.as_mut_ptr(), CAP as c_int) as usize;
    if n < 3 {
        return Walk::Cannot(b"backtrace() returned fewer than 3 frames");
    }
    let base = SELF_BASE;
    if base.is_null() {
        return Walk::Cannot(b"the hook could not locate its own image");
    }
    #[cfg(target_os = "linux")]
    if !symtab::FOUND.load(Ordering::Relaxed) {
        return Walk::Cannot(b"no .symtab in /proc/self/exe");
    }
    let mut resolved = false;
    let mut first = true;
    for &ret in pcs.iter().take(n) {
        // Every frame but the innermost is a RETURN address, one past its
        // call. After a call that never returns (`exit`, ruling R19) that is
        // the first byte of the NEXT function, so resolve one byte earlier:
        // inside the calling instruction, in the caller -- what every
        // symbolizer does. Any other call resolves to the same function.
        let pc = (ret as usize).wrapping_sub(1) as *mut c_void;
        let mut info = DlInfo::empty();
        let found = dladdr(pc, &mut info) != 0;
        if found && info.dli_fbase == base {
            continue;
        }
        // The call's immediate caller: a system-internal image's own work is
        // infrastructure, exempt (R11).
        if first {
            first = false;
            if exempt_system_internal && found && system_internal(info.dli_fname) {
                return Walk::Exempt;
            }
        }
        let mut s: Option<&'static [u8]> = None;
        if found && !info.dli_sname.is_null() {
            s = Some(CStr::from_ptr(info.dli_sname).to_bytes());
        }
        // Ruling R42: is this frame in the executable's own image?
        #[cfg_attr(target_os = "macos", allow(unused_mut))]
        let mut in_main = found && !MAIN_BASE.is_null() && info.dli_fbase == MAIN_BASE;
        #[cfg(target_os = "linux")]
        if s.map(|x| classify(x) == 0 && !x.ends_with(b"E")).unwrap_or(true) {
            if let Some(t) = symtab::lookup(pc) {
                s = Some(t);
                in_main = true; // the .symtab read is /proc/self/exe's own
            }
        }
        let s = match s {
            Some(s) => s,
            None => continue,
        };
        resolved = true;
        // A control helper decides, with the group it stands for.
        if let Some(g) = control(s) {
            return Walk::Control(g, s);
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
            return Walk::Crate(b"(test body, inlined)");
        }
        match classify(s) {
            1 => return Walk::Crate(s),
            2 | 3 => return Walk::Exempt,
            _ => {}
        }
        // Ruling R42: a `#[no_mangle]`/`#[export_name]` fn carries a plain C
        // symbol that names no crate. One the wrapper listed as the crate's
        // own (an rcgu member of a cargo-built rlib), in the executable's
        // image, IS a crate frame, judged like any other -- an `atexit` or
        // `.init_array` callback no longer reads as pre-`main` init.
        if in_main && EXPORTS_LEN > 0 {
            let list = core::slice::from_raw_parts((&raw const EXPORTS) as *const u8,
                                                   EXPORTS_LEN.min(EXPORTS_CAP));
            if let Some(who) = exported(list, s) {
                return Walk::Crate(who);
            }
        }
    }
    // Ruling R15a: the buffer filled and nothing decided -- a stack too deep
    // to attribute. Fail closed, as go does past its cap.
    if n == CAP {
        return Walk::Crate(b"(a stack deeper than 1024 frames)");
    }
    if !resolved {
        return Walk::Cannot(b"no frame resolved to a symbol");
    }
    // Ruling R15b: no crate frame on this stack. Off the main thread it is a
    // spawned thread or a thread-local destructor running std-only code, and
    // it is JUDGED. On the main thread it is pre-`main` libc/dyld init and
    // stays exempt.
    if !is_main_thread() {
        return Walk::Crate(b"(a thread with no crate frame)");
    }
    Walk::Exempt
}

unsafe fn decide(what: &'static [u8]) {
    // Each intercept's group comes from the rendered INTERCEPT table, never
    // from a literal beside the hook. A hooked name with no row fails closed.
    let group = match INTERCEPT.iter().find(|&&(name, _)| name == what) {
        Some(&(_, g)) => g,
        None => cannot(b"an intercept has no group in the rendered table"),
    };
    match walk(true) {
        Walk::Exempt => {}
        Walk::Crate(who) => judge(group, what, who),
        Walk::Control(g, who) => judge(g, what, who),
        Walk::Cannot(why) => cannot(why),
    }
}

/// Ruling R22(b): the re-entrancy mark is the ADDRESS of this private
/// static, never merely "non-null". A test that filled every pthread key
/// with a value made each gate believe it was already inside the hook, and
/// every call passed -- real I/O included.
static SENTINEL: u8 = 0x5A;

unsafe fn in_hook() -> bool {
    pthread_getspecific(KEY) as *const u8 == &SENTINEL as *const u8
}

unsafe fn enter_hook() {
    pthread_setspecific(KEY, &SENTINEL as *const u8 as *const c_void);
}

unsafe fn leave_hook() {
    pthread_setspecific(KEY, core::ptr::null());
}

/// Ruling R19: libc `exit` or `quick_exit`, reached with a crate frame
/// responsible (the same walk), is the test ending the process before
/// libtest can print its verdict. It is REPORTED, never judged: the group,
/// `process-exit`, is in no blocked list. libtest's own exits -- 101 after a
/// failure, the normal end after main returns -- have no crate frame and
/// stay silent. A walk that cannot be read reports too, with its reason.
/// Returns true when the exit is the test's own: the caller then exits with
/// EARLY_EXIT_STATUS, not the test's code (ruling R22(a)), so the verdict
/// survives a closed fd 2.
fn exit_guard(what: &'static [u8]) -> bool {
    if !READY.load(Ordering::Acquire) || !ARMED.load(Ordering::Acquire) {
        return false;
    }
    unsafe {
        match INTERCEPT.iter().find(|&&(name, _)| name == what) {
            Some(&(_, g)) if g == &b"process-exit"[..] => {}
            _ => cannot(b"an intercept has no group in the rendered table"),
        }
        if in_hook() {
            return false;
        }
        enter_hook();
        let who = match walk(false) {
            Walk::Exempt => None,
            Walk::Crate(w) | Walk::Control(_, w) => Some(w),
            Walk::Cannot(why) => Some(why),
        };
        if let Some(w) = who {
            out(b"\ntsn-hook: early exit (");
            out(w);
            out(b")\n");
        }
        leave_hook();
        who.is_some()
    }
}

fn guard(what: &'static [u8]) {
    if !READY.load(Ordering::Acquire) || !ARMED.load(Ordering::Acquire) {
        return;
    }
    unsafe {
        if in_hook() {
            return;
        }
        enter_hook();
        decide(what);
        leave_hook();
    }
}

/// The real exec functions' shape, for the C-variadic `execl`/`execlp`.
type ExecV = unsafe extern "C" fn(*const c_char, *const *const c_char) -> c_int;
/// How many argument slots a variadic exec replacement reads.
const EXEC_SLOTS: usize = 32;

/// Rebuild a C-variadic exec argument list (`arg0, ..., NULL`) read from
/// fixed parameter slots and hand it to the NON-variadic real function.
/// Reached only when the call was not judged (the hook unarmed, or an
/// exempt caller): armed, `subprocess` is blocked at every tier.
unsafe fn forward_list(p: *const c_char, slots: &[*const c_char; EXEC_SLOTS], f: ExecV) -> c_int {
    let mut argv = [core::ptr::null::<c_char>(); EXEC_SLOTS + 1];
    for (i, &s) in slots.iter().enumerate() {
        argv[i] = s;
        if s.is_null() {
            return f(p, argv.as_ptr());
        }
    }
    out(b"\ntsn-hook: an exec argument list longer than the hook reads\n");
    _exit(2)
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
        fn exit(code: c_int) -> !;
        fn quick_exit(code: c_int) -> !;
        fn execl(p: *const c_char, a0: *const c_char, ...) -> c_int;
        fn execlp(f: *const c_char, a0: *const c_char, ...) -> c_int;
        fn close(fd: c_int) -> c_int;
    }
    /// Ruling R42: the crate-export list, read with the real open/read.
    pub unsafe fn read_file(p: *const c_char, buf: *mut u8, cap: size_t) -> Result<usize, &'static [u8]> {
        let fd = open(p, 0);
        if fd < 0 {
            return Err(b"the crate-export list (TSN_EXPORTS) could not be opened");
        }
        let got = fill(fd, buf, cap, read);
        close(fd);
        got
    }
    // Rulings R19, R22(a): reported, then the real exit -- with the reserved
    // status when crate code made it. `_exit` is not hooked.
    unsafe extern "C" fn my_exit(code: c_int) -> ! {
        let early = exit_guard(b"exit");
        exit(if early { EARLY_EXIT_STATUS } else { code })
    }
    unsafe extern "C" fn my_quick_exit(code: c_int) -> ! {
        let early = exit_guard(b"quick_exit");
        quick_exit(if early { EARLY_EXIT_STATUS } else { code })
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
        I_EXIT: my_exit => exit,
        I_QUICK_EXIT: my_quick_exit => quick_exit,
        I_EXECL: my_execl => execl,
        I_EXECLP: my_execlp => execlp,
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
    // Ruling R22(d): every exec entry point, not only execve. libSystem has
    // no execvpe or fexecve.
    hook!(execv, my_execv, I_EXECV, (p: *const c_char, av: *const *const c_char) -> c_int);
    hook!(execvp, my_execvp, I_EXECVP, (f: *const c_char, av: *const *const c_char) -> c_int);
    // execl/execlp are C-variadic, which stable Rust cannot define. Apple
    // arm64 passes EVERY variadic argument on the stack: a fixed definition
    // reads the first from its 9th parameter slot (the my_open trick), so
    // `arg0` is x1 and the rest follow from the first stack slot. x86_64
    // passes them like fixed arguments. The list is read up to its NULL and
    // handed to the real, non-variadic execv/execvp.
    #[cfg(target_arch = "aarch64")]
    unsafe extern "C" fn my_execl(p: *const c_char, a0: *const c_char, _r2: u64, _r3: u64, _r4: u64, _r5: u64, _r6: u64, _r7: u64, v0: *const c_char, v1: *const c_char, v2: *const c_char, v3: *const c_char, v4: *const c_char, v5: *const c_char, v6: *const c_char, v7: *const c_char, v8: *const c_char, v9: *const c_char, v10: *const c_char, v11: *const c_char, v12: *const c_char, v13: *const c_char, v14: *const c_char, v15: *const c_char, v16: *const c_char, v17: *const c_char, v18: *const c_char, v19: *const c_char, v20: *const c_char, v21: *const c_char, v22: *const c_char, v23: *const c_char, v24: *const c_char, v25: *const c_char, v26: *const c_char, v27: *const c_char, v28: *const c_char, v29: *const c_char, v30: *const c_char) -> c_int {
        guard(b"execl");
        forward_list(p, &[a0, v0, v1, v2, v3, v4, v5, v6, v7, v8, v9, v10, v11, v12, v13, v14, v15, v16, v17, v18, v19, v20, v21, v22, v23, v24, v25, v26, v27, v28, v29, v30], execv)
    }
    #[cfg(target_arch = "x86_64")]
    unsafe extern "C" fn my_execl(p: *const c_char, a0: *const c_char, a1: *const c_char, a2: *const c_char, a3: *const c_char, a4: *const c_char, a5: *const c_char, a6: *const c_char, a7: *const c_char, a8: *const c_char, a9: *const c_char, a10: *const c_char, a11: *const c_char, a12: *const c_char, a13: *const c_char, a14: *const c_char, a15: *const c_char, a16: *const c_char, a17: *const c_char, a18: *const c_char, a19: *const c_char, a20: *const c_char, a21: *const c_char, a22: *const c_char, a23: *const c_char, a24: *const c_char, a25: *const c_char, a26: *const c_char, a27: *const c_char, a28: *const c_char, a29: *const c_char, a30: *const c_char, a31: *const c_char) -> c_int {
        guard(b"execl");
        forward_list(p, &[a0, a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, a12, a13, a14, a15, a16, a17, a18, a19, a20, a21, a22, a23, a24, a25, a26, a27, a28, a29, a30, a31], execv)
    }
    #[cfg(target_arch = "aarch64")]
    unsafe extern "C" fn my_execlp(p: *const c_char, a0: *const c_char, _r2: u64, _r3: u64, _r4: u64, _r5: u64, _r6: u64, _r7: u64, v0: *const c_char, v1: *const c_char, v2: *const c_char, v3: *const c_char, v4: *const c_char, v5: *const c_char, v6: *const c_char, v7: *const c_char, v8: *const c_char, v9: *const c_char, v10: *const c_char, v11: *const c_char, v12: *const c_char, v13: *const c_char, v14: *const c_char, v15: *const c_char, v16: *const c_char, v17: *const c_char, v18: *const c_char, v19: *const c_char, v20: *const c_char, v21: *const c_char, v22: *const c_char, v23: *const c_char, v24: *const c_char, v25: *const c_char, v26: *const c_char, v27: *const c_char, v28: *const c_char, v29: *const c_char, v30: *const c_char) -> c_int {
        guard(b"execlp");
        forward_list(p, &[a0, v0, v1, v2, v3, v4, v5, v6, v7, v8, v9, v10, v11, v12, v13, v14, v15, v16, v17, v18, v19, v20, v21, v22, v23, v24, v25, v26, v27, v28, v29, v30], execvp)
    }
    #[cfg(target_arch = "x86_64")]
    unsafe extern "C" fn my_execlp(p: *const c_char, a0: *const c_char, a1: *const c_char, a2: *const c_char, a3: *const c_char, a4: *const c_char, a5: *const c_char, a6: *const c_char, a7: *const c_char, a8: *const c_char, a9: *const c_char, a10: *const c_char, a11: *const c_char, a12: *const c_char, a13: *const c_char, a14: *const c_char, a15: *const c_char, a16: *const c_char, a17: *const c_char, a18: *const c_char, a19: *const c_char, a20: *const c_char, a21: *const c_char, a22: *const c_char, a23: *const c_char, a24: *const c_char, a25: *const c_char, a26: *const c_char, a27: *const c_char, a28: *const c_char, a29: *const c_char, a30: *const c_char, a31: *const c_char) -> c_int {
        guard(b"execlp");
        forward_list(p, &[a0, a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, a12, a13, a14, a15, a16, a17, a18, a19, a20, a21, a22, a23, a24, a25, a26, a27, a28, a29, a30, a31], execvp)
    }
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
        fn close(fd: c_int) -> c_int;
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
    /// Ruling R42: the crate-export list, read with the real open/read.
    pub unsafe fn read_file(p: *const c_char, buf: *mut u8, cap: size_t) -> Result<usize, &'static [u8]> {
        let fd = real!(c"open", unsafe extern "C" fn(*const c_char, c_int, ...) -> c_int)(p, 0);
        if fd < 0 {
            return Err(b"the crate-export list (TSN_EXPORTS) could not be opened");
        }
        let got = fill(fd, buf, cap, real!(c"read", ReadFn));
        close(fd);
        got
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
    // Ruling R19: reported, then the real exit. `_exit` is NOT hooked: this
    // image's own `_exit(3)`/`_exit(2)` must never recurse.
    #[no_mangle]
    pub unsafe extern "C" fn exit(code: c_int) -> ! {
        let early = exit_guard(b"exit");
        real!(c"exit", unsafe extern "C" fn(c_int) -> !)(if early { EARLY_EXIT_STATUS } else { code })
    }
    #[no_mangle]
    pub unsafe extern "C" fn quick_exit(code: c_int) -> ! {
        let early = exit_guard(b"quick_exit");
        real!(c"quick_exit", unsafe extern "C" fn(c_int) -> !)(if early { EARLY_EXIT_STATUS } else { code })
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
    // Ruling R22(d): glibc's exec family calls its internal __execve, never
    // the exported (hooked) execve, so each entry point is hooked itself.
    hook!(execv, (p: *const c_char, av: *const *const c_char) -> c_int);
    hook!(execvp, (f: *const c_char, av: *const *const c_char) -> c_int);
    hook!(execvpe, (f: *const c_char, av: *const *const c_char, ev: *const *const c_char) -> c_int);
    hook!(fexecve, (fd: c_int, av: *const *const c_char, ev: *const *const c_char) -> c_int);
    // execl/execlp are C-variadic, which stable Rust cannot define. On SysV
    // x86_64 and AAPCS64-on-Linux a variadic argument sits exactly where a
    // fixed one in that position would, so EXEC_SLOTS fixed parameters see
    // the list in order. Slots past the caller's last argument read registers
    // or the caller's own frame, never unmapped memory, and nothing past the
    // NULL is used. The real execv/execvp are not variadic.
    #[no_mangle]
    pub unsafe extern "C" fn execl(p: *const c_char, a0: *const c_char, a1: *const c_char, a2: *const c_char, a3: *const c_char, a4: *const c_char, a5: *const c_char, a6: *const c_char, a7: *const c_char, a8: *const c_char, a9: *const c_char, a10: *const c_char, a11: *const c_char, a12: *const c_char, a13: *const c_char, a14: *const c_char, a15: *const c_char, a16: *const c_char, a17: *const c_char, a18: *const c_char, a19: *const c_char, a20: *const c_char, a21: *const c_char, a22: *const c_char, a23: *const c_char, a24: *const c_char, a25: *const c_char, a26: *const c_char, a27: *const c_char, a28: *const c_char, a29: *const c_char, a30: *const c_char, a31: *const c_char) -> c_int {
        guard(b"execl");
        forward_list(p, &[a0, a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, a12, a13, a14, a15, a16, a17, a18, a19, a20, a21, a22, a23, a24, a25, a26, a27, a28, a29, a30, a31], real!(c"execv", ExecV))
    }
    #[no_mangle]
    pub unsafe extern "C" fn execlp(p: *const c_char, a0: *const c_char, a1: *const c_char, a2: *const c_char, a3: *const c_char, a4: *const c_char, a5: *const c_char, a6: *const c_char, a7: *const c_char, a8: *const c_char, a9: *const c_char, a10: *const c_char, a11: *const c_char, a12: *const c_char, a13: *const c_char, a14: *const c_char, a15: *const c_char, a16: *const c_char, a17: *const c_char, a18: *const c_char, a19: *const c_char, a20: *const c_char, a21: *const c_char, a22: *const c_char, a23: *const c_char, a24: *const c_char, a25: *const c_char, a26: *const c_char, a27: *const c_char, a28: *const c_char, a29: *const c_char, a30: *const c_char, a31: *const c_char) -> c_int {
        guard(b"execlp");
        forward_list(p, &[a0, a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, a12, a13, a14, a15, a16, a17, a18, a19, a20, a21, a22, a23, a24, a25, a26, a27, a28, a29, a30, a31], real!(c"execvp", ExecV))
    }
}
#[cfg(target_os = "linux")]
unsafe fn real_getenv(n: *const c_char) -> *mut c_char {
    plat::real_getenv(n)
}
