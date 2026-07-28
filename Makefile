# Skill quality gates. Run `make gate` to validate every skill in this repo.
SHELL := /bin/sh
GATES := scripts/gates

# Every top-level directory containing a SKILL.md is a skill.
SKILLS := $(patsubst %/SKILL.md,%,$(wildcard */SKILL.md))

.PHONY: gate validate scan-leaks dry-run playbook test eval frontmatter ab-validate list-skills clean $(addprefix gate-,$(SKILLS))

list-skills:
	@printf '%s\n' $(SKILLS)

# Full gate across all skills. Runs every skill (does NOT stop at the first
# failure) and exits non-zero if any failed.
#
# On failure the captured output is PRINTED, not discarded. It used to be
# swallowed — only a one-line grep'd summary survived — so a failing unit suite
# showed up as a single line buried mid-log with no traceback, followed by more
# PASS lines. That made a real CI failure both easy to miss and impossible to
# diagnose from the log. `_fail` is the fix; keep it that way.
gate: clean
	@rc=0; \
	_fail() { printf '\n!!! FAILURE: %s\n' "$$1"; printf '%s\n' "$$2" | tail -40; printf '!!! end of %s failure\n\n' "$$1"; }; \
	for d in $(SKILLS); do \
		printf '\n=== %s ===\n' "$$d"; \
		out=$$(sh $(GATES)/validate-skill.sh "$$d" 2>&1); st=$$?; printf '%s\n' "$$out" | tail -1; [ $$st -eq 0 ] || { _fail "$$d validate" "$$out"; rc=1; }; \
		out=$$(sh $(GATES)/scan-leaks.sh "$$d" 2>&1); st=$$?; printf '%s\n' "$$out" | tail -1; [ $$st -eq 0 ] || { _fail "$$d scan-leaks" "$$out"; rc=1; }; \
		out=$$(sh $(GATES)/prompting-playbook.sh "$$d" $(PLAYBOOK_FLAGS) 2>&1); st=$$?; printf '%s\n' "$$out" | tail -1; [ $$st -eq 0 ] || { _fail "$$d playbook" "$$out"; rc=1; }; \
		out=$$(sh $(GATES)/frontmatter-standard.sh "$$d" 2>&1); st=$$?; printf '%s\n' "$$out" | tail -1; [ $$st -eq 0 ] || { _fail "$$d frontmatter" "$$out"; rc=1; }; \
		if [ -f "$$d/PARAMETERS.md" ]; then \
			scratch=$$(mktemp -d); \
			out=$$(sh $(GATES)/dry-run-replay.sh "$$d" "$$scratch" 2>&1); st=$$?; printf '%s\n' "$$out" | tail -1; [ $$st -eq 0 ] || { _fail "$$d dry-run" "$$out"; rc=1; }; \
			rm -rf "$$scratch"; \
		fi; \
		for t in "$$d"/assets/test_*.py; do \
			[ -f "$$t" ] || continue; \
			out=$$(cd "$$d/assets" && python3 "$$(basename "$$t")" 2>&1); st=$$?; \
			printf 'UNIT %s: %s\n' "$$t" "$$(printf '%s\n' "$$out" | grep -oE 'OK|FAILED.*|Ran [0-9]+ tests' | tr '\n' ' ')"; \
			[ $$st -eq 0 ] || { _fail "$$t" "$$out"; rc=1; }; \
		done; \
		out=$$(sh $(GATES)/run-eval.sh "$$d" 2>&1); st=$$?; printf '%s\n' "$$out" | tail -1; [ $$st -eq 0 ] || { _fail "$$d eval" "$$out"; rc=1; }; \
	done; \
	if [ $$rc -ne 0 ]; then printf '\nGATE_RESULT: FAIL (search this log for "!!! FAILURE")\n'; else printf '\nGATE_RESULT: PASS\n'; fi; \
	exit $$rc

validate:
	@rc=0; for d in $(SKILLS); do \
		out=$$(sh $(GATES)/validate-skill.sh "$$d" 2>&1); st=$$?; printf '%s\n' "$$out" | tail -1; [ $$st -eq 0 ] || rc=1; \
	done; exit $$rc

scan-leaks: clean
	@rc=0; for d in $(SKILLS); do \
		out=$$(sh $(GATES)/scan-leaks.sh "$$d" 2>&1); st=$$?; printf '%s\n' "$$out" | tail -1; [ $$st -eq 0 ] || rc=1; \
	done; exit $$rc

dry-run:
	@rc=0; for d in $(SKILLS); do \
		if [ -f "$$d/PARAMETERS.md" ]; then \
			scratch=$$(mktemp -d); \
			out=$$(sh $(GATES)/dry-run-replay.sh "$$d" "$$scratch" 2>&1); st=$$?; printf '%s\n' "$$out" | tail -1; [ $$st -eq 0 ] || rc=1; \
			rm -rf "$$scratch"; \
		fi; \
	done; exit $$rc

# Run every skill's stdlib unit test suite (offline, deterministic — no pip, no network).
test: clean
	@rc=0; for d in $(SKILLS); do \
		for t in "$$d"/assets/test_*.py; do \
			[ -f "$$t" ] || continue; \
			printf '\n=== %s ===\n' "$$t"; \
			out=$$(cd "$$d/assets" && python3 "$$(basename "$$t")" 2>&1); st=$$?; \
			printf '%s\n' "$$out" | tail -3; [ $$st -eq 0 ] || rc=1; \
		done; \
	done; exit $$rc

# Run every skill's outcome eval (docs/eval-standard.md): deterministic
# harness -> skill tooling -> model-free grader. Hard gate: missing eval fails.
eval: clean
	@rc=0; for d in $(SKILLS); do \
		printf '\n=== %s ===\n' "$$d"; \
		sh $(GATES)/run-eval.sh "$$d" || rc=1; \
	done; exit $$rc

# Check the standard frontmatter metadata (license/compatibility/author/version/tags).
frontmatter:
	@rc=0; for d in $(SKILLS); do \
		out=$$(sh $(GATES)/frontmatter-standard.sh "$$d" 2>&1); st=$$?; \
		printf '%s: %s\n' "$$d" "$$(printf '%s\n' "$$out" | tail -1)"; [ $$st -eq 0 ] || rc=1; \
	done; exit $$rc

# Lint every skill against "The Prompting Playbook" conventions (full output).
# Promote the two advisories (PP-5/PP-6) to hard failures: make playbook PLAYBOOK_FLAGS=--strict
playbook:
	@rc=0; for d in $(SKILLS); do \
		printf '\n=== %s ===\n' "$$d"; \
		sh $(GATES)/prompting-playbook.sh "$$d" $(PLAYBOOK_FLAGS) || rc=1; \
	done; exit $$rc

# Behavioural A/B against a baseline ref: does a change make skills BEHAVE better,
# or merely still pass the gates? Deliberately NOT part of `make gate` — it needs a
# baseline commit present in the clone, and SKIPs cleanly when that is unavailable.
#   make ab-validate                 # against the default baseline in the script
#   make ab-validate BASE=<git-ref>  # against any other ref
ab-validate: clean
	@python3 scripts/ab-validate.py $(BASE)

# Gate a single skill: make gate-skill SKILL=base-in-reality
gate-skill:
	@test -n "$(SKILL)" || { echo "usage: make gate-skill SKILL=<dir>"; exit 2; }
	@sh $(GATES)/validate-skill.sh "$(SKILL)"
	@sh $(GATES)/scan-leaks.sh "$(SKILL)"
	@sh $(GATES)/prompting-playbook.sh "$(SKILL)" $(PLAYBOOK_FLAGS)
	@if [ -f "$(SKILL)/PARAMETERS.md" ]; then \
		scratch=$$(mktemp -d); \
		sh $(GATES)/dry-run-replay.sh "$(SKILL)" "$$scratch"; \
		rm -rf "$$scratch"; \
	fi
	@sh $(GATES)/frontmatter-standard.sh "$(SKILL)"
	@for t in "$(SKILL)"/assets/test_*.py; do \
		[ -f "$$t" ] || continue; \
		out=$$(cd "$(SKILL)/assets" && python3 "$$(basename "$$t")" 2>&1) || { printf '%s\n' "$$out" | tail -5; exit 1; }; \
		printf 'UNIT %s: %s\n' "$$t" "$$(printf '%s\n' "$$out" | grep -oE 'OK|Ran [0-9]+ tests' | tr '\n' ' ')"; \
	done
	@sh $(GATES)/run-eval.sh "$(SKILL)"

# Remove build artifacts that pollute scan-leaks (e.g. __pycache__/*.pyc).
clean:
	@find . -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete 2>/dev/null || true
