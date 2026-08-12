#!/usr/bin/env python3
"""Engine for an OKF capability catalog: parsing, the readiness graph, the
dependency state machine, audit findings, and commit-boundary enforcement.

The catalog is an Open Knowledge Format (OKF v0.1) bundle — plain markdown
with YAML frontmatter in git (spec: https://github.com/GoogleCloudPlatform/
knowledge-catalog/blob/main/okf/SPEC.md). This module holds every rule that
must be *computed* rather than typed by a human:

  * readiness is a grid, not a boolean — code maturity (where the code is)
    and deployment reality (per environment) are independent axes;
  * each readiness state lives in a document owned by the party entitled to
    assert it (provider -> Capability, consumer -> Verification, machine ->
    Signal), so effective readiness is derived across all three and can
    never be left behind as a stale green tick;
  * effective readiness is the minimum over a capability's hard-requires
    closure, and it carries a *depth* qualifier so "nobody has looked" never
    renders as "fine";
  * a dependency trips automatically when its point of no return passes
    unconfirmed, and trips propagate downstream without a human relaying it.

Stdlib only, no network, deterministic: every time-dependent entry point
takes an explicit `today`/`now`. The CLI that drives this module is
okf_catalog.py.
"""
from __future__ import annotations

import datetime as _dt
import os
import re

OKF_SPEC_VERSION = "0.1"
SCANNER_VERSION = "okf-capability-catalog/0.1.0"

# index.md/log.md are OKF-reserved; README.md/CONTRIBUTING.md are human-facing
# bundle furniture rather than concepts, so they carry no frontmatter and are
# not read as documents.
RESERVED = ("index.md", "log.md", "README.md", "CONTRIBUTING.md")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".github"}

# Deployment-reality axis, lowest to highest.
READINESS_ORDER = ("unknown", "provider_tested", "consumer_verified", "production_live")
# The only states a provider may assert in its own Capability document.
PROVIDER_ASSERTABLE = ("unknown", "provider_tested")
# Code-maturity axis.
CODE_MATURITY = ("feature", "integration", "release")

DEPENDENCY_STATES = (
    "detected", "proposed", "acknowledged", "at_risk", "tripped",
    "satisfied", "fallback_invoked", "renegotiated",
)
# States that represent a managed commitment (an edge someone stands behind).
MANAGED_STATES = ("acknowledged", "at_risk", "tripped", "satisfied",
                  "fallback_invoked", "renegotiated")
TERMINAL_STATES = ("satisfied", "fallback_invoked", "renegotiated")


# ─────────────────────────────────────────────────────────────────────────────
# YAML subset: parser and emitter
#
# Catalog documents are written by this tool and read back by it, by humans,
# and by other agents. That needs a real (if small) YAML: nested maps, lists
# of maps, block scalars, inline lists. Stdlib-only is a house rule, so this
# is a hand-rolled subset covering exactly the shapes the schemas use. The
# parser is tolerant by contract (OKF's consumer rule) and never raises; the
# emitter is canonical so re-running a scan on unchanged input is a no-op.
# ─────────────────────────────────────────────────────────────────────────────

_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")
_PLAIN_SAFE_RE = re.compile(r"^[A-Za-z0-9_/][A-Za-z0-9 _./:@+()'&,\-]*$")
# `- key: value` starts a mapping only when the colon is followed by space or
# end of line. Without this, `- https://example/x` parses as {https: //example/x}.
_ITEM_KEY_RE = re.compile(r"^([^\s:'\"]+):(\s|$)")


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _scalar(raw):
    """Convert a YAML scalar token to a Python value. ISO dates stay strings."""
    if raw is None:
        return None
    v = raw.strip()
    if v == "" or v in ("null", "~"):
        return None
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    low = v.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if _NUM_RE.match(v):
        return int(v) if "." not in v else float(v)
    return v


def _strip_comment(rest: str) -> str:
    """Drop a trailing ` # comment` from an unquoted scalar."""
    if not rest or rest[0] in "\"'":
        return rest
    idx = rest.find(" #")
    return rest[:idx].rstrip() if idx >= 0 else rest


def _inline_split(text: str):
    """Split `a, b, {k: v}` on top-level commas only."""
    parts, depth, cur, quote = [], 0, "", ""
    for ch in text:
        if quote:
            cur += ch
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            cur += ch
        elif ch in "[{":
            depth += 1
            cur += ch
        elif ch in "]}":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    return [p.strip() for p in parts]


def _inline_value(text: str):
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_inline_value(p) for p in _inline_split(inner)] if inner else []
    if text.startswith("{") and text.endswith("}"):
        inner = text[1:-1].strip()
        out = {}
        for part in _inline_split(inner):
            k, sep, v = part.partition(":")
            if sep:
                out[k.strip()] = _inline_value(v)
        return out
    return _scalar(text)


def _significant(lines, i, end):
    """Index of the next line that is neither blank nor a comment."""
    while i < end:
        s = lines[i].strip()
        if s and not s.startswith("#"):
            return i
        i += 1
    return end


def _parse_block(lines, i, end, indent):
    """Parse a mapping or sequence at `indent`. Returns (value, next_index)."""
    j = _significant(lines, i, end)
    if j >= end or _indent_of(lines[j]) < indent:
        return None, i
    if lines[j].lstrip().startswith("- "):
        return _parse_seq(lines, j, end, _indent_of(lines[j]))
    return _parse_map(lines, j, end, _indent_of(lines[j]))


def _parse_map(lines, i, end, indent):
    out = {}
    while True:
        i = _significant(lines, i, end)
        if i >= end:
            break
        line = lines[i]
        if _indent_of(line) != indent or line.lstrip().startswith("- "):
            break
        key, sep, rest = line.strip().partition(":")
        if not sep:
            i += 1
            continue
        key, rest = key.strip(), _strip_comment(rest.strip())
        value, i = _parse_value(lines, i, end, indent, rest)
        out[key] = value
    return out, i


def _parse_seq(lines, i, end, indent):
    items = []
    while True:
        i = _significant(lines, i, end)
        if i >= end:
            break
        line = lines[i]
        if _indent_of(line) != indent or not line.lstrip().startswith("- "):
            break
        entry = line.strip()[2:].strip()
        if entry == "":
            value, i = _parse_block(lines, i + 1, end, indent + 1)
            items.append(value)
            continue
        key, sep, rest = entry.partition(":")
        if _ITEM_KEY_RE.match(entry):
            # `- key: value` starts a mapping whose remaining keys are indented
            # to the column the key sits in.
            child_indent = indent + 2
            head = {}
            value, i = _parse_value(lines, i, end, child_indent, _strip_comment(rest.strip()))
            head[key.strip()] = value
            rest_map, i = _parse_map(lines, i, end, child_indent)
            head.update(rest_map)
            items.append(head)
        else:
            items.append(_inline_value(entry))
            i += 1
    return items, i


def _parse_value(lines, i, end, indent, rest):
    """Value for a `key:` at line i. Returns (value, next_index)."""
    if rest in ("|", ">", "|-", ">-", "|+", ">+"):
        block, i = [], i + 1
        while i < end and (not lines[i].strip() or _indent_of(lines[i]) > indent):
            block.append(lines[i].strip())
            i += 1
        joiner = "\n" if rest.startswith("|") else " "
        return joiner.join(block).strip(), i
    if rest == "":
        nxt = _significant(lines, i + 1, end)
        if nxt < end and _indent_of(lines[nxt]) > indent:
            return _parse_block(lines, i + 1, end, _indent_of(lines[nxt]))
        if nxt < end and _indent_of(lines[nxt]) == indent and lines[nxt].lstrip().startswith("- "):
            return _parse_seq(lines, nxt, end, indent)
        return None, i + 1
    if rest.startswith(("[", "{")):
        return _inline_value(rest), i + 1
    # Plain scalar, possibly folded across more-indented continuation lines.
    value, i = rest, i + 1
    cont = []
    while (i < end and lines[i].strip() and _indent_of(lines[i]) > indent
           and not lines[i].lstrip().startswith("- ")
           and ":" not in lines[i].split("#")[0]):
        cont.append(lines[i].strip())
        i += 1
    if cont:
        return " ".join([value] + cont), i
    return _scalar(value), i


def parse_yaml(text: str) -> dict:
    """Parse a YAML-subset document into a dict. Never raises."""
    lines = (text or "").replace("\t", "  ").split("\n")
    try:
        value, _ = _parse_map(lines, 0, len(lines), 0)
        return value or {}
    except Exception:
        return {"_parse_error": True}


def parse_frontmatter(text: str):
    """Return (meta, body, had_frontmatter). Never raises."""
    lines = (text or "").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text or "", False
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() in ("---", "..."):
            end = idx
            break
    if end is None:
        return {}, text or "", False
    meta = parse_yaml("\n".join(lines[1:end]))
    return meta, "\n".join(lines[end + 1:]).lstrip("\n"), True


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _emit_scalar(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if s == "" or s != s.strip() or not _PLAIN_SAFE_RE.match(s) or ": " in s or s.endswith(":"):
        return _quote(s)
    if s.lower() in ("true", "false", "yes", "no", "null", "~") or _NUM_RE.match(s):
        return _quote(s)
    return s


def _wrap(text: str, width: int = 76):
    words, out, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        out.append(cur)
    return out


def dump_yaml(value, indent: int = 0) -> str:
    """Emit the YAML subset canonically. Round-trips through parse_yaml()."""
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return f"{pad}{{}}\n"
        out = []
        for k, v in value.items():
            if isinstance(v, dict) and v:
                out.append(f"{pad}{k}:\n" + dump_yaml(v, indent + 2))
            elif isinstance(v, list) and v:
                out.append(f"{pad}{k}:\n" + dump_yaml(v, indent))
            elif isinstance(v, list):
                out.append(f"{pad}{k}: []\n")
            elif isinstance(v, str) and ("\n" in v or len(v) > 80):
                out.append(_emit_block(pad, k, v))
            else:
                out.append(f"{pad}{k}: {_emit_scalar(v)}\n")
        return "".join(out)
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                inner = dump_yaml(item, indent + 2)
                first, _, rest = inner.partition("\n")
                out.append(f"{pad}- {first.strip()}\n" + (rest if rest.strip() else ""))
            else:
                out.append(f"{pad}- {_emit_scalar(item)}\n")
        return "".join(out)
    return f"{pad}{_emit_scalar(value)}\n"


def _emit_block(pad: str, key: str, text: str) -> str:
    """Long/multi-line strings as block scalars: `|-` keeps newlines, `>-` folds."""
    if "\n" in text:
        lines = [f"{pad}  {ln}".rstrip() for ln in text.split("\n")]
        return f"{pad}{key}: |-\n" + "\n".join(lines) + "\n"
    lines = [f"{pad}  {ln}" for ln in _wrap(" ".join(text.split()))]
    return f"{pad}{key}: >-\n" + "\n".join(lines) + "\n"


# Field order per document type — generated files then read like the schema,
# and diffs stay stable across re-scans.
KEY_ORDER = {
    "Team": ["type", "title", "description", "team_id", "provenance", "claimed_by",
             "claimed_at", "contacts", "repositories", "provides", "timestamp"],
    "Capability": ["type", "title", "description", "capability_id", "owning_team",
                   "lifecycle", "inputs", "outputs", "constraints", "fulfilled_by",
                   "requires", "upstream", "code_maturity", "readiness",
                   "drift_detected", "timestamp"],
    "Service": ["type", "title", "description", "service_id", "owning_team",
                "repository", "fulfils", "runtimes", "interfaces", "scan", "timestamp"],
    "Dependency": ["type", "title", "dependency_id", "consumer_team", "provider_team",
                   "capability", "target_environment", "requested_date", "promised_date",
                   "consequence_if_late", "fallback", "no_fallback_rationale",
                   "point_of_no_return", "acknowledgement", "consumer_reconfirmation_required",
                   "on_track", "risk_note", "decision", "depends_on", "detected_via",
                   "state", "timestamp"],
    "Verification": ["type", "title", "capability", "verifying_team", "environment",
                     "result", "kind", "environment_resolved_from", "scope", "evidence",
                     "verified_by", "verified_at", "commit_sha", "expires_after_days"],
    "Signal": ["type", "title", "capability", "environment", "observation", "window",
               "detail", "emitted_by", "emitted_at"],
}


def render_doc(meta: dict, body: str = "") -> str:
    """Render a concept document: ordered frontmatter + markdown body."""
    order = KEY_ORDER.get(meta.get("type"), [])
    ordered = {}
    for key in order:
        if key in meta and meta[key] is not None:
            ordered[key] = meta[key]
    for key in sorted(k for k in meta if k not in ordered):
        if meta[key] is not None:
            ordered[key] = meta[key]
    text = "---\n" + dump_yaml(ordered) + "---\n"
    body = (body or "").strip()
    return text + ("\n" + body + "\n" if body else "")


# ─────────────────────────────────────────────────────────────────────────────
# Time helpers — every entry point takes an explicit clock so the whole
# engine is deterministic under test.
# ─────────────────────────────────────────────────────────────────────────────

def parse_date(value):
    """Accept a date or ISO timestamp; return a date, or None."""
    if value is None:
        return None
    if isinstance(value, _dt.date) and not isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.datetime):
        return value.date()
    text = str(value).strip().strip('"')
    if not text:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if not m:
        return None
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_ts(value):
    """Accept an ISO-8601 timestamp (Z or offset); return a UTC datetime."""
    if value is None:
        return None
    text = str(value).strip().strip('"')
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?", text)
    if not m:
        d = parse_date(text)
        return _dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone.utc) if d else None
    date = parse_date(m.group(1))
    return _dt.datetime(date.year, date.month, date.day, int(m.group(2)),
                        int(m.group(3)), int(m.group(4) or 0), tzinfo=_dt.timezone.utc)


def iso(ts: _dt.datetime) -> str:
    return ts.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    if not slug:
        return "item"
    # A concept file named index.md or log.md is reserved by OKF and would be
    # read as bundle furniture — i.e. it would silently not exist.
    if f"{slug}.md" in RESERVED:
        return f"{slug}-capability"
    return slug


def norm_link(link: str) -> str:
    """Bundle-absolute or relative doc link -> bundle-relative path."""
    if not link:
        return ""
    return str(link).strip().strip('"').lstrip("/")


# ─────────────────────────────────────────────────────────────────────────────
# Catalog model
# ─────────────────────────────────────────────────────────────────────────────

class Doc:
    def __init__(self, relpath, meta, body, raw=""):
        self.relpath = relpath
        self.meta = meta or {}
        self.body = body or ""
        self.raw = raw

    @property
    def type(self):
        return self.meta.get("type")

    def get(self, key, default=None):
        value = self.meta.get(key, default)
        return default if value is None else value


DEFAULT_CONFIG = {
    "okf_version": OKF_SPEC_VERSION,
    "organization": "acme",
    "branches": {"integration": "develop", "release": "main"},
    "environments": [{"name": "dev"}, {"name": "staging"},
                     {"name": "production", "requires_live_signal": True}],
    "capability_id_scheme": "<team>/<slug>",
    "default_fallback_execution_days": 2,
    "on_track_window_days": 7,
    "ponr_warning_days": 7,
    "stale_assertion_days": 90,
}


class Catalog:
    """A loaded OKF capability-catalog bundle."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.config = dict(DEFAULT_CONFIG)
        self.docs = {}          # relpath -> Doc
        self.warnings = []
        self.unparsed = []      # relpaths whose frontmatter is missing/unusable

    # ── loading ──────────────────────────────────────────────────────────────
    @classmethod
    def load(cls, root):
        cat = cls(root)
        cfg_path = os.path.join(cat.root, "catalog.config.yaml")
        if os.path.isfile(cfg_path):
            with open(cfg_path, encoding="utf-8") as fh:
                parsed = parse_yaml(fh.read())
            if isinstance(parsed, dict) and parsed:
                merged = dict(DEFAULT_CONFIG)
                merged.update({k: v for k, v in parsed.items() if v is not None})
                cat.config = merged
        else:
            cat.warnings.append("catalog.config.yaml not found — using defaults")
        for dirpath, dirnames, filenames in os.walk(cat.root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                if not name.endswith(".md"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, name), cat.root).replace(os.sep, "/")
                if name in RESERVED:
                    continue
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    raw = fh.read()
                meta, body, had = parse_frontmatter(raw)
                if not had or not meta.get("type"):
                    cat.unparsed.append(rel)
                    continue
                cat.docs[rel] = Doc(rel, meta, body, raw)
        return cat

    # ── indexes ──────────────────────────────────────────────────────────────
    def by_type(self, doc_type):
        return [d for _, d in sorted(self.docs.items()) if d.type == doc_type]

    @property
    def teams(self):
        return {d.get("team_id"): d for d in self.by_type("Team") if d.get("team_id")}

    @property
    def capabilities(self):
        return {d.relpath: d for d in self.by_type("Capability")}

    @property
    def services(self):
        return {d.relpath: d for d in self.by_type("Service")}

    @property
    def dependencies(self):
        return {d.get("dependency_id", d.relpath): d for d in self.by_type("Dependency")}

    @property
    def verifications(self):
        return self.by_type("Verification")

    @property
    def signals(self):
        return self.by_type("Signal")

    def environments(self):
        out = []
        for env in self.config.get("environments") or []:
            out.append(env if isinstance(env, dict) else {"name": env})
        return out

    def env_names(self):
        return [e.get("name") for e in self.environments() if e.get("name")]

    def env_config(self, name):
        for env in self.environments():
            if env.get("name") == name:
                return env
        return {}

    def capability_by_id(self, cap_id):
        for rel, doc in sorted(self.capabilities.items()):
            if doc.get("capability_id") == cap_id:
                return doc
        return None

    def resolve_capability(self, ref):
        """Resolve a capability by id or by bundle path."""
        if not ref:
            return None
        rel = norm_link(ref)
        if rel in self.docs and self.docs[rel].type == "Capability":
            return self.docs[rel]
        return self.capability_by_id(str(ref).strip())

    def team_of_doc(self, doc):
        """The team_id that owns a document, from its path or owning_team link."""
        owner = norm_link(doc.get("owning_team") or doc.get("verifying_team") or "")
        for source in (owner, doc.relpath):
            m = re.match(r"^teams/([^/]+)/", source or "")
            if m:
                return m.group(1)
        return None

    def team_doc(self, team_id):
        return self.teams.get(team_id)

    def team_is_claimed(self, team_id):
        doc = self.team_doc(team_id)
        return bool(doc) and doc.get("provenance") != "stub"


# ─────────────────────────────────────────────────────────────────────────────
# Readiness
# ─────────────────────────────────────────────────────────────────────────────

def readiness_rank(state):
    try:
        return READINESS_ORDER.index(state)
    except ValueError:
        return 0


def readiness_min(a, b):
    return a if readiness_rank(a) <= readiness_rank(b) else b


class Readiness:
    """The answer a consumer needs: a state AND how much is actually known."""

    def __init__(self, state, depth, notes=None, cycle=None):
        self.state = state
        self.depth = depth          # complete | unknown
        self.notes = notes or []
        self.cycle = cycle or []

    @property
    def verdict(self):
        # Partial information presented without its own limits manufactures
        # confidence — the EKS lesson generalised. `unknown` is never green.
        if self.depth != "complete":
            return "unknown"
        return "green" if readiness_rank(self.state) >= readiness_rank("consumer_verified") else "not_ready"

    def __repr__(self):
        return f"<Readiness {self.state}/{self.depth}/{self.verdict}>"


def verification_active(ver, today):
    """A Verification counts until it expires. Expiry is opt-in per document."""
    days = ver.get("expires_after_days")
    when = parse_date(ver.get("verified_at"))
    if not days or not when:
        return True
    try:
        return today <= when + _dt.timedelta(days=int(days))
    except (TypeError, ValueError):
        return True


def own_readiness(cat, cap, env, today):
    """Readiness of one capability in one environment, derived across the three
    owners: provider (Capability), consumer (Verification), machine (Signal).

    Provider assertions are clamped to `provider_tested` — anything higher in a
    provider-owned file is a policy violation, reported by audit and ignored here.
    """
    state, notes = "unknown", []
    for entry in cap.get("readiness") or []:
        if not isinstance(entry, dict) or entry.get("environment") != env:
            continue
        claimed = entry.get("state") or "unknown"
        if claimed not in PROVIDER_ASSERTABLE:
            notes.append(f"ignored provider-asserted '{claimed}' in {cap.relpath} "
                         "(only the consumer or a machine may assert that)")
            claimed = "provider_tested" if claimed else "unknown"
        state = max(state, claimed, key=readiness_rank)

    failed = False
    for ver in cat.verifications:
        if norm_link(ver.get("capability")) != cap.relpath or ver.get("environment") != env:
            continue
        if not verification_active(ver, today):
            notes.append(f"expired verification {ver.relpath}")
            continue
        # A contract test proves the shape of the exchange, not the wiring. It
        # is evidence, never readiness — the incident's contract was fine.
        if ver.get("kind") == "contract":
            notes.append(f"contract test {ver.relpath} recorded as evidence only")
            continue
        if ver.get("result") == "verified":
            state = max(state, "consumer_verified", key=readiness_rank)
        elif ver.get("result") == "failed":
            failed = True
            notes.append(f"failed verification {ver.relpath} by {ver.get('verifying_team')}")
        else:
            notes.append(f"partial verification {ver.relpath} does not raise readiness")

    for sig in cat.signals:
        if norm_link(sig.get("capability")) != cap.relpath or sig.get("environment") != env:
            continue
        if sig.get("observation") == "traffic":
            state = max(state, "production_live", key=readiness_rank)

    if failed:
        # Someone tried it and it did not work: not consumable, whatever else
        # the graph says.
        state = readiness_min(state, "provider_tested")
    return state, notes


def hard_requires(cap):
    out = []
    for req in cap.get("requires") or []:
        if isinstance(req, dict) and (req.get("criticality") or "hard") == "hard":
            out.append(norm_link(req.get("capability")))
    return [r for r in out if r]


def soft_requires(cap):
    out = []
    for req in cap.get("requires") or []:
        if isinstance(req, dict) and req.get("criticality") == "soft":
            out.append(norm_link(req.get("capability")))
    return [r for r in out if r]


def effective_readiness(cat, cap, env, today, _stack=None):
    """min over the capability and its hard-requires closure, plus a depth.

    Depth is `complete` only when every capability in that closure has
    attested its own upstreams and resolves to a claimed team. Anything else
    is `unknown` — which must never render as green.
    """
    _stack = _stack or []
    if cap.relpath in _stack:
        cycle = _stack[_stack.index(cap.relpath):] + [cap.relpath]
        state, notes = own_readiness(cat, cap, env, today)
        return Readiness(state, "unknown", notes + ["cycle in hard requires"], cycle)

    state, notes = own_readiness(cat, cap, env, today)
    depth = "complete"
    cycle = []
    upstream = (cap.get("upstream") or {}) if isinstance(cap.get("upstream"), dict) else {}
    if upstream.get("attested") is not True:
        depth = "unknown"
        notes.append(f"{cap.get('capability_id') or cap.relpath}: upstreams not attested "
                     "— absence of declared requires proves nothing")

    for rel in hard_requires(cap):
        target = cat.docs.get(rel)
        if target is None or target.type != "Capability":
            depth = "unknown"
            state = readiness_min(state, "unknown")
            notes.append(f"hard upstream {rel} is missing from the catalog")
            continue
        owner = cat.team_of_doc(target)
        if owner and not cat.team_is_claimed(owner):
            depth = "unknown"
            notes.append(f"hard upstream {rel} belongs to stub team '{owner}'")
        sub = effective_readiness(cat, target, env, today, _stack + [cap.relpath])
        state = readiness_min(state, sub.state)
        if sub.depth != "complete":
            depth = "unknown"
        notes.extend(sub.notes)
        cycle = cycle or sub.cycle

    for rel in soft_requires(cap):
        target = cat.docs.get(rel)
        if target is not None:
            sub = effective_readiness(cat, target, env, today, _stack + [cap.relpath])
            if readiness_rank(sub.state) < readiness_rank("consumer_verified"):
                notes.append(f"soft upstream {rel} at {sub.state} — degradation risk")
    return Readiness(state, depth, notes, cycle)


def find_cycles(cat):
    """All hard-requires cycles, as lists of capability paths. Cycle-safe."""
    cycles, seen = [], set()

    def walk(rel, stack):
        if rel in stack:
            cycle = stack[stack.index(rel):] + [rel]
            key = tuple(sorted(set(cycle)))
            if key not in seen:
                seen.add(key)
                cycles.append(cycle)
            return
        cap = cat.docs.get(rel)
        if cap is None or cap.type != "Capability":
            return
        for nxt in hard_requires(cap):
            walk(nxt, stack + [rel])

    for rel in sorted(cat.capabilities):
        walk(rel, [])
    return cycles


def closure(cat, cap, _stack=None):
    """The capability plus its hard-requires closure (cycle-safe)."""
    _stack = _stack or []
    if cap.relpath in _stack:
        return []
    out = [cap.relpath]
    for rel in hard_requires(cap):
        target = cat.docs.get(rel)
        if target is None or target.type != "Capability":
            out.append(rel)
            continue
        out.extend(closure(cat, target, _stack + [cap.relpath]))
    seen, ordered = set(), []
    for rel in out:
        if rel not in seen:
            seen.add(rel)
            ordered.append(rel)
    return ordered


def closure_coverage(cat, team_id):
    """Adoption metric: what fraction of a team's hard closure is actually known.

    A green board before coverage is high is a lie, so this is the number to
    watch during rollout — not the count of green edges.
    """
    total, known = 0, 0
    for dep in cat.by_type("Dependency"):
        if norm_link(dep.get("consumer_team")) != f"teams/{team_id}/team.md":
            continue
        cap = cat.resolve_capability(dep.get("capability"))
        if cap is None:
            total += 1
            continue
        for rel in closure(cat, cap):
            total += 1
            target = cat.docs.get(rel)
            if target is None or target.type != "Capability":
                continue
            owner = cat.team_of_doc(target)
            upstream = target.get("upstream") or {}
            if (cat.team_is_claimed(owner) and isinstance(upstream, dict)
                    and upstream.get("attested") is True):
                known += 1
    return known, total


# ─────────────────────────────────────────────────────────────────────────────
# Dependency state machine
# ─────────────────────────────────────────────────────────────────────────────

def point_of_no_return(dep, config=None):
    """promised_date − fallback.execution_days. Backwards from the deadline."""
    promised = parse_date(dep.get("promised_date"))
    if promised is None:
        return None
    fallback = dep.get("fallback") if isinstance(dep.get("fallback"), dict) else {}
    days = fallback.get("execution_days")
    if days is None:
        days = (config or {}).get("default_fallback_execution_days",
                                  DEFAULT_CONFIG["default_fallback_execution_days"])
    try:
        return promised - _dt.timedelta(days=int(days))
    except (TypeError, ValueError):
        return None


def uses_default_fallback_days(dep):
    fallback = dep.get("fallback") if isinstance(dep.get("fallback"), dict) else {}
    return dep.get("promised_date") is not None and fallback.get("execution_days") is None


def on_track_valid(dep, today, config):
    """A confirmation counts only inside the window before the PONR — a promise
    made a month ago is not a check-in."""
    track = dep.get("on_track") if isinstance(dep.get("on_track"), dict) else {}
    when = parse_date(track.get("confirmed_at"))
    if when is None:
        return False
    ponr = point_of_no_return(dep, config)
    if ponr is None:
        return True
    window = int(config.get("on_track_window_days", DEFAULT_CONFIG["on_track_window_days"]))
    return when >= ponr - _dt.timedelta(days=window)


def upstream_edges(cat, dep):
    """Edges this one rests on: declared `depends_on`, plus the edges managing
    the hard upstreams of the capability being depended on."""
    out, deps = [], cat.dependencies
    for dep_id in dep.get("depends_on") or []:
        if dep_id in deps:
            out.append(deps[dep_id])
    cap = cat.resolve_capability(dep.get("capability"))
    if cap is not None:
        upstream_caps = set(hard_requires(cap))
        for other in cat.by_type("Dependency"):
            if other.relpath == dep.relpath:
                continue
            if norm_link(other.get("capability")) in upstream_caps:
                out.append(other)
    seen, ordered = set(), []
    for edge in out:
        if edge.relpath not in seen:
            seen.add(edge.relpath)
            ordered.append(edge)
    return ordered


class EdgeState:
    def __init__(self, state, reason="", originator=None):
        self.state = state
        self.reason = reason
        self.originator = originator

    def __repr__(self):
        return f"<EdgeState {self.state}{' via ' + self.originator if self.originator else ''}>"


def effective_state(cat, dep, today, _stack=None):
    """Derived edge state: stored state, plus the two transitions no human
    performs — the automatic trip at the point of no return, and propagation
    of an upstream trip downstream."""
    _stack = _stack or []
    stored = dep.get("state") or "detected"
    if dep.relpath in _stack:
        return EdgeState(stored, "cycle in dependency graph")
    if stored in TERMINAL_STATES or stored in ("detected", "proposed"):
        return EdgeState(stored)

    ponr = point_of_no_return(dep, cat.config)
    if ponr is not None and today > ponr and not on_track_valid(dep, today, cat.config):
        return EdgeState("tripped",
                         f"point of no return {ponr.isoformat()} passed without an "
                         "on-track confirmation")

    for upstream in upstream_edges(cat, dep):
        sub = effective_state(cat, upstream, today, _stack + [dep.relpath])
        if sub.state in ("tripped", "at_risk"):
            origin = sub.originator or upstream.get("dependency_id", upstream.relpath)
            return EdgeState("at_risk", f"upstream edge {origin} is {sub.state}", origin)

    if stored == "at_risk":
        return EdgeState("at_risk", dep.get("risk_note") or "flagged by the provider")
    return EdgeState(stored)


def can_satisfy(cat, dep, today):
    """An edge may only be closed as satisfied when the thing actually works
    for the consumer — effective readiness, not the capability's own claim."""
    cap = cat.resolve_capability(dep.get("capability"))
    if cap is None:
        return False, "capability not found in the catalog"
    env = dep.get("target_environment")
    ready = effective_readiness(cat, cap, env, today)
    if readiness_rank(ready.state) < readiness_rank("consumer_verified"):
        return False, (f"effective readiness in {env} is '{ready.state}' — needs "
                       "consumer_verified or better")
    if ready.depth != "complete":
        return False, (f"effective readiness is '{ready.state}' but depth is "
                       "'unknown' — part of the hard closure is unmapped")
    return True, "effective readiness is consumer_verified or better, depth complete"


def impossible_promise(cat, dep):
    """A promise the graph says cannot happen: an upstream is promised later
    than this edge's own promised date. Arithmetic, not intuition."""
    promised = parse_date(dep.get("promised_date"))
    if promised is None:
        return []
    cap = cat.resolve_capability(dep.get("capability"))
    if cap is None:
        return []
    upstream_caps = set(hard_requires(cap))
    out = []
    for other in cat.by_type("Dependency"):
        if other.relpath == dep.relpath:
            continue
        if norm_link(other.get("capability")) not in upstream_caps:
            continue
        other_promised = parse_date(other.get("promised_date"))
        if other_promised and other_promised > promised:
            out.append((other, other_promised))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Audit findings
# ─────────────────────────────────────────────────────────────────────────────

class Finding:
    def __init__(self, code, severity, path, message):
        self.code = code
        self.severity = severity        # high | medium | low
        self.path = path
        self.message = message

    def line(self):
        return f"FINDING: {self.code} [{self.severity}] {self.path} — {self.message}"


_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def audit(cat, today, ponr_days=None):
    """Seams at risk: one meaningful line per boundary, not a status dump."""
    findings = []
    add = lambda *a: findings.append(Finding(*a))  # noqa: E731
    ponr_days = int(ponr_days if ponr_days is not None
                    else cat.config.get("ponr_warning_days", 7))
    stale_days = int(cat.config.get("stale_assertion_days", 90))

    deps = cat.by_type("Dependency")
    for dep in deps:
        state = effective_state(cat, dep, today)
        dep_id = dep.get("dependency_id", dep.relpath)
        env = dep.get("target_environment")
        cap = cat.resolve_capability(dep.get("capability"))

        if state.state == "tripped" and not dep.get("decision"):
            who = ((dep.get("acknowledgement") or {}).get("provider") or {}).get("by")
            add("CC-TRIPPED", "high", dep.relpath,
                f"{dep_id} tripped ({state.reason}) with no recorded decision. "
                f"Named decision-maker: {who or 'unassigned — this is the finding'}. "
                "Choose satisfied, fallback_invoked, or renegotiated; limbo is what "
                "caused the incident")

        for other, other_date in impossible_promise(cat, dep):
            add("CC-IMPOSSIBLE-PROMISE", "high", dep.relpath,
                f"{dep_id} is promised {dep.get('promised_date')} but its hard "
                f"upstream edge {other.get('dependency_id', other.relpath)} is promised "
                f"{other_date.isoformat()} — the graph says this cannot happen")

        if state.state in MANAGED_STATES and cap is not None:
            ready = effective_readiness(cat, cap, env, today)
            if ready.depth != "complete":
                add("CC-FOG", "high", dep.relpath,
                    f"{dep_id} is a commitment made into fog: effective readiness "
                    f"'{ready.state}' with depth 'unknown' in {env}")
            if readiness_rank(ready.state) < readiness_rank("consumer_verified") \
                    and state.state == "acknowledged":
                add("CC-NOT-VERIFIED", "medium", dep.relpath,
                    f"{dep_id} is acknowledged but {cap.get('capability_id')} is only "
                    f"'{ready.state}' in {env} — no consuming team has verified it there")
            own, _ = own_readiness(cat, cap, env, today)
            if own == "provider_tested" and state.state in MANAGED_STATES:
                add("CC-PROVIDER-ONLY", "high", cap.relpath,
                    f"{cap.get('capability_id')} is provider_tested only in {env} while "
                    f"{dep_id} is committed against it. Internal testing by the provider "
                    "is not acceptance — acceptance belongs to the consumer")
            for rel in hard_requires(cap):
                target = cat.docs.get(rel)
                if target is None:
                    continue
                sub, _ = own_readiness(cat, target, env, today)
                if readiness_rank(sub) >= readiness_rank("consumer_verified"):
                    continue
                managed = any(norm_link(o.get("capability")) == rel
                              and (o.get("state") in MANAGED_STATES) for o in deps)
                if not managed:
                    add("CC-UNMANAGED-UPSTREAM", "high", dep.relpath,
                        f"{dep_id} rests on {target.get('capability_id') or rel}, which is "
                        f"'{sub}' in {env} with no dependency edge managing it")
            maturity = cap.get("code_maturity") or {}
            if isinstance(maturity, dict) and maturity.get("state") == "feature":
                add("CC-FEATURE-BRANCH", "high", cap.relpath,
                    f"{cap.get('capability_id')} exists only on a feature branch "
                    f"({maturity.get('branch')}) but {dep_id} depends on it — that is a "
                    "proposal, not a capability")

        if state.state == "at_risk" and state.originator:
            add("CC-PROPAGATED-RISK", "medium", dep.relpath,
                f"{dep_id} is at risk because upstream edge {state.originator} is in "
                "trouble — propagated automatically, no human relayed it")

        if dep.get("state") == "detected":
            add("CC-DETECTED", "medium", dep.relpath,
                f"{dep_id} is an integration that exists in code with nobody managing it "
                f"({dep.get('detected_via') or 'scanned wiring'}). Run `declare` on it")

        if dep.get("state") == "proposed":
            ack = dep.get("acknowledgement") or {}
            provider_team = re.sub(r"^teams/|/team\.md$", "", norm_link(dep.get("provider_team")))
            if not cat.team_is_claimed(provider_team):
                add("CC-STUB-PROVIDER", "high", dep.relpath,
                    f"{dep_id} names provider team '{provider_team}', which is a stub — "
                    "it can never be acknowledged until someone from that team claims it. "
                    "Go and talk to them")
            elif not (ack.get("provider") and ack.get("consumer")):
                side = "provider" if not ack.get("provider") else "consumer"
                add("CC-ONE-SIDED-ACK", "medium", dep.relpath,
                    f"{dep_id} is waiting on the {side}'s acknowledgement — an edge does "
                    "not exist until both sides sign")
            if dep.get("consumer_reconfirmation_required"):
                add("CC-DATE-GAP", "medium", dep.relpath,
                    f"{dep_id}: promised {dep.get('promised_date')} is later than the "
                    f"requested {dep.get('requested_date')} and the consumer has not "
                    "re-confirmed — negotiate now, not on delivery day")

        ponr = point_of_no_return(dep, cat.config)
        if (ponr and state.state in ("acknowledged", "at_risk")
                and today <= ponr <= today + _dt.timedelta(days=ponr_days)
                and not on_track_valid(dep, today, cat.config)):
            add("CC-PONR-NEAR", "high", dep.relpath,
                f"{dep_id} reaches its point of no return on {ponr.isoformat()} with no "
                "on-track confirmation — after that the fallback cannot be stood up in time")

        if dep.get("state") in MANAGED_STATES or dep.get("state") == "proposed":
            fallback = dep.get("fallback")
            if not isinstance(fallback, dict) and not dep.get("no_fallback_rationale"):
                add("CC-NO-FALLBACK", "medium", dep.relpath,
                    f"{dep_id} has no fallback and no rationale for having none — a team "
                    "that cannot articulate one has surfaced a red flag, so record it")
            if not dep.get("consequence_if_late"):
                add("CC-NO-CONSEQUENCE", "medium", dep.relpath,
                    f"{dep_id} does not say what happens if it is late — an unwritten "
                    "consequence is an accepted risk")
            if uses_default_fallback_days(dep):
                add("CC-DEFAULT-FALLBACK", "low", dep.relpath,
                    f"{dep_id} has no estimated fallback execution time; the point of no "
                    "return uses the org default and is therefore un-estimated")

        if cap is None and dep.get("capability"):
            add("CC-BROKEN-LINK", "low", dep.relpath,
                f"{dep_id} points at {dep.get('capability')}, which is not in the catalog")

    # Capability- and service-level findings.
    for rel, cap in sorted(cat.capabilities.items()):
        if cap.get("drift_detected"):
            add("CC-DRIFT", "medium", rel,
                f"{cap.get('capability_id')}: the fulfilling service no longer exposes "
                "this capability — the contract and the code have diverged")
        for link in cap.get("fulfilled_by") or []:
            if norm_link(link) not in cat.docs:
                add("CC-BROKEN-LINK", "low", rel, f"fulfilled_by points at missing {link}")
        for entry in cap.get("readiness") or []:
            if not isinstance(entry, dict):
                continue
            env = entry.get("environment")
            state_claimed = entry.get("state")
            if state_claimed in ("consumer_verified", "production_live"):
                sev = "high" if cat.env_config(env).get("requires_live_signal") else "medium"
                add("CC-HUMAN-LIVENESS", sev, rel,
                    f"'{state_claimed}' is asserted inside the provider-owned capability "
                    f"file for {env}. consumer_verified lives in the consumer's "
                    "Verification, production_live in signals/ — humans are exactly who "
                    "got this wrong last time")
            asserted = parse_date(entry.get("asserted_at"))
            if asserted and (today - asserted).days > stale_days:
                add("CC-STALE-ASSERTION", "low", rel,
                    f"readiness for {env} was asserted {(today - asserted).days} days ago "
                    f"by {entry.get('asserted_by')} — human-asserted fields rot")

    for ver in cat.verifications:
        if ver.get("result") == "failed":
            add("CC-FAILED-VERIFICATION", "high", ver.relpath,
                f"{ver.get('verifying_team')} could not make "
                f"{norm_link(ver.get('capability'))} work in {ver.get('environment')} — "
                "the most valuable document in the bundle, do not let it go quiet")
        if ver.get("kind") == "deployment" and not ver.get("environment_resolved_from"):
            add("CC-UNRESOLVED-ENV", "medium", ver.relpath,
                "a deployment verification must record the environment it actually ran "
                "against, resolved from the CI run rather than declared by hand")

    for sig in cat.signals:
        if not sig.relpath.startswith("signals/"):
            add("CC-HUMAN-LIVENESS", "high", sig.relpath,
                "a Signal outside signals/ is not machine-owned; production_live may only "
                "come from CI or monitoring")
        emitted = str(sig.get("emitted_by") or "")
        if not re.match(r"^(ci|monitor|monitoring)://", emitted):
            add("CC-HUMAN-LIVENESS", "high", sig.relpath,
                f"signal emitted_by '{emitted or 'unset'}' is not a machine source "
                "(ci:// or monitor://)")

    open_dep_caps = {norm_link(d.get("capability")) for d in deps
                     if d.get("state") not in TERMINAL_STATES}
    for rel, svc in sorted(cat.services.items()):
        platforms = {}
        for run in svc.get("runtimes") or []:
            if isinstance(run, dict) and run.get("environment"):
                platforms[run["environment"]] = run.get("platform")
        if len(set(p for p in platforms.values() if p)) > 1:
            fulfils = [norm_link(f) for f in svc.get("fulfils") or []]
            if any(f in open_dep_caps for f in fulfils):
                detail = ", ".join(f"{e}={p}" for e, p in sorted(platforms.items()))
                add("CC-PLATFORM-DRIFT", "high", rel,
                    f"{svc.get('service_id')} runs on different platforms per environment "
                    f"({detail}) while carrying an open dependency. Tested on one platform "
                    "is not tested on the other — this is the shape of the original incident")
        for link in svc.get("fulfils") or []:
            if norm_link(link) not in cat.docs:
                add("CC-BROKEN-LINK", "low", rel, f"fulfils points at missing {link}")

    for cyc in find_cycles(cat):
        add("CC-CYCLE", "medium", cyc[0],
            "cycle in hard requires: " + " -> ".join(cyc) +
            ". Usually means the capability boundary is drawn wrong")

    # Adoption metric, not a grievance: coverage rising is the signal that the
    # catalog is becoming trustworthy.
    for team_id in sorted(cat.teams):
        known, total = closure_coverage(cat, team_id)
        if total:
            pct = round(100.0 * known / total)
            if pct < 100:
                add("CC-COVERAGE", "low", f"teams/{team_id}/team.md",
                    f"closure coverage {known}/{total} ({pct}%) — the fraction of this "
                    "team's hard closure that resolves to claimed teams with attested "
                    "upstreams")

    findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 3),
                                 0 if f.code == "CC-TRIPPED" else 1, f.code, f.path))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Bundle validation — two tiers. OKF requires permissive consumption;
# strictness belongs to authoring.
# ─────────────────────────────────────────────────────────────────────────────

def validate(cat, today):
    hard, soft = [], []
    root_index = os.path.join(cat.root, "index.md")
    if not os.path.isfile(root_index):
        hard.append("index.md missing from the bundle root (OKF §6)")
    else:
        with open(root_index, encoding="utf-8") as fh:
            meta, _, had = parse_frontmatter(fh.read())
        if not had or not meta.get("okf_version"):
            hard.append("root index.md does not declare okf_version (OKF §4)")
    if not os.path.isfile(os.path.join(cat.root, "log.md")):
        soft.append("log.md missing — creation/update events are unrecorded (OKF §7)")
    for rel in cat.unparsed:
        hard.append(f"{rel}: no parseable YAML frontmatter with a non-empty `type`")

    for dep in cat.by_type("Dependency"):
        dep_id = dep.get("dependency_id", dep.relpath)
        if dep.get("state") not in DEPENDENCY_STATES:
            soft.append(f"{dep.relpath}: unknown state '{dep.get('state')}'")
        if dep.get("state") in MANAGED_STATES:
            if not dep.get("consequence_if_late"):
                soft.append(f"{dep_id}: acknowledged without consequence_if_late")
            if not isinstance(dep.get("fallback"), dict) and not dep.get("no_fallback_rationale"):
                soft.append(f"{dep_id}: acknowledged without a fallback or a rationale")
        ack = dep.get("acknowledgement") or {}
        if dep.get("state") == "proposed" and (bool(ack.get("provider")) != bool(ack.get("consumer"))):
            when = parse_ts((ack.get("provider") or ack.get("consumer") or {}).get("at"))
            if when and (today - when.date()).days > 7:
                soft.append(f"{dep_id}: single-sided acknowledgement older than 7 days")
        if effective_state(cat, dep, today).state == "tripped" and not dep.get("decision"):
            soft.append(f"{dep_id}: tripped with no decision recorded")
    for rel, cap in sorted(cat.capabilities.items()):
        if not cap.get("fulfilled_by"):
            soft.append(f"{rel}: capability with no fulfilling service")
        for entry in cap.get("readiness") or []:
            if isinstance(entry, dict) and entry.get("state") == "production_live":
                env = cat.env_config(entry.get("environment"))
                if env.get("requires_live_signal"):
                    soft.append(f"{rel}: production_live asserted by hand for "
                                f"{entry.get('environment')}, which requires a live signal")
    return hard, soft


# ─────────────────────────────────────────────────────────────────────────────
# Commit-boundary enforcement (§8.1)
#
# The mode-level guards protect the honest path only. The commit is where the
# rule is actually enforced, because the bundle is markdown in git and anyone
# can type `consumer_verified` into a file. This is a pure function over a
# described changeset so it is testable offline; scripts/ci-enforce.sh builds
# the changeset from git.
# ─────────────────────────────────────────────────────────────────────────────

class Violation:
    def __init__(self, code, path, message):
        self.code = code
        self.path = path
        self.message = message

    def line(self):
        return f"VIOLATION: {self.code} {self.path} — {self.message}"


_CONSUMER_OWNED = ("consequence_if_late", "fallback", "requested_date",
                   "no_fallback_rationale")
_PROVIDER_OWNED = ("promised_date",)


def _doc_from_text(rel, text):
    meta, body, had = parse_frontmatter(text or "")
    return Doc(rel, meta if had else {}, body, text or "")


def enforce(cat, commits, tolerance_days=2):
    """Check a changeset against the authority rules. `commits` is a list of

        {"sha", "author_handle", "author_team", "committed_at",
         "files": [{"path", "content", "previous"}]}

    Returns a list of Violation.
    """
    violations = []
    platform_team = cat.config.get("platform_team")

    for commit in commits or []:
        author_team = commit.get("author_team")
        author = commit.get("author_handle")
        when = parse_ts(commit.get("committed_at"))
        files = commit.get("files") or []
        paths = [f.get("path", "") for f in files]
        touches_provider_code = {}

        for entry in files:
            path = entry.get("path", "")
            doc = _doc_from_text(path, entry.get("content"))
            if doc.type in ("Capability", "Service"):
                owner = cat.team_of_doc(doc) or author_team
                touches_provider_code.setdefault(owner, []).append(path)

        for entry in files:
            path = entry.get("path", "")
            doc = _doc_from_text(path, entry.get("content"))
            before = _doc_from_text(path, entry.get("previous")) if entry.get("previous") else None

            if doc.type == "Verification":
                verifying = re.sub(r"^teams/|/team\.md$", "",
                                   norm_link(doc.get("verifying_team")))
                # Acceptance belongs to the consumer. A provider must not be
                # able to produce one — this is the check the honest-path
                # guards cannot make.
                if author_team and verifying and author_team != verifying:
                    violations.append(Violation(
                        "EN-FOREIGN-VERIFICATION", path,
                        f"authored by team '{author_team}' but claims verification by "
                        f"'{verifying}'. A verification must be committed from the "
                        "verifying team's own repository"))
                cap = cat.resolve_capability(doc.get("capability"))
                owning = cat.team_of_doc(cap) if cap is not None else None
                if owning and verifying == owning:
                    violations.append(Violation(
                        "EN-SELF-VERIFICATION", path,
                        f"team '{verifying}' owns {doc.get('capability')} and cannot "
                        "verify its own capability — self-certification is the failure "
                        "this design exists to prevent"))
                if owning and author_team == owning:
                    other = [p for p in touches_provider_code.get(owning, []) if p != path]
                    detail = (f" in the same commit as the provider's own change to "
                              f"{', '.join(sorted(other))}" if other else "")
                    violations.append(Violation(
                        "EN-SELF-VERIFICATION", path,
                        f"introduced by '{author}' on the providing team '{owning}'"
                        f"{detail}. Require the consumer's own commit"))
                if author and doc.get("verified_by") and doc.get("verified_by") != author:
                    violations.append(Violation(
                        "EN-AUTHOR-MISMATCH", path,
                        f"verified_by '{doc.get('verified_by')}' is not the commit author "
                        f"'{author}'"))
                verified_at = parse_ts(doc.get("verified_at"))
                if verified_at and when:
                    delta = (when - verified_at).total_seconds() / 86400.0
                    if delta > tolerance_days:
                        violations.append(Violation(
                            "EN-BACKDATED", path,
                            f"verified_at {doc.get('verified_at')} predates its own commit "
                            f"({iso(when)}) by {delta:.1f} days — a backdated claim"))
                    elif delta < -tolerance_days:
                        violations.append(Violation(
                            "EN-BACKDATED", path,
                            f"verified_at {doc.get('verified_at')} is dated after its own "
                            f"commit ({iso(when)})"))

            for entry_meta in (doc.get("readiness") or []):
                if isinstance(entry_meta, dict) and entry_meta.get("state") == "production_live" \
                        and not path.startswith("signals/"):
                    violations.append(Violation(
                        "EN-LIVENESS-OUTSIDE-SIGNALS", path,
                        "production_live may only originate from signals/, written by CI "
                        "or monitoring"))

            if path.startswith("signals/") and platform_team and author_team \
                    and author_team != platform_team:
                violations.append(Violation(
                    "EN-SIGNAL-AUTHOR", path,
                    f"signals/ is machine-owned (CODEOWNED by '{platform_team}') but this "
                    f"was committed by '{author_team}'"))

            if doc.type == "Dependency" and before is not None:
                consumer = re.sub(r"^teams/|/team\.md$", "",
                                  norm_link(doc.get("consumer_team")))
                provider = re.sub(r"^teams/|/team\.md$", "",
                                  norm_link(doc.get("provider_team")))
                changed = [k for k in set(list(doc.meta) + list(before.meta))
                           if doc.meta.get(k) != before.meta.get(k)]
                for key in _CONSUMER_OWNED:
                    if key in changed and author_team == provider and provider != consumer:
                        violations.append(Violation(
                            "EN-CROSS-SIDE-EDIT", path,
                            f"provider team '{provider}' changed '{key}', which belongs to "
                            "the consumer. Record the dispute in the body instead"))
                for key in _PROVIDER_OWNED:
                    if key in changed and author_team == consumer and provider != consumer:
                        violations.append(Violation(
                            "EN-CROSS-SIDE-EDIT", path,
                            f"consumer team '{consumer}' changed '{key}', which belongs to "
                            "the provider"))
                ack_now = doc.get("acknowledgement") or {}
                ack_before = before.get("acknowledgement") or {}
                for side, team in (("provider", provider), ("consumer", consumer)):
                    if ack_now.get(side) != ack_before.get(side) and author_team \
                            and team and author_team != team:
                        violations.append(Violation(
                            "EN-PROXY-ACK", path,
                            f"'{author_team}' recorded the {side}'s acknowledgement on "
                            f"behalf of '{team}'"))
    return violations
