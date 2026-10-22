# ====== Project Configuration (edit only here) ======
VERSION := $(shell cat VERSION 2>/dev/null || echo "0.0.0")
BASE_IMAGE := bf-cbom/base
# Git commit of this checkout, stamped into every image (ENV BF_CBOM_COMMIT, last layer) and every JobResult;
# `-dirty` means the images were built from uncommitted changes
BF_CBOM_COMMIT := $(shell git describe --always --dirty --abbrev=12 2>/dev/null || echo unknown)
# Comma-separated list of all workers available in the repo
AVAILABLE_WORKERS := skeleton,testing,mssbomtool,cbomkit,cdxgen,cryptobomforge,deepseek,cryptoscan,chatgpt,claude,pqcheck,qdarpan,securin-xbom,cipherradar,xgrep,cbomkit-jar,ciphersift,scanoss,qelens,cryptarium,qscan
# Comma-separated list of dev-only workers
DEV_WORKERS := skeleton,testing,mssbomtool
# Batch runs (make experiment): memory budget in GiB = sum of the compose memory limits of the workers
# fed at the same time (common/tools.yaml memory_gb). Tools beyond the budget run in later waves.
# Laptop default 14 (Docker Desktop VM ~20 GiB); on a big VM set 0 (no limit) or e.g. 64.
# Override per call: make experiment EXP=... EXPERIMENT_MEMORY_BUDGET_GB=0
EXPERIMENT_MEMORY_BUDGET_GB ?= 14
# Memory cap of the git server (compose limit). A full clone runs git pack-objects inside this cgroup; 256m killed it
# on keycloak and ansible (CBOMkit's JGit clone, 2026-10-19). The cap also bounds the corpus: repos whose packed size
# on GitHub exceeds CORPUS_MAX_REPO_SIZE_MB (default GITSERVER_MEMORY/4, i.e. 1024 MB) are not sampled.
GITSERVER_MEMORY ?= 4g
CORPUS_MAX_REPO_SIZE_MB ?=
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

# NO_CACHE=1 rebuilds every layer from the Dockerfiles (e.g. `make up-prod NO_CACHE=1`)
NO_CACHE ?= 0
NO_CACHE_FLAG := $(if $(filter 1,$(NO_CACHE)),--no-cache,)

build-base:
	docker build $(NO_CACHE_FLAG) -f docker/Dockerfile.base \
		-t $(BASE_IMAGE):$(VERSION) .

release:
	bash ./scripts/bump_version.sh $(VERSION)

# Helper to build all services (of the active profile) with the correct base tag.
# SERIAL_BUILD=1 (default) builds one service at a time to avoid OOM in the Docker VM;
# use SERIAL_BUILD=0 for a faster parallel build on machines with plenty of RAM.
SERIAL_BUILD ?= 1
build-all: ensure-env build-base
ifeq ($(SERIAL_BUILD),1)
	@for svc in $$(docker compose config --services) runner worker-chatgpt worker-claude; do \
		echo ">>> building $$svc"; docker compose --profile tools --profile llm build $(NO_CACHE_FLAG) $$svc || exit 1; \
	done
else
	docker compose --profile tools --profile llm build $(NO_CACHE_FLAG)
endif

# Dev: only the explicitly listed dev workers
up-dev: export COMPOSE_PROFILES := dev
up-dev: export AVAILABLE_WORKERS := $(DEV_WORKERS)
up-dev: build-all
	docker compose up

# Prod: all workers except the dev-only ones, started in the background; then the live board (`make board`; Ctrl-C
# closes it, the stack keeps running: `make logs` for the raw logs, `make down` stops it). Opt-in groups via
# EXTRA_PROFILES, e.g.
#   make up-prod EXTRA_PROFILES=llm   (chatgpt + claude workers; need API keys in docker/env/)
# A service that is not up within the wait shows on the board (as down), so make goes on.
up_detached = docker compose up -d --wait --wait-timeout 300 $(1) \
	|| echo "some services are not up yet; the board shows which"
EXTRA_PROFILES ?=
up-prod: export COMPOSE_PROFILES := prod$(if $(EXTRA_PROFILES),$(comma)$(EXTRA_PROFILES),)
up-prod: export AVAILABLE_WORKERS := $(PROD_WORKERS)
up-prod: build-all
	$(call up_detached)
	@$(MAKE) --no-print-directory board

# Prod from prebuilt GHCR images (no local build; see .github/workflows/images.yml)
up-prod-pull: export COMPOSE_PROFILES := prod$(if $(EXTRA_PROFILES),$(comma)$(EXTRA_PROFILES),)
up-prod-pull: export AVAILABLE_WORKERS := $(PROD_WORKERS)
up-prod-pull: ensure-env
	docker compose pull
	$(call up_detached,--no-build)
	@$(MAKE) --no-print-directory board

# All: exactly what's in AVAILABLE_WORKERS
up-all: export COMPOSE_PROFILES := all
up-all: build-all
	docker compose up

down:
	docker compose down

# The live board of the running stack (misc/cli/stack_cmd.py): experiments, workers, queues; ARGS="--once" prints one
# snapshot. BOARD_SERVICES: every container of the stack, running or exited, so a crashed worker shows as down.
board: ensure-env
	docker compose run --rm -e TERM -e COLORTERM -e BF_CBOM_VERSION=$(VERSION) \
		-e BOARD_SERVICES="$$(docker ps -a --filter label=com.docker.compose.project=bf-cbom --format '{{.Label "com.docker.compose.service"}}' | sort -u | paste -sd, -)" \
		runner stack board $(ARGS)

# ---- Batch experiments (results stay in the results-data volume; see experiments/README.md) ----
# make experiment EXP=experiments/e4-mutation-smoke.yaml
# EXPERIMENT_IMAGE_IDS: the worker images on this host by content (recorded per session in <exp>/_run-log.json).
# EXPERIMENT_REPLICAS="cryptoscan=3,xgrep=2": replicas started with `docker compose up -d --scale worker-<tool>=N`;
# the memory budget then counts each replica (README "Parallelism and memory").
EXPERIMENT_REPLICAS ?=
experiment: ensure-env
	EXPERIMENT_IMAGE_IDS="$$(docker images --no-trunc --format '{{.Repository}}:{{.Tag}}={{.ID}}' --filter 'reference=bf-cbom/*:$(VERSION)' | paste -sd ',' -)" \
	docker compose run --rm -e EXPERIMENT_IMAGE_IDS -e EXPERIMENT_REPLICAS runner experiment run /app/$(EXP) $(ARGS)

# make experiment-status NAME=e4-mutation-smoke   /   make experiment-list
experiment-status:
	docker compose run --rm runner experiment status $(NAME)

experiment-list:
	docker compose run --rm runner experiment list

# make experiment-collect NAME=e4-mutation-smoke  -> collect results of jobs already queued (after `ARGS=--no-wait`)
experiment-collect:
	docker compose run --rm runner experiment collect $(NAME) $(ARGS)

# make experiment-cancel NAME=e4-mutation-smoke  -> remove its queued jobs from the worker queues
experiment-cancel:
	docker compose run --rm runner experiment cancel $(NAME)

# make experiment-delete NAME=e4-mutation-smoke  -> removes <name> from the results-data volume (asks first; ARGS=--yes skips)
experiment-delete:
	docker compose run --rm runner experiment delete $(NAME) $(ARGS)

# make experiment-export NAME=e4-mutation-smoke  -> experiments/results/<name>.zip;
# runs `analysis report` first (ARGS=--no-report skips it)
experiment-export:
	mkdir -p experiments/results
	docker compose run --rm -v "$(CURDIR)/experiments/results:/export" runner experiment export $(NAME) --dest /export $(ARGS)

# make corpus ARGS="--per-language 100"  -> results volume: corpus/corpus.yaml (export with experiment-export-corpus)
corpus: ensure-env
	docker compose run --rm runner experiment corpus build $(ARGS)

# make corpus-prune ARGS="--in /data/results/corpus/corpus.yaml --from e0-stability"  -> drops repos a tool timed out on
corpus-prune:
	docker compose run --rm runner experiment corpus prune $(ARGS)

corpus-export:
	mkdir -p experiments/results
	docker compose run --rm -v "$(CURDIR)/experiments/results:/export" runner sh -c 'cp /data/results/corpus/*.yaml /export/'

# make analysis ARGS="report e0-stability"  /  ARGS="micro e0-micro"  -> results volume: <exp>/_ANALYSIS/
analysis:
	docker compose run --rm --no-deps runner analysis $(ARGS)

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

.PHONY: clean-docker build-base build-all release up-dev up-prod up-prod-pull up-all down board logs ps show-workers \
	experiment experiment-status experiment-list experiment-collect experiment-cancel experiment-delete \
	experiment-export corpus corpus-prune corpus-export analysis clean-cache

# Frees Docker's build cache and dangling images (safe: volumes and tagged images are kept).
# Repeated rebuilds of the large worker images fill the Docker VM disk ("No space left on device").
clean-cache:
	docker builder prune -f
	docker image prune -f

# Removes BF-CBOM's containers (all profiles) and images only; other Docker projects are untouched.
# The redis-data volume (stored inspections) is kept; use `docker compose down -v` to drop it too.
clean-docker:
	docker compose --profile '*' down --remove-orphans
	@ids=$$(docker images -q --filter 'reference=bf-cbom/*' --filter 'reference=ghcr.io/seg-unibe/bf-cbom-*' | sort -u); \
	if [ -n "$$ids" ]; then docker rmi -f $$ids; else echo "no BF-CBOM images to remove"; fi
