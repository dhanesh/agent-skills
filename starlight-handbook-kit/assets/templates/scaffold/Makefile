# {{SITE_TITLE}} — Astro + Starlight static docs site.
# Package manager is npm (package-lock.json is committed).

.PHONY: install run build preview check verify

# Install dependencies (produces/uses package-lock.json).
install:
	npm install

# Dev server with a browser tab auto-opened.
run: install
	npm run dev -- --open

# Static production build to ./dist.
build: install
	npm run build

# Preview the built static site locally.
preview: install
	npm run preview

# Type-check the project.
check: install
	npm run check

# Full CI gate: build + all 8 integrity checks.
verify: install
	npm run verify
