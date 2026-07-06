"""Pure status-classification logic for the tmux agent cockpit.

Extracted from ``agent-status-scan`` so the classifier can be unit-tested
(``test_agent_classify.py``). Statuses are routing hints, not truth (see
SKILL.md "Status model").

Design (audit 2026-06-22, findings [34] VIOLATION / [35] DEVIATION):

* Match only the *tail* of the captured pane — the live prompt region — so a
  stale token that has scrolled up out of the current view no longer pins a
  status. ``tmux capture-pane`` returns only the visible buffer, so without
  this restriction classification was order- and viewport-dependent.
* Prefer explicit ``AGENT_STATUS:`` sentinels and real failure signals over
  loose word heuristics, and let *active output* win over advisory words so a
  stray ``done``/``429`` cannot mislabel an actively-streaming agent.
* Report a crashed agent as a distinct ``error`` state (checked before
  ``blocked``) instead of conflating it with "awaiting input".
"""

import re

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


def tail(text, lines=6):
    """Return the lowercased last ``lines`` non-empty lines of ``text``.

    This is the live prompt region of the pane — where the current state (a
    waiting prompt, a final completion/exit line) actually appears.
    """
    nonempty = [ln for ln in text.splitlines() if ln.strip()]
    return '\n'.join(nonempty[-lines:]).lower()


def _hit(patterns, text):
    return any(re.search(p, text, re.I) for p in patterns)


def classify(text, changed, idle_elapsed, idle_s, prev_status='working'):
    """Classify a pane's status from its captured buffer.

    Args:
        text: full captured visible pane buffer.
        changed: True if the buffer hash differs from the previous scan.
        idle_elapsed: seconds since the buffer last changed.
        idle_s: idle threshold in seconds.
        prev_status: status from the previous scan (fallback).

    Returns one of :data:`STATES` (never ``unknown`` — that is decided by the
    caller from pane existence).

    Precedence (highest first):
      1. ``error``   — crash/nonzero-exit signal in the tail.
      2. ``done``    — explicit ``AGENT_STATUS: done`` sentinel in the tail.
      3. ``blocked`` — explicit interactive-wait prompt in the tail.
      4. ``working`` — buffer changed since last scan (active output).
      5. ``blocked`` — advisory blocked hint, only while quiescent.
      6. ``done``    — advisory completion hint, only while quiescent.
      7. ``idle``    — quiescent past the idle threshold.
      8. ``prev_status`` — quiescent but within threshold.
    """
    low = tail(text)
    if _hit(ERROR_PATTERNS, low):
        return 'error'
    if _hit(DONE_STRONG, low):
        return 'done'
    if _hit(BLOCKED_STRONG, low):
        return 'blocked'
    if changed:
        return 'working'
    if _hit(BLOCKED_SOFT, low):
        return 'blocked'
    if _hit(DONE_SOFT, low):
        return 'done'
    if idle_elapsed > idle_s:
        return 'idle'
    return prev_status or 'working'
