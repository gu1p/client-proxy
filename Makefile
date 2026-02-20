SHELL := /bin/sh

BIN_DIR ?= $(HOME)/bin
WRAPPER_NAME ?= client-proxy
WRAPPER_PATH := $(BIN_DIR)/$(WRAPPER_NAME)
WRAPPER_SOURCE := $(CURDIR)/main.py
ENV_EXAMPLE_SOURCE := $(CURDIR)/.env.example
ENV_EXAMPLE_TARGET := $(BIN_DIR)/.env.example
PYTHON ?= python3

.PHONY: install install-wrapper install-links install-env uninstall test

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

test:
	@$(PYTHON) -m unittest discover -s tests -v
