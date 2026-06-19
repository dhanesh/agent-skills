# Skill quality gates. Run `make gate` to validate every skill in this repo.
SHELL := /bin/sh
GATES := scripts/gates

# Every top-level directory containing a SKILL.md is a skill.
SKILLS := $(patsubst %/SKILL.md,%,$(wildcard */SKILL.md))

.PHONY: gate validate scan-leaks dry-run list-skills clean $(addprefix gate-,$(SKILLS))

list-skills:
	@printf '%s\n' $(SKILLS)

# Full gate across all skills. Fails fast on the first failing skill.
gate: clean
	@rc=0; for d in $(SKILLS); do \
		printf '\n=== %s ===\n' "$$d"; \
		sh $(GATES)/validate-skill.sh "$$d" | tail -1 || rc=1; \
		sh $(GATES)/scan-leaks.sh "$$d" | tail -1 || rc=1; \
		if [ -f "$$d/PARAMETERS.md" ]; then \
			scratch=$$(mktemp -d); \
			sh $(GATES)/dry-run-replay.sh "$$d" "$$scratch" | tail -1 || rc=1; \
			rm -rf "$$scratch"; \
		fi; \
	done; \
	exit $$rc

validate:
	@rc=0; for d in $(SKILLS); do \
		sh $(GATES)/validate-skill.sh "$$d" | tail -1 || rc=1; \
	done; exit $$rc

scan-leaks: clean
	@rc=0; for d in $(SKILLS); do \
		sh $(GATES)/scan-leaks.sh "$$d" | tail -1 || rc=1; \
	done; exit $$rc

dry-run:
	@rc=0; for d in $(SKILLS); do \
		if [ -f "$$d/PARAMETERS.md" ]; then \
			scratch=$$(mktemp -d); \
			sh $(GATES)/dry-run-replay.sh "$$d" "$$scratch" | tail -1 || rc=1; \
			rm -rf "$$scratch"; \
		fi; \
	done; exit $$rc

# Gate a single skill: make gate-skill SKILL=base_in_reality
gate-skill:
	@test -n "$(SKILL)" || { echo "usage: make gate-skill SKILL=<dir>"; exit 2; }
	@sh $(GATES)/validate-skill.sh "$(SKILL)"
	@sh $(GATES)/scan-leaks.sh "$(SKILL)"
	@if [ -f "$(SKILL)/PARAMETERS.md" ]; then \
		scratch=$$(mktemp -d); \
		sh $(GATES)/dry-run-replay.sh "$(SKILL)" "$$scratch"; \
		rm -rf "$$scratch"; \
	fi

# Remove build artifacts that pollute scan-leaks (e.g. __pycache__/*.pyc).
clean:
	@find . -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete 2>/dev/null || true
