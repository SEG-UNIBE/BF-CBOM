# ====== Project Configuration (edit only here) ======
VERSION := $(shell cat VERSION 2>/dev/null || echo "0.0.0")
BASE_IMAGE := bf-cbom/base
# Comma-separated list of all workers available in the repo
AVAILABLE_WORKERS := skeleton,testing,cdxgen,cbomkit,deepseek,mssbomtool,cryptobomforge
# Comma-separated list of dev-only workers
DEV_WORKERS := skeleton,testing
# Export everything defined above to downstream commands for cross-shell support
.EXPORT_ALL_VARIABLES:
# ====================================================

# Directory holding env templates and concrete env files
ENV_DIR := docker/env
ENV_TEMPLATE_FILES := $(wildcard $(ENV_DIR)/*.env.template)
ENV_FILES := $(ENV_TEMPLATE_FILES:.env.template=.env)
ENSURE_ENV_SCRIPT := ./scripts/ensure_env.sh
# ====================================================

# --- internals: don't edit below ---
# helpers to convert comma-separated lists <-> space lists
comma := ,
empty :=
space := $(empty) $(empty)
to_space_list = $(subst $(comma),$(space),$(strip $1))
to_comma_list = $(subst $(space),$(comma),$(strip $1))

AVAILABLE_WORKERS_LIST := $(call to_space_list,$(AVAILABLE_WORKERS))
DEV_WORKERS_LIST       := $(call to_space_list,$(DEV_WORKERS))
PROD_WORKERS_LIST      := $(filter-out $(DEV_WORKERS_LIST),$(AVAILABLE_WORKERS_LIST))
PROD_WORKERS           := $(call to_comma_list,$(PROD_WORKERS_LIST))

build-base:
	docker build -f docker/Dockerfile.base \
		-t $(BASE_IMAGE):$(VERSION) \
		-t $(BASE_IMAGE):latest \
		-t bf-cbom/base:latest .

release:
	bash ./scripts/bump_version.sh $(VERSION)

# Helper to build all services with the correct base tag
build-all: ensure-env build-base
	docker compose build

# Dev: only the explicitly listed dev workers
up-dev: export COMPOSE_PROFILES := dev
up-dev: export AVAILABLE_WORKERS := $(DEV_WORKERS)
up-dev: build-all
	docker compose up --build

# Prod: all workers except the dev-only ones
up-prod: export COMPOSE_PROFILES := prod
up-prod: export AVAILABLE_WORKERS := $(PROD_WORKERS)
up-prod: build-all
	docker compose up --build

# All: exactly what's in AVAILABLE_WORKERS
up-all: export COMPOSE_PROFILES := all
up-all: build-all
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

# Sanity helper to see what lists resolve to
show-workers:
	@echo "AVAILABLE_WORKERS=$(AVAILABLE_WORKERS)"
	@echo "DEV_WORKERS=$(DEV_WORKERS)"
	@echo "PROD_WORKERS=$(PROD_WORKERS)"

ensure-env: $(ENV_FILES)

$(ENV_DIR)/%.env: $(ENV_DIR)/%.env.template
	@$(ENSURE_ENV_SCRIPT) $< $@

.PHONY: prune prune-build clean-docker build-base build-all release up-dev up-prod up-all down logs ps show-workers

prune:
	docker system prune -af --volumes

prune-build:
	docker builder prune -af

clean-docker: prune prune-build
