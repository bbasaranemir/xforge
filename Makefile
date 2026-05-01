.PHONY: help up down build restart status logs ingest report test lint format clean prune

SHELL := /bin/bash
MATCH_ID ?= 3869685

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ─── Docker ──────────────────────────────────────────────────────────

up: ## Build and start all services
	docker compose up -d --build

down: ## Stop all services
	docker compose down

build: ## Rebuild Airflow image only
	docker compose build airflow-webserver airflow-scheduler

restart: ## Restart all services
	docker compose restart

status: ## Show container status and health
	@docker compose ps
	@echo ""
	@echo "── Disk Usage ──"
	@df -h / 2>/dev/null | tail -1 || true
	@echo ""
	@echo "── Docker Disk ──"
	@docker system df 2>/dev/null || true

logs: ## Follow scheduler logs (Ctrl+C to stop)
	docker compose logs -f airflow-scheduler

logs-all: ## Follow all service logs
	docker compose logs -f

# ─── Pipeline ────────────────────────────────────────────────────────

ingest: ## Unpause and trigger ingestion_pipeline
	docker compose exec airflow-webserver airflow dags unpause ingestion_pipeline
	docker compose exec airflow-webserver airflow dags trigger ingestion_pipeline
	@echo "✅ ingestion_pipeline triggered"

train: ## Trigger ML training DAG
	docker compose exec airflow-webserver airflow dags unpause ml_training
	docker compose exec airflow-webserver airflow dags trigger ml_training
	@echo "✅ ml_training triggered"

report: ## Generate match report (usage: make report MATCH_ID=12345)
	docker compose exec airflow-webserver airflow dags unpause matchday_push
	docker compose exec airflow-webserver airflow dags trigger matchday_push \
		--conf '{"match_id": $(MATCH_ID)}'
	@echo "✅ matchday_push triggered for match $(MATCH_ID)"

dag-status: ## Show DAG run states
	docker compose exec airflow-webserver airflow dags list-runs -d ingestion_pipeline
	docker compose exec airflow-webserver airflow dags list-runs -d ml_training

# ─── Database ────────────────────────────────────────────────────────

db-shell: ## Open psql shell
	docker compose exec postgres psql -U $${POSTGRES_USER:-analytics} -d $${POSTGRES_DB:-football_analytics}

db-stats: ## Show database statistics
	@docker compose exec postgres psql -U $${POSTGRES_USER:-analytics} -d $${POSTGRES_DB:-football_analytics} -c \
		"SELECT 'matches' AS entity, COUNT(*) FROM dim_matches UNION ALL \
		 SELECT 'events', COUNT(*) FROM fact_events UNION ALL \
		 SELECT 'players', COUNT(*) FROM dim_players UNION ALL \
		 SELECT 'competitions', COUNT(*) FROM dim_competitions \
		 ORDER BY 1;"

# ─── Code Quality ────────────────────────────────────────────────────

lint: ## Run linters (black check + isort check + flake8)
	black --check scripts/ dags/ config/ tests/
	isort --check-only scripts/ dags/ config/ tests/
	flake8 scripts/ dags/ config/ tests/ --max-line-length=100 --extend-ignore=E203,W503

format: ## Auto-format code (black + isort)
	black scripts/ dags/ config/ tests/
	isort scripts/ dags/ config/ tests/

test: ## Run pytest with coverage
	pytest tests/ -v --cov=scripts --cov-report=term-missing

# ─── Cleanup ─────────────────────────────────────────────────────────

clean: ## Remove Python caches and dbt artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf dbt_project/target dbt_project/logs

prune: ## Remove unused Docker resources (images, volumes, cache)
	docker system prune -af --volumes
	@echo "✅ Docker resources pruned"
