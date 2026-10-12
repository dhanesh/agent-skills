# find_python: set PY to the Python interpreter this kit runs, or to "" when there is
# none. Order: $SKILL_PYTHON (one executable, no arguments), python3 unless it is the
# macOS install stub (/usr/bin/python3 while `xcode-select -p` fails; never run, so its
# install dialog never opens), then python. The same lookup as skill-contract's
# resolve_python() and the SKILL.md "Python interpreter" rule. This file is
# byte-identical in world-model-ledger, context-hygiene-kit and tmux-agent-herdr-lite;
# world-model-ledger/assets/test_find_python.py pins the copies together.
find_python() {
  PY=""
  if [ -n "${SKILL_PYTHON:-}" ]; then
    _fp=$(command -v "$SKILL_PYTHON" 2>/dev/null) && [ -n "$_fp" ] && { PY=$_fp; return 0; }
  fi
  _fp=$(command -v python3 2>/dev/null) || _fp=""
  if [ -n "$_fp" ]; then
    if [ "$_fp" = "${_FIND_PYTHON_STUB:-/usr/bin/python3}" ] \
       && [ "$(uname -s 2>/dev/null)" = Darwin ] \
       && ! xcode-select -p >/dev/null 2>&1; then
      :
    else
      PY=$_fp
      return 0
    fi
  fi
  _fp=$(command -v python 2>/dev/null) || _fp=""
  PY=$_fp
  return 0
}
