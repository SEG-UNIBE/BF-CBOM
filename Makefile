# Makefile for BF-CBOM multi-container system

# Variables
PROJECT_NAME := bf-cbom
DOCKER_COMPOSE := docker-compose
DOCKER := docker
UV := uv

# Colors for terminal output
BLUE := \033[36m
GREEN := \033[32m
YELLOW := \033[33m
RED := \033[31m
NC := \033[0m

.PHONY: help install dev build up down restart logs clean test lint format check-deps security-scan

help: ## Show this help message
	@echo "$(BLUE)BF-CBOM - Benchmarking Framework for Cryptographic Bill of Materials$(NC)"
	@echo ""
	@echo "$(GREEN)Available commands:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(BLUE)%-20s$(NC) %s\n", $$1, $$2}'

install: ## Install project dependencies with uv
	@echo "$(BLUE)Installing dependencies...$(NC)"
	$(UV) sync --all-extras

dev: ## Install development dependencies
	@echo "$(BLUE)Installing development dependencies...$(NC)"
	$(UV) sync --all-extras --dev

build: ## Build Docker images
	@echo "$(BLUE)Building Docker images...$(NC)"
	$(DOCKER_COMPOSE) build

build-no-cache: ## Build Docker images without cache
	@echo "$(BLUE)Building Docker images without cache...$(NC)"
	$(DOCKER_COMPOSE) build --no-cache

up: ## Start all services
	@echo "$(BLUE)Starting all services...$(NC)"
	$(DOCKER_COMPOSE) up -d

up-build: ## Build and start all services
	@echo "$(BLUE)Building and starting all services...$(NC)"
	$(DOCKER_COMPOSE) up -d --build

down: ## Stop all services
	@echo "$(BLUE)Stopping all services...$(NC)"
	$(DOCKER_COMPOSE) down

down-volumes: ## Stop all services and remove volumes
	@echo "$(YELLOW)Stopping all services and removing volumes...$(NC)"
	$(DOCKER_COMPOSE) down -v

restart: ## Restart all services
	@echo "$(BLUE)Restarting all services...$(NC)"
	$(DOCKER_COMPOSE) restart

restart-api: ## Restart only the API service
	@echo "$(BLUE)Restarting API service...$(NC)"
	$(DOCKER_COMPOSE) restart api

logs: ## Show logs from all services
	@echo "$(BLUE)Showing logs from all services...$(NC)"
	$(DOCKER_COMPOSE) logs -f

logs-api: ## Show logs from API service
	@echo "$(BLUE)Showing logs from API service...$(NC)"
	$(DOCKER_COMPOSE) logs -f api

logs-worker: ## Show logs from worker service
	@echo "$(BLUE)Showing logs from worker service...$(NC)"
	$(DOCKER_COMPOSE) logs -f worker

logs-db: ## Show logs from database service
	@echo "$(BLUE)Showing logs from database service...$(NC)"
	$(DOCKER_COMPOSE) logs -f db

status: ## Show status of all services
	@echo "$(BLUE)Service status:$(NC)"
	$(DOCKER_COMPOSE) ps

shell-api: ## Open shell in API container
	@echo "$(BLUE)Opening shell in API container...$(NC)"
	$(DOCKER_COMPOSE) exec api /bin/bash

shell-db: ## Open shell in database container
	@echo "$(BLUE)Opening shell in database container...$(NC)"
	$(DOCKER_COMPOSE) exec db psql -U bf_cbom -d bf_cbom_db

test: ## Run tests
	@echo "$(BLUE)Running tests...$(NC)"
	$(UV) run pytest

test-docker: ## Run tests in Docker container
	@echo "$(BLUE)Running tests in Docker container...$(NC)"
	$(DOCKER_COMPOSE) run --rm api python -m pytest

lint: ## Run linting
	@echo "$(BLUE)Running linting...$(NC)"
	$(UV) run ruff check src/
	$(UV) run mypy src/

format: ## Format code
	@echo "$(BLUE)Formatting code...$(NC)"
	$(UV) run black src/
	$(UV) run ruff check --fix src/

check-deps: ## Check for dependency updates
	@echo "$(BLUE)Checking for dependency updates...$(NC)"
	$(UV) lock --upgrade

security-scan: ## Run security scan
	@echo "$(BLUE)Running security scan...$(NC)"
	$(UV) run safety check

clean: ## Clean up Docker resources
	@echo "$(YELLOW)Cleaning up Docker resources...$(NC)"
	$(DOCKER) system prune -f
	$(DOCKER) volume prune -f
	$(DOCKER) network prune -f

clean-all: ## Clean up all Docker resources including images
	@echo "$(RED)Cleaning up ALL Docker resources...$(NC)"
	$(DOCKER_COMPOSE) down -v --rmi all
	$(DOCKER) system prune -af

backup-db: ## Backup database
	@echo "$(BLUE)Backing up database...$(NC)"
	mkdir -p backups
	$(DOCKER_COMPOSE) exec -T db pg_dump -U bf_cbom bf_cbom_db > backups/bf_cbom_db_$(shell date +%Y%m%d_%H%M%S).sql

restore-db: ## Restore database (use: make restore-db BACKUP_FILE=backup.sql)
	@echo "$(BLUE)Restoring database from $(BACKUP_FILE)...$(NC)"
	$(DOCKER_COMPOSE) exec -T db psql -U bf_cbom -d bf_cbom_db < $(BACKUP_FILE)

monitoring: ## Open monitoring dashboards
	@echo "$(BLUE)Opening monitoring dashboards...$(NC)"
	@echo "Prometheus: http://localhost:9090"
	@echo "Grafana: http://localhost:3000 (admin/admin)"
	@echo "API Health: http://localhost:8000/health"

dev-setup: install ## Setup development environment
	@echo "$(GREEN)Development environment setup complete!$(NC)"
	@echo "Run '$(BLUE)make up$(NC)' to start the services"

production-deploy: build up ## Deploy to production
	@echo "$(GREEN)Production deployment complete!$(NC)"
	@echo "API available at: http://localhost:8000"
	@echo "Admin interface at: http://localhost:3000"

# Environment-specific commands
dev-up: ## Start services for development
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml up -d

prod-up: ## Start services for production
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker-compose.prod.yml up -d

# Quick development workflow
dev-workflow: dev build dev-up ## Complete development setup workflow
	@echo "$(GREEN)Development workflow complete!$(NC)"
	@echo "Services are running. Use '$(BLUE)make logs$(NC)' to view logs."