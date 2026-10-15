# ====== Project Configuration (edit only here) ======
VERSION := $(shell cat VERSION 2>/dev/null || echo "0.0.0")
BASE_IMAGE := bf-cbom/base
# Comma-separated list of all workers available in the repo
AVAILABLE_WORKERS := skeleton,testing,mssbomtool,cbomkit,cdxgen,cryptobomforge,deepseek
# Comma-separated list of dev-only workers
DEV_WORKERS := skeleton,testing,mssbomtool
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
		-t $(BASE_IMAGE):$(VERSION) .

release:
	bash ./scripts/bump_version.sh $(VERSION)

# Helper to build all services (of the active profile) with the correct base tag.
# SERIAL_BUILD=1 (default) builds one service at a time to avoid OOM in the Docker VM;
# use SERIAL_BUILD=0 for a faster parallel build on machines with plenty of RAM.
SERIAL_BUILD ?= 1
build-all: ensure-env build-base
ifeq ($(SERIAL_BUILD),1)
	@for svc in $$(docker compose config --services); do \
		echo ">>> building $$svc"; docker compose build $$svc || exit 1; \
	done
else
	docker compose build
endif

# Dev: only the explicitly listed dev workers
up-dev: export COMPOSE_PROFILES := dev
up-dev: export AVAILABLE_WORKERS := $(DEV_WORKERS)
up-dev: build-all
	docker compose up

# Prod: all workers except the dev-only ones
up-prod: export COMPOSE_PROFILES := prod
up-prod: export AVAILABLE_WORKERS := $(PROD_WORKERS)
up-prod: build-all
	docker compose up

# Prod from prebuilt GHCR images (no local build; see .github/workflows/images.yml)
up-prod-pull: export COMPOSE_PROFILES := prod
up-prod-pull: export AVAILABLE_WORKERS := $(PROD_WORKERS)
up-prod-pull: ensure-env
	docker compose pull
	docker compose up --no-build

# All: exactly what's in AVAILABLE_WORKERS
up-all: export COMPOSE_PROFILES := all
up-all: build-all
	docker compose up

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

.PHONY: prune prune-build clean-docker build-base build-all release up-dev up-prod up-prod-pull up-all down logs ps show-workers

prune:
	docker system prune -af

prune-build:
	docker builder prune -af

clean-docker: prune prune-build
