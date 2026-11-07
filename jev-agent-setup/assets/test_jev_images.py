"""Offline tests for jev's --images flag, box questions and image placeholders. Stdlib only.

jev runs under this Python (not through its uv shebang) with a stub `typesafe_sdk` on
PYTHONPATH. The stub records each system_one call in a JSON file and returns fixed answers,
so no test reaches the network or needs a real API key. A missing record file means jev
made no call.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
JEV = HERE / "jev"
HEADER = {"TypeSafe-Danger-Zone-Images": "true"}
IMAGE_BYTES = b"not-really-a-png-0123456789"

STUB = '''
import hashlib, json, os
class Image:
    def __init__(self, data):
        data = bytes(data); self.n, self.sha = len(data), hashlib.sha256(data).hexdigest()
class _A(dict):
    def model_dump(self): return dict(self)
def _enc(o):
    if isinstance(o, Image): return {"IMAGE": o.n, "sha256": o.sha}
    if isinstance(o, dict): return {k: _enc(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_enc(v) for v in o]
    return o
class TypeSafeClient:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def system_one(self, model=None, state=None, questions=None, extra_headers=None, **kw):
        with open(os.environ["JEV_STUB_OUT"], "w") as f:
            json.dump({"headers": dict(extra_headers or {}), "state": _enc(state),
                       "questions": _enc(questions)}, f)
        ans = {}
        for k, q in (questions or {}).items():
            t = q.get("type") if isinstance(q, dict) else getattr(q, "type", None)
            ans[k] = _A(type="box", box={"top": 0.2, "bottom": 0.6, "left": 0.1, "right": 0.5},
                        confidence=0.9) if t == "box" else _A(type="noul", noul=0.9)
        class R: pass
        r = R(); r.answers = ans; return r
'''


class JevImagesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        pkg = self.root / "stub" / "typesafe_sdk"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(STUB)
        (self.root / "home").mkdir()
        (self.root / "img").mkdir()
        self.image = self.root / "img" / "shot.png"
        self.image.write_bytes(IMAGE_BYTES)
        self.out = self.root / "stub-out.json"

    def tearDown(self):
        self._tmp.cleanup()

    def request(self, name, req):
        path = self.root / name
        path.write_text(json.dumps(req))
        return path

    def box_request(self, image_path):
        return {"state": {"shot": {"image": str(image_path)}},
                "questions": {"where": {"type": "box", "instructions": "the red button"}}}

    def run_jev(self, *args):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.root / "home"),
               "JEV_LOG_DIR": str(self.root / "log"), "TYPESAFE_API_KEY": "dummy-not-a-key",
               "PYTHONPATH": str(self.root / "stub"), "JEV_STUB_OUT": str(self.out)}
        return subprocess.run([sys.executable, str(JEV), *map(str, args)], env=env,
                              cwd=str(self.root), capture_output=True, text=True, timeout=60)

    def call(self):
        return json.loads(self.out.read_text())

    # without --images: refused before any call

    def test_box_without_images_exits_2_and_makes_no_call(self):
        r = self.run_jev(self.request("box.json", self.box_request(self.image)))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("--images", r.stderr)
        self.assertFalse(self.out.exists())

    def test_box_question_alone_without_images_exits_2(self):
        req = {"state": {"x": "a page"}, "questions": {"where": {"type": "box"}}}
        r = self.run_jev(self.request("box-only.json", req))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(self.out.exists())

    def test_image_placeholder_in_criteria_without_images_exits_2(self):
        req = {"state": {"x": "a page"},
               "questions": {"q": {"type": "noul", "instructions": "Same?",
                                   "criteria": {"true": {"image": str(self.image)}}}}}
        r = self.run_jev(self.request("crit.json", req))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(self.out.exists())

    # with --images: header and an SDK Image

    def test_box_with_images_sends_header_and_image(self):
        r = self.run_jev("--images", self.request("box.json", self.box_request(self.image)))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["where"]["type"], "box")
        call = self.call()
        self.assertEqual(call["headers"], HEADER)
        shot = call["state"]["shot"]
        self.assertEqual(shot["IMAGE"], len(IMAGE_BYTES))
        self.assertEqual(len(shot["sha256"]), 64)

    def test_flag_after_file_is_accepted(self):
        r = self.run_jev(self.request("box.json", self.box_request(self.image)), "--images")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.call()["headers"], HEADER)

    def test_image_in_criteria_becomes_an_image(self):
        req = {"state": {"x": "a page"},
               "questions": {"q": {"type": "noul", "instructions": "Same?",
                                   "criteria": {"true": {"image": str(self.image)}}}}}
        r = self.run_jev("--images", self.request("crit.json", req))
        self.assertEqual(r.returncode, 0, r.stderr)
        crit = self.call()["questions"]["q"]["criteria"]["true"]
        self.assertEqual(crit["IMAGE"], len(IMAGE_BYTES))

    def test_log_keeps_no_image_bytes(self):
        r = self.run_jev("--images", self.request("box.json", self.box_request(self.image)))
        self.assertEqual(r.returncode, 0, r.stderr)
        logs = list((self.root / "log").rglob("*.jsonl"))
        self.assertTrue(logs)
        for p in logs:
            self.assertNotIn(IMAGE_BYTES.decode(), p.read_text())

    # placeholders that are not regular files: refused before any call

    def assert_refused(self, image_path):
        r = self.run_jev("--images", self.request("bad.json", self.box_request(image_path)))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(self.out.exists())

    def test_directory_placeholder_exits_2(self):
        self.assert_refused(self.root / "img")

    def test_missing_placeholder_exits_2(self):
        self.assert_refused(self.root / "img" / "absent.png")

    def test_symlink_placeholder_exits_2(self):
        link = self.root / "img" / "link.png"
        os.symlink(self.image, link)
        self.assert_refused(link)

    # regression: a plain request is unchanged

    def test_plain_noul_sends_no_header(self):
        req = {"state": {"x": "sky is blue"},
               "questions": {"q": {"type": "noul", "instructions": "Is x true?"}}}
        r = self.run_jev(self.request("noul.json", req))
        self.assertEqual(r.returncode, 0, r.stderr)
        call = self.call()
        self.assertEqual(call["headers"], {})
        self.assertEqual(call["state"], {"x": "sky is blue"})
        self.assertEqual(json.loads(r.stdout), {"q": {"type": "noul", "noul": 0.9}})


try:
    from typesafe_sdk import Image as RealImage  # the real SDK, when this Python has it
except ImportError:
    RealImage = None


class RealSdkImageTest(unittest.TestCase):
    @unittest.skipIf(RealImage is None, "the real typesafe_sdk is not importable in this Python")
    def test_real_image_takes_bytes_and_has_no_from_bytes(self):
        self.assertIsNotNone(RealImage(b"x"))
        self.assertFalse(hasattr(RealImage, "from_bytes"))


if __name__ == "__main__":
    unittest.main()
