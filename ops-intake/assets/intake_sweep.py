"""ops-intake sweep runner: the one module of intake that runs a command (R9).

intake.py imports it only inside `intake sweep`. It runs one argv list from the committed
config, with no shell, and returns what the command printed as untrusted text. It never
reads a command from item content. Stdlib only, Python 3.10+, POSIX.
"""
import os
import subprocess

SWEEP_TIMEOUT = 300           # seconds per command; read at call time, so tests can shorten it
SWEEP_MAX_OUTPUT = 20 * 1024 * 1024  # bytes of stdout kept; more is a failure, not a cut


def run_source(argv, root):
    """Run `argv` in `root` with no shell and no stdin. Returns (text, None) when it exits 0,
    or (None, reason) when it cannot start, times out, exits non-zero, prints too much or
    prints text that is not UTF-8. stderr is dropped: it is untrusted text and intake
    reports only the exit status."""
    try:
        r = subprocess.run(list(argv), cwd=root, shell=False, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=SWEEP_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return None, "timed out after %d seconds" % SWEEP_TIMEOUT
    except OSError as e:
        return None, "cannot start %s: %s" % (os.path.basename(argv[0]),
                                               e.strerror or type(e).__name__)
    if r.returncode != 0:
        return None, "exited %d" % r.returncode
    if len(r.stdout) > SWEEP_MAX_OUTPUT:
        return None, "printed more than %d bytes" % SWEEP_MAX_OUTPUT
    try:
        return r.stdout.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "printed text that is not UTF-8"
