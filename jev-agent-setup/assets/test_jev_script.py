"""Offline tests for the jev script's shebang and PEP 723 header. Stdlib only.

The shebang must make uv ignore every config file, so a repo cannot pick the package
index, and the dependency must take the latest typesafe-sdk from 0.7.5 on. When uv is
installed, the test asks uv for its resolved settings with the shebang's own words, from
a folder whose uv.toml names a hostile index, and checks that uv did not read it. That
call runs no script and resolves no dependency.
"""
import contextlib
import io
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
JEV = HERE / "jev"
SHEBANG = "#!/usr/bin/env -S uv run --quiet --no-config --script"
DEPENDENCY = '# dependencies = ["typesafe-sdk>=0.7.5"]'
HOSTILE = "index.invalid"


def head(path, n=4):
    with open(path, encoding="utf-8") as f:
        return [f.readline().rstrip("\n") for _ in range(n)]


def uv_words(shebang_line):
    """The words after `uv` in the shebang, without --quiet (it hides --show-settings)."""
    words = shlex.split(shebang_line[2:])
    return [w for w in words[words.index("uv") + 1:] if w not in ("--quiet", "-q")]


class HeaderTest(unittest.TestCase):
    def test_shebang_is_exact(self):
        self.assertEqual(head(JEV)[0], SHEBANG)

    def test_dependency_is_exact_with_no_upper_bound(self):
        lines = head(JEV)
        self.assertEqual(lines[1], "# /// script")
        self.assertEqual(lines[3], DEPENDENCY)
        self.assertNotIn("<", lines[3])
        self.assertNotIn(",", lines[3])

    def test_uv_words_disable_config(self):
        self.assertEqual(uv_words(head(JEV)[0]), ["run", "--no-config", "--script"])

    def test_script_is_executable(self):
        self.assertTrue(os.access(JEV, os.X_OK))


class InstallerCopyTest(unittest.TestCase):
    def test_installer_copies_the_same_first_lines(self):
        sys.path.insert(0, str(HERE))
        import install_jev_setup as inst

        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = inst.main(["--home", tmp], env={"PATH": "/usr/bin:/bin"})
            self.assertEqual(code, 0)
            installed = Path(tmp) / ".local/bin/jev"
            self.assertEqual(head(installed), head(JEV))
            self.assertEqual(installed.read_bytes(), JEV.read_bytes())


@unittest.skipUnless(shutil.which("uv"), "uv is not installed")
class UvIgnoresConfigTest(unittest.TestCase):
    def test_hostile_uv_toml_is_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "cfg"
            for d in ("cfg", "home", "xdg", "cache"):
                (root / d).mkdir()
            (cfg / "uv.toml").write_text(f'index-url = "https://{HOSTILE}/simple"\n')
            env = {k: v for k, v in os.environ.items()
                   if k not in ("UV_CONFIG_FILE", "UV_INDEX_URL", "UV_DEFAULT_INDEX",
                                "UV_EXTRA_INDEX_URL", "UV_INDEX")}
            env.update(HOME=str(root / "home"), XDG_CONFIG_HOME=str(root / "xdg"),
                       UV_OFFLINE="1", UV_CACHE_DIR=str(root / "cache"))
            cmd = ["uv", *uv_words(head(JEV)[0]), "--show-settings", str(JEV)]
            proc = subprocess.run(cmd, cwd=cfg, env=env, capture_output=True, text=True,
                                  timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Settings", proc.stdout)
            self.assertNotIn(HOSTILE, proc.stdout + proc.stderr)

    def test_probe_sees_the_index_without_no_config(self):
        """Negative control: the same probe without --no-config does read uv.toml."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "cfg"
            for d in ("cfg", "home", "xdg", "cache"):
                (root / d).mkdir()
            (cfg / "uv.toml").write_text(f'index-url = "https://{HOSTILE}/simple"\n')
            env = dict(os.environ, HOME=str(root / "home"), XDG_CONFIG_HOME=str(root / "xdg"),
                       UV_OFFLINE="1", UV_CACHE_DIR=str(root / "cache"))
            words = [w for w in uv_words(head(JEV)[0]) if w != "--no-config"]
            proc = subprocess.run(["uv", *words, "--show-settings", str(JEV)], cwd=cfg,
                                  env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(HOSTILE, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
