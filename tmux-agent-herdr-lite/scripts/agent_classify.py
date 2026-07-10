"""Pure status-classification logic for the tmux agent cockpit.

Extracted from ``agent-status-scan`` so the classifier can be unit-tested
(``assets/test_agent_classify.py``). Statuses are routing hints, not truth
(see SKILL.md "Status model").

Two detection layers, mirroring Herdr's architecture:

1. **Per-agent screen manifests** (``AGENT_MANIFESTS``) — prioritized rules
   ported from Herdr's ``src/detect/manifests/*.toml``. When the pane's agent
   is known (``agent-pane --agent`` or inferred from the command), only its
   manifest decides blocked/working/idle from screen chrome; loose generic
   word heuristics are skipped, so an agent *discussing* an error can't be
   mislabeled. Like Herdr, unknown prompts fall back to idle, never blocked.
2. **Generic heuristics** — for unrecognized commands: explicit
   ``AGENT_STATUS:`` sentinels and real failure signals win over loose word
   hints, and active output (``working``) beats advisory words.

Both layers match only the *tail* of the captured pane (the live prompt
region), so a token that scrolled out of view no longer pins a status. The
pane title (tmux ``#{pane_title}``, set by agents via OSC 0/2) is used the
way Herdr uses OSC titles: a braille-spinner title means working.
"""

import re

# --- generic heuristics (unchanged behavior when the agent is unknown) -----

# Authoritative interactive-wait prompts: an agent showing these in the tail is
# genuinely waiting for a human right now, so they preempt the activity check.
BLOCKED_STRONG = [
    r'AGENT_STATUS:\s*blocked', r'approval required', r'do you want to continue',
    r'continue\?\s*\[?[yn]/?n?\]?', r'proceed\?', r'press enter', r'waiting for user',
    r'need(s)? permission', r'password:', r'passphrase', r'clarification needed',
]

# Advisory blocked hints: prone to false positives (a logged error, an old rate
# limit), so they are trusted only when output is quiescent.
BLOCKED_SOFT = [r'permission denied', r'rate limit', r'too many attempts', r'\b429\b']

# Real failure signals. Checked BEFORE blocked so a genuine crash is not masked
# by an incidental 'permission denied'/'429' substring, and surfaced as its own
# state. Note re.search is unanchored, so 'exit_code=[1-9]' matches multi-digit
# nonzero codes (10, 137, 255) on their leading digit.
ERROR_PATTERNS = [r'traceback', r'uncaught exception', r'command failed', r'exit_code=[1-9]']

# Authoritative completion sentinel emitted by agent-pane.
DONE_STRONG = [r'AGENT_STATUS:\s*done']

# Advisory completion hints: loose, trusted only when quiescent.
DONE_SOFT = [
    r'task complete', r'\bdone\b', r'all tests passed',
    r'no changes needed', r'process exited', r'exit_code=0',
]

# Order panes are sorted/iterated by elsewhere; kept here as the single source of
# truth for the state vocabulary.
STATES = ('error', 'blocked', 'working', 'idle', 'done', 'unknown')

# --- per-agent screen manifests (ported from Herdr src/detect/manifests) ---

# Matchers evaluated against the lowercased agent tail region:
#   ('contains', s)    substring
#   ('line', pattern)  regex must match at least one line
#   ('regex', pattern) regex against the whole region
#   ('title', s) / ('title_regex', p)  against the pane title (OSC 0/2)
# Rule = {'id', 'state', 'priority', 'all': [...], 'any': [...], 'none': [...]}
# Highest matching priority wins; ties keep the earlier rule.

# Braille spinner class agents put in the terminal title while streaming.
_SPINNER_TITLE = r'^[⠀-⣿] '

AGENT_MANIFESTS = {
    'claude': [
        {'id': 'title_working', 'state': 'working', 'priority': 1100,
         'all': [('title_regex', _SPINNER_TITLE)]},
        {'id': 'selection_form', 'state': 'blocked', 'priority': 980,
         'all': [('contains', 'enter to select'), ('contains', 'esc to cancel')]},
        {'id': 'permission_prompt', 'state': 'blocked', 'priority': 850,
         'all': [('contains', 'do you want to proceed?')]},
        {'id': 'interrupt_hint_working', 'state': 'working', 'priority': 700,
         'any': [('contains', 'esc to interrupt'), ('contains', 'ctrl+b to run in background')]},
        {'id': 'legacy_prompt_blocker', 'state': 'blocked', 'priority': 300,
         'any': [('contains', 'waiting for permission'), ('contains', 'tab to amend'),
                 ('contains', 'do you want to allow this connection?')]},
        {'id': 'ask_prompt_blocker', 'state': 'blocked', 'priority': 290,
         'any': [('contains', 'do you want to'), ('contains', 'would you like to')],
         'all': [('line', r'^\s*❯?\s*(1\.\s*)?yes\b')]},
        {'id': 'prompt_box_idle', 'state': 'idle', 'priority': 250,
         'all': [('line', r'^[\s│]*❯')],
         'none': [('contains', 'enter to select'), ('contains', 'arrow keys to navigate')]},
        {'id': 'title_idle', 'state': 'idle', 'priority': 240,
         'all': [('title_regex', r'^✳ ')]},
    ],
    'codex': [
        {'id': 'title_blocked', 'state': 'blocked', 'priority': 1100,
         'all': [('title', 'action required')]},
        {'id': 'title_working', 'state': 'working', 'priority': 1050,
         'all': [('title_regex', _SPINNER_TITLE)]},
        {'id': 'strong_blocker', 'state': 'blocked', 'priority': 900,
         'any': [('contains', 'press enter to confirm or esc to cancel'),
                 ('contains', 'enter to submit answer'),
                 ('contains', 'enter to submit all'),
                 ('contains', 'allow command?')]},
        {'id': 'interrupt_hint_working', 'state': 'working', 'priority': 700,
         'any': [('contains', 'esc to interrupt'), ('contains', 'ctrl+c to interrupt')]},
        {'id': 'weak_blocker', 'state': 'blocked', 'priority': 600,
         'any': [('contains', '[y/n]'), ('contains', 'yes (y)'),
                 ('contains', 'do you want to'), ('contains', 'would you like to')]},
    ],
    'gemini': [
        {'id': 'apply_or_allow', 'state': 'blocked', 'priority': 300,
         'any': [('contains', '│ apply this change'),
                 ('contains', '│ allow execution'),
                 ('contains', 'waiting for user confirmation'),
                 ('contains', 'do you want to proceed'),
                 ('line', r'^\s*❯.*(yes|allow)')]},
        {'id': 'esc_cancel_working', 'state': 'working', 'priority': 100,
         'all': [('contains', 'esc to cancel')]},
    ],
    'opencode': [
        {'id': 'permission_required', 'state': 'blocked', 'priority': 300,
         'any': [('contains', '△ permission required'),
                 ('contains', 'esc dismiss')]},
        {'id': 'interrupt_working', 'state': 'working', 'priority': 110,
         'any': [('contains', 'esc to interrupt'), ('contains', 'ctrl+c to interrupt'),
                 ('contains', 'esc interrupt')]},
        {'id': 'progress_bar_working', 'state': 'working', 'priority': 100,
         'all': [('regex', r'(■|⬝){4,}')]},
    ],
    'amp': [
        {'id': 'approval_footer', 'state': 'blocked', 'priority': 300,
         'any': [('contains', 'waiting for approval'), ('contains', 'run this command?'),
                 ('contains', 'allow editing file:'), ('contains', 'allow creating file:'),
                 ('contains', 'confirm tool call')]},
        {'id': 'status_footer_working', 'state': 'working', 'priority': 200,
         'all': [('line', r'^\s*╰\s+\S+\s+(thinking|streaming|running tools|waiting)\s+─')]},
        {'id': 'esc_cancel_working', 'state': 'working', 'priority': 100,
         'all': [('contains', 'esc to cancel')]},
    ],
    'cursor': [
        {'id': 'write_file_approval', 'state': 'blocked', 'priority': 320,
         'all': [('contains', 'write to this file?'), ('contains', 'proceed (y)')]},
        {'id': 'approval_prompt', 'state': 'blocked', 'priority': 300,
         'any': [('contains', 'waiting for approval'), ('contains', 'run (once) (y)'),
                 ('contains', 'skip (esc or n)'), ('contains', '(y) (enter)')]},
        {'id': 'stop_hint_working', 'state': 'working', 'priority': 100,
         'all': [('contains', 'ctrl+c to stop')]},
        {'id': 'spinner_working', 'state': 'working', 'priority': 90,
         'all': [('line', r'^\s*(⬡|⬢|[⠀-⣿]+)\s+\w+ing\b')]},
    ],
    'copilot': [
        {'id': 'selection_blocker', 'state': 'blocked', 'priority': 300,
         'any': [('contains', 'esc to cancel'), ('contains', 'esc cancel')],
         'all': [('regex', r'enter (to )?(select|confirm|submit|accept)')]},
        {'id': 'cancel_hint_working', 'state': 'working', 'priority': 100,
         'any': [('contains', 'esc to cancel'), ('contains', 'esc cancel'),
                 ('contains', 'esc again to cancel'), ('contains', 'esc interrupt')]},
    ],
    'droid': [
        {'id': 'execute_selection_blocker', 'state': 'blocked', 'priority': 300,
         'all': [('contains', 'enter to select'), ('contains', 'esc to cancel')]},
        {'id': 'stop_hint_working', 'state': 'working', 'priority': 100,
         'all': [('contains', 'esc to stop')]},
    ],
    'cline': [
        {'id': 'tool_permission', 'state': 'blocked', 'priority': 300,
         'any': [('contains', 'let cline use this tool'),
                 ('contains', 'execute command?'), ('contains', 'use this tool?')]},
        # Cline has no idle chrome: any non-empty screen means working.
        {'id': 'default_working', 'state': 'working', 'priority': -10,
         'all': [('regex', r'\S')]},
    ],
    'devin': [
        {'id': 'workspace_trust_prompt', 'state': 'blocked', 'priority': 300,
         'all': [('contains', 'do you trust the authors of this directory?')]},
        {'id': 'permission_prompt', 'state': 'blocked', 'priority': 290,
         'all': [('contains', 'approve once'), ('contains', 'esc cancel')]},
        {'id': 'running_tools_working', 'state': 'working', 'priority': 200,
         'any': [('contains', 'running tools'), ('contains', 'esc to interrupt'),
                 ('contains', 'guide devin while it works')]},
        {'id': 'live_prompt_idle', 'state': 'idle', 'priority': 100,
         'all': [('line', r'^\s*❭')]},
    ],
    'kimi': [
        {'id': 'approval_panel', 'state': 'blocked', 'priority': 400,
         'all': [('contains', '↵ confirm')],
         'any': [('contains', 'run this command?'), ('contains', 'write this file?'),
                 ('contains', 'apply these edits?'), ('contains', 'stop this task?'),
                 ('contains', 'ready to build with this plan?')]},
        {'id': 'question_panel', 'state': 'blocked', 'priority': 390,
         'all': [('contains', '↑↓ select'), ('contains', 'esc cancel')]},
        {'id': 'legacy_approval_panel', 'state': 'blocked', 'priority': 300,
         'all': [('contains', 'requesting approval'), ('contains', 'reject')]},
        {'id': 'moon_spinner_working', 'state': 'working', 'priority': 100,
         'all': [('line', r'^\s*(🌕|🌖|🌗|🌘|🌑|🌒|🌓|🌔)\s*$')]},
        {'id': 'braille_spinner_working', 'state': 'working', 'priority': 90,
         'all': [('line', r'^\s*[⠁-⣿]+\s*(thinking\.\.\.|working\.\.\.|using )')]},
    ],
    'kiro': [
        {'id': 'tool_approval', 'state': 'blocked', 'priority': 300,
         'all': [('contains', 'requires approval')]},
        {'id': 'subagent_approval', 'state': 'blocked', 'priority': 290,
         'all': [('contains', 'pending from subagents')]},
        {'id': 'working_marker', 'state': 'working', 'priority': 100,
         'all': [('contains', 'kiro is working')]},
        {'id': 'tool_spinner_working', 'state': 'working', 'priority': 90,
         'all': [('contains', 'esc to cancel'), ('line', r'^\s*(◔|◑|◕|●)\s+\w')]},
    ],
    'grok': [
        {'id': 'option_dialog_blocked', 'state': 'blocked', 'priority': 320,
         'all': [('line', r'^\s*┃\s+[0-9a-z]+\s+\([●○]\)\s')]},
        {'id': 'permission_hints_blocked', 'state': 'blocked', 'priority': 310,
         'all': [('contains', ':select'), ('contains', 'ctrl+o:yolo')]},
        {'id': 'permission_scope_selector', 'state': 'blocked', 'priority': 300,
         'all': [('contains', 'yes, proceed'), ('contains', 'no, reject')]},
        # Anchored on the [stop] chip: Grok's splash logo is drawn in braille,
        # so a bare spinner glyph is not evidence of work.
        {'id': 'spinner_status_working', 'state': 'working', 'priority': 200,
         'all': [('line', r'^\s*[⠁-⣿]\s.*\[stop\]\s*$')]},
        {'id': 'esc_cancel_working', 'state': 'working', 'priority': 190,
         'all': [('contains', 'esc:cancel')]},
        {'id': 'prompt_hints_idle', 'state': 'idle', 'priority': 100,
         'all': [('contains', 'ctrl+.:shortcuts')],
         'none': [('contains', 'esc:cancel'), ('contains', 'ctrl+c:cancel')]},
    ],
    'hermes': [
        {'id': 'dangerous_command_approval', 'state': 'blocked', 'priority': 300,
         'any': [('contains', 'dangerous command'), ('contains', 'allow once'),
                 ('contains', 'allow for this session')]},
        {'id': 'interrupt_status_working', 'state': 'working', 'priority': 100,
         'any': [('contains', 'msg=interrupt'), ('contains', 'ctrl+c cancel')]},
    ],
    'qodercli': [
        {'id': 'confirmation_or_input_blocker', 'state': 'blocked', 'priority': 300,
         'any': [('contains', 'waiting for user confirmation'),
                 ('contains', 'awaiting approval'), ('contains', 'permission required'),
                 ('contains', 'allow once or always?'), ('contains', 'asking user'),
                 ('contains', 'enter your response'), ('contains', 'shell awaiting input')]},
        {'id': 'cancel_hint_working', 'state': 'working', 'priority': 100,
         'all': [('contains', '(esc to cancel,')]},
        {'id': 'spinner_working', 'state': 'working', 'priority': 90,
         'all': [('line', r'^\s*[⠁-⣿]\s+.*\w')]},
    ],
    'antigravity': [
        {'id': 'permission_prompt', 'state': 'blocked', 'priority': 300,
         'all': [('contains', 'requesting permission for:')]},
        {'id': 'spinner_working', 'state': 'working', 'priority': 100,
         'all': [('line', r'^\s*[⠁-⣿]+\s+\w+ing\b')]},
    ],
    'pi': [
        {'id': 'working_literal', 'state': 'working', 'priority': 100,
         'all': [('contains', 'working...')]},
    ],
}

# CLI binary names → manifest key (Herdr identify_agent, trimmed to the
# manifests carried here).
AGENT_ALIASES = {
    'claude': 'claude', 'claude-code': 'claude',
    'codex': 'codex',
    'gemini': 'gemini',
    'opencode': 'opencode', 'open-code': 'opencode', 'kilo': 'opencode',
    'amp': 'amp', 'amp-local': 'amp',
    'cursor': 'cursor', 'cursor-agent': 'cursor',
    'copilot': 'copilot', 'github-copilot': 'copilot',
    'droid': 'droid',
    'cline': 'cline',
    'devin': 'devin', 'devin-cli': 'devin',
    'kimi': 'kimi', 'kimi-code': 'kimi',
    'kiro': 'kiro', 'kiro-cli': 'kiro',
    'grok': 'grok', 'grok-build': 'grok',
    'hermes': 'hermes', 'hermes-agent': 'hermes',
    'qodercli': 'qodercli', 'qoder': 'qodercli',
    'agy': 'antigravity', 'antigravity': 'antigravity', 'antigravity-cli': 'antigravity',
    'pi': 'pi',
}

_WRAPPERS = {'env', 'uv', 'uvx', 'npx', 'node', 'bun', 'python', 'python3',
             'sh', 'bash', 'zsh', 'fish'}


def load_manifests(override_dir=None):
    """Built-in manifests merged with user overrides (Herdr local-override parity).

    ``override_dir`` (typically ``$AGENT_TMUX_ROOT/detect``) may hold one JSON
    file per agent — ``claude.json`` etc. — whose contents replace that
    agent's built-in rule list entirely, like Herdr's
    ``agent-detection/<agent>.toml``. Rules use the same shape as
    :data:`AGENT_MANIFESTS` with matchers as 2-item lists::

        [{"id": "my_rule", "state": "blocked", "priority": 500,
          "all": [["contains", "custom approval text"]]}]

    Unreadable files are ignored (a broken override must not take the
    scanner down).
    """
    import json
    import os
    manifests = dict(AGENT_MANIFESTS)
    if not override_dir or not os.path.isdir(override_dir):
        return manifests
    for fn in sorted(os.listdir(override_dir)):
        if not fn.endswith('.json'):
            continue
        agent = fn[:-5]
        try:
            with open(os.path.join(override_dir, fn)) as f:
                rules = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(rules, list):
            continue
        cleaned = []
        for rule in rules:
            if not isinstance(rule, dict) or 'state' not in rule or 'priority' not in rule:
                continue
            for key in ('all', 'any', 'none'):
                rule[key] = [tuple(m) for m in rule.get(key, [])
                             if isinstance(m, (list, tuple)) and len(m) == 2]
            rule.setdefault('id', 'override')
            cleaned.append(rule)
        if cleaned:
            manifests[agent] = cleaned
    return manifests


def infer_agent(command):
    """Best-effort agent name from a launch command line ('' if unknown).

    Walks leading wrapper tokens (env/npx/uv run/...) the way Herdr unwraps
    interpreter argv, then looks the basename up in AGENT_ALIASES.
    """
    for tok in (command or '').split():
        base = tok.rsplit('/', 1)[-1].lower()
        if base in _WRAPPERS or '=' in tok or tok.startswith('-') or base == 'run':
            continue
        return AGENT_ALIASES.get(base, '')
    return ''


def tail(text, lines=6):
    """Return the lowercased last ``lines`` non-empty lines of ``text``.

    This is the live prompt region of the pane — where the current state (a
    waiting prompt, a final completion/exit line) actually appears.
    """
    nonempty = [ln for ln in text.splitlines() if ln.strip()]
    return '\n'.join(nonempty[-lines:]).lower()


def _hit(patterns, text):
    return any(re.search(p, text, re.I) for p in patterns)


def _match(matcher, region, title):
    kind, value = matcher
    if kind == 'contains':
        return value in region
    if kind == 'regex':
        return re.search(value, region) is not None
    if kind == 'line':
        return any(re.search(value, ln) for ln in region.splitlines())
    if kind == 'title':
        return value in title
    if kind == 'title_regex':
        return re.search(value, title) is not None
    return False


def _eval_manifest(rules, region, title):
    """Return (state, rule_id) for the highest-priority matching rule."""
    best = None
    for rule in rules:
        if best is not None and best[0] >= rule['priority']:
            continue
        if any(_match(m, region, title) for m in rule.get('none', [])):
            continue
        if not all(_match(m, region, title) for m in rule.get('all', [])):
            continue
        anys = rule.get('any', [])
        if anys and not any(_match(m, region, title) for m in anys):
            continue
        best = (rule['priority'], rule['state'], rule['id'])
    return (best[1], best[2]) if best else (None, None)


def classify_explain(text, changed, idle_elapsed, idle_s, prev_status='working',
                     agent='', title='', manifests=None):
    """Classify a pane and say why. Returns ``(status, reason)``.

    Args:
        text: full captured visible pane buffer.
        changed: True if the buffer hash differs from the previous scan.
        idle_elapsed: seconds since the buffer last changed.
        idle_s: idle threshold in seconds.
        prev_status: status from the previous scan (fallback).
        agent: manifest key ('claude', 'codex', ...) or '' for generic.
        title: pane title (tmux #{pane_title}; OSC 0/2 set by the agent).

    Never returns ``unknown`` — that is decided by the caller from pane
    existence.

    Precedence (highest first):
      1. ``error``      — crash/nonzero-exit signal in the tail.
      2. sentinel       — explicit ``AGENT_STATUS: done|blocked`` in the tail.
      3. manifest       — the known agent's screen rules (blocked/working/
                          idle chrome; a finished agent shows idle chrome,
                          reported as ``done`` until previously working).
      4. ``working``    — buffer changed since last scan (active output).
      5. generic hints  — strong/soft blocked + done words, only for panes
                          with no manifest (known agents skip loose words —
                          Herdr's strict-blocked rule).
      6. ``idle``       — quiescent past the idle threshold.
      7. ``prev_status``— quiescent but within threshold.
    """
    low = tail(text)
    title = (title or '').lower()
    if _hit(ERROR_PATTERNS, low):
        return 'error', 'error-signal in tail'
    if _hit(DONE_STRONG, low):
        return 'done', 'sentinel AGENT_STATUS: done'
    if re.search(r'AGENT_STATUS:\s*blocked', low, re.I):
        return 'blocked', 'sentinel AGENT_STATUS: blocked'

    manifest = (manifests if manifests is not None else AGENT_MANIFESTS).get(agent)
    if manifest:
        region = tail(text, 15)
        state, rule_id = _eval_manifest(manifest, region, title)
        if state == 'idle':
            # Herdr maps finished-and-unviewed to done; a fresh prompt that
            # was never working is just idle.
            done = prev_status in ('working', 'blocked', 'done')
            return ('done' if done else 'idle'), f'manifest {agent}/{rule_id}'
        if state:
            return state, f'manifest {agent}/{rule_id}'
        if changed:
            return 'working', 'output changed'
        if idle_elapsed > idle_s:
            return 'idle', f'quiescent > {idle_s}s (known agent, no chrome matched)'
        return (prev_status or 'working'), 'no signal; kept previous status'

    if _hit(BLOCKED_STRONG, low):
        return 'blocked', 'strong interactive-wait prompt'
    if changed:
        return 'working', 'output changed'
    if _hit(BLOCKED_SOFT, low):
        return 'blocked', 'advisory blocked hint while quiescent'
    if _hit(DONE_SOFT, low):
        return 'done', 'advisory completion hint while quiescent'
    if idle_elapsed > idle_s:
        return 'idle', f'quiescent > {idle_s}s'
    return (prev_status or 'working'), 'no signal; kept previous status'


def classify(text, changed, idle_elapsed, idle_s, prev_status='working',
             agent='', title='', manifests=None):
    """Classify a pane's status; see :func:`classify_explain` for semantics."""
    return classify_explain(text, changed, idle_elapsed, idle_s, prev_status,
                            agent, title, manifests)[0]
