SHELL := /bin/bash
.DEFAULT_GOAL := help

PYTHON ?= python3
UV ?= uv
UV_CACHE_DIR ?= .uv-cache
RUFF ?= $(if $(wildcard .venv/bin/ruff),.venv/bin/ruff,$(UV) run --extra dev ruff)
MYPY ?= $(if $(wildcard .venv/bin/mypy),.venv/bin/mypy,$(UV) run --extra dev mypy)
RELEASE_PROJECT ?= ../anki-addon-release
RELEASE_ENV_FILE ?= .env
RELEASE_DIAGNOSTICS_DIR ?= .anki-addon-release/diagnostics
RELEASE_ARTIFACT ?= dist/fractional-new-card-scheduler.ankiaddon
RELEASE_BIN ?= $(RELEASE_PROJECT)/.venv/bin/anki-addon-release
ANKI_ADDON_RELEASE = $(RELEASE_BIN) --project .
ANKI_ADDON_RELEASE_BROWSER = op run --env-file=$(RELEASE_ENV_FILE) -- $(RELEASE_BIN) --project .
export UV_CACHE_DIR

PY_FILES := $(shell git ls-files --cached --others --exclude-standard '*.py' ':!:out/**' ':!:dist/**' ':!:node_modules/**' ':!:.venv/**' ':!:input/**' ':!:media/**' ':!:backups/**' ':!:templates/**' ':!:drafts/**' ':!:_vendor/**')
MYPY_FILES := $(shell git ls-files --cached --others --exclude-standard '*.py' ':!:tests/**' ':!:out/**' ':!:dist/**' ':!:node_modules/**' ':!:.venv/**' ':!:input/**' ':!:media/**' ':!:backups/**' ':!:templates/**' ':!:drafts/**' ':!:_vendor/**')
JS_FILES := $(shell git ls-files --cached --others --exclude-standard '*.js' '*.mjs' ':!:out/**' ':!:dist/**' ':!:node_modules/**')
SHELL_FILES := $(shell git ls-files --cached --others --exclude-standard '*.sh')

.PHONY: help lint lint-paths lint-python lint-js lint-shell type test release release-check release-package release-inspect release-dry-run release-handoff release-login release-publish check

help:
	@printf "Available targets:\n"
	@printf "  make lint   Run linters and source hygiene checks\n"
	@printf "  make type   Run type checks where typed source exists\n"
	@printf "  make test   Run unit tests and repository hygiene tests\n"
	@printf "  make release  Validate, package, inspect, and dry-run AnkiWeb release\n"
	@printf "  make release-handoff  Write handoff files for browser/manual AnkiWeb upload\n"
	@printf "  make release-login    Log in to AnkiWeb through the release browser profile\n"
	@printf "  make release-publish  Fill the AnkiWeb publish form through the release browser\n"
	@printf "  make check  Run lint, type, and test\n"

lint: lint-paths lint-python lint-js lint-shell

lint-paths:
	@$(PYTHON) tests/test_repo_hygiene.py --path-only

lint-python:
	@if [ -n "$(PY_FILES)" ]; then \
		if [ -f pyproject.toml ]; then \
			$(RUFF) check $(PY_FILES); \
		else \
			$(PYTHON) -m compileall -q $(PY_FILES); \
		fi; \
	else \
		printf "No Python files to lint.\n"; \
	fi

lint-js:
	@if [ -n "$(JS_FILES)" ]; then \
		for file in $(JS_FILES); do node --check "$$file"; done; \
	else \
		printf "No JavaScript files to lint.\n"; \
	fi

lint-shell:
	@if [ -n "$(SHELL_FILES)" ]; then \
		for file in $(SHELL_FILES); do bash -n "$$file"; done; \
	else \
		printf "No shell files to lint.\n"; \
	fi

type:
	@if [ -n "$(MYPY_FILES)" ]; then \
		if [ -f pyproject.toml ]; then \
			$(MYPY) $(MYPY_FILES); \
		else \
			$(PYTHON) -m compileall -q $(MYPY_FILES); \
		fi; \
	else \
		printf "No Python files to type-check.\n"; \
	fi
	@if [ -f package.json ] && node -e "const p=require('./package.json'); process.exit(p.scripts && p.scripts.typecheck ? 0 : 1)"; then \
		npm run typecheck; \
	elif [ -f package.json ]; then \
		printf "No npm typecheck script configured.\n"; \
	fi

test:
	@if [ -d tests ]; then $(PYTHON) -m unittest discover -s tests -v; fi
	@if [ -f package.json ] && node -e "const p=require('./package.json'); process.exit(p.scripts && p.scripts.test ? 0 : 1)"; then \
		npm test; \
	fi

release: release-check release-package release-inspect release-dry-run

release-check:
	@$(ANKI_ADDON_RELEASE) check

release-package:
	@$(ANKI_ADDON_RELEASE) package

release-inspect: release-package
	@$(ANKI_ADDON_RELEASE) inspect $(RELEASE_ARTIFACT)

release-dry-run: release-package
	@$(ANKI_ADDON_RELEASE) publish --dry-run

release-handoff: release-package
	@$(ANKI_ADDON_RELEASE) handoff

release-login:
	@$(ANKI_ADDON_RELEASE_BROWSER) login --submit-login --diagnostics-dir $(RELEASE_DIAGNOSTICS_DIR)

release-publish:
	@$(ANKI_ADDON_RELEASE_BROWSER) publish --diagnostics-dir $(RELEASE_DIAGNOSTICS_DIR)

check: lint type test
