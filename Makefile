.DEFAULT_GOAL := ajuda
SHELL := /bin/bash
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
TAILWIND := tools/tailwindcss

# Runtime de container: usa podman se disponível (rootless, padrão no Fedora),
# senão docker. Sobrescreva com: make COMPOSE="docker compose" infra-up
COMPOSE ?= $(shell command -v podman >/dev/null 2>&1 && echo "podman compose" || echo "docker compose")

# Porta da aplicação. Sobrescreva com: make PORTA=8010 dev
PORTA ?= 8000

.PHONY: ajuda bootstrap venv deps infra-up infra-down migrate seed seed-demo \
        dev worker css css-watch lint fmt tipos test test-rapido ci limpar \
        nova-migration psql

ajuda:  ## Lista os alvos disponíveis
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Setup -----------------------------------------------------------------
venv:  ## Cria a venv com Python 3.12
	python3.12 -m venv $(VENV)
	$(PIP) install -U pip

deps: venv  ## Instala o projeto em modo editável com as deps de dev
	$(PIP) install -e ".[dev]"

bootstrap: deps infra-up  ## Setup completo: venv + deps + banco + migrations + seed
	@test -f .env || cp .env.example .env
	./scripts/esperar_postgres.sh
	$(MAKE) migrate
	$(MAKE) seed
	@echo ""
	@echo "Pronto. Rode 'make dev' e abra http://localhost:$(PORTA)"

# --- Infra -----------------------------------------------------------------
infra-up:  ## Sobe Postgres+pgvector e Mailpit
	$(COMPOSE) up -d db mailpit

infra-down:  ## Derruba os containers (mantém os dados no volume pgdata)
	$(COMPOSE) down

psql:  ## Abre um psql no banco de desenvolvimento
	$(COMPOSE) exec db psql -U psiconnect -d psiconnect

# --- Banco -----------------------------------------------------------------
migrate:  ## Aplica todas as migrations
	$(VENV)/bin/alembic upgrade head

nova-migration:  ## Gera uma migration. Uso: make nova-migration m="descricao"
	$(VENV)/bin/alembic revision --autogenerate -m "$(m)"

seed:  ## Popula taxonomias, planos, parâmetros e termos
	$(PY) -m app.seeds

seed-demo:  ## Cria usuários e dados de demonstração
	$(PY) -m app.seeds --demo

# --- Execução --------------------------------------------------------------
dev:  ## Sobe a aplicação com reload (make PORTA=8010 dev para trocar a porta)
	$(VENV)/bin/uvicorn app.main:app --reload --port $(PORTA)

worker:  ## Sobe o worker (outbox, salas T-20min, expiração de reservas)
	$(PY) -m app.workers

# --- CSS (só dev; produção usa o app/static/css/app.css commitado) ---------
css:  ## Compila o Tailwind uma vez
	$(TAILWIND) -i app/static/src/input.css -o app/static/css/app.css --minify

css-watch:  ## Compila o Tailwind em modo watch
	$(TAILWIND) -i app/static/src/input.css -o app/static/css/app.css --watch

# --- Qualidade -------------------------------------------------------------
lint:  ## ruff check + format --check
	$(VENV)/bin/ruff check app tests
	$(VENV)/bin/ruff format --check app tests

fmt:  ## Formata e corrige o que der
	$(VENV)/bin/ruff check --fix app tests
	$(VENV)/bin/ruff format app tests

tipos:  ## mypy strict
	$(VENV)/bin/mypy app

test:  ## Suíte completa com cobertura
	$(VENV)/bin/pytest --cov=app --cov-report=term-missing

test-rapido:  ## Só os testes que não exigem infra
	$(VENV)/bin/pytest -m "not lento and not e2e"

ci: lint tipos test  ## Tudo o que o CI roda

limpar:  ## Remove caches
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
