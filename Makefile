SHELL := /bin/sh

UV ?= uv
BIN_DIR ?= $(HOME)/bin
WRAPPER_NAME ?= client-proxy
WRAPPER_PATH := $(BIN_DIR)/$(WRAPPER_NAME)
WRAPPER_SOURCE := $(CURDIR)/main.py
ENV_EXAMPLE_SOURCE := $(CURDIR)/.env.example
ENV_EXAMPLE_TARGET := $(BIN_DIR)/.env.example
COV_ARGS := --cov=main --cov-branch --cov-report=term-missing --cov-report=xml --cov-fail-under=95

.PHONY: setup install install-wrapper install-links install-env uninstall \
	format format-check lint typecheck test test-unit test-e2e check

setup:
	@$(UV) sync --dev --no-install-project

install: install-wrapper install-links install-env
	@echo "Installed $(WRAPPER_NAME) wrapper and tool links into $(BIN_DIR)"

install-wrapper:
	@mkdir -p "$(BIN_DIR)"
	@chmod +x "$(WRAPPER_SOURCE)"
	@ln -sf "$(WRAPPER_SOURCE)" "$(WRAPPER_PATH)"

install-links:
	@ln -sf "$(WRAPPER_PATH)" "$(BIN_DIR)/uv"
	@ln -sf "$(WRAPPER_PATH)" "$(BIN_DIR)/npm"
	@ln -sf "$(WRAPPER_PATH)" "$(BIN_DIR)/pnpm"

install-env:
	@mkdir -p "$(BIN_DIR)"
	@if [ ! -f "$(ENV_EXAMPLE_TARGET)" ]; then \
		cp "$(ENV_EXAMPLE_SOURCE)" "$(ENV_EXAMPLE_TARGET)"; \
		echo "Created $(ENV_EXAMPLE_TARGET)"; \
	else \
		echo "$(ENV_EXAMPLE_TARGET) already exists; leaving unchanged"; \
	fi

uninstall:
	@rm -f "$(BIN_DIR)/uv" "$(BIN_DIR)/npm" "$(BIN_DIR)/pnpm" "$(WRAPPER_PATH)"
	@echo "Removed wrapper links from $(BIN_DIR)"
	@echo "Kept $(ENV_EXAMPLE_TARGET)"

format:
	@$(UV) run --no-project ruff format main.py tests

format-check:
	@$(UV) run --no-project ruff format --check main.py tests

lint:
	@$(UV) run --no-project ruff check main.py tests

typecheck:
	@$(UV) run --no-project mypy main.py

test-unit:
	@PYTHONPATH="$(CURDIR)" $(UV) run --no-project pytest tests/test_main.py $(COV_ARGS)

test-e2e:
	@PYTHONPATH="$(CURDIR)" $(UV) run --no-project pytest tests/e2e -m e2e

test:
	@PYTHONPATH="$(CURDIR)" $(UV) run --no-project pytest tests $(COV_ARGS)

check: format-check lint typecheck test
