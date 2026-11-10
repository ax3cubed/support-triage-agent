.PHONY: setup test lint retrieval eval report demo

setup: ## Install dependencies and create .env
	uv sync
	test -f .env || cp .env.example .env

test: ## Offline tests: graph wiring, redaction, retrieval, evaluators, dataset shape
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

retrieval: ## Retrieval recall on raw ticket text (no key needed)
	uv run python -m evals.retrieval

NAME ?= baseline
PROMPT ?= v0
eval: ## Full evaluation as a LangSmith experiment: make eval NAME=baseline PROMPT=v0
	uv run python -m evals.run --name $(NAME) --prompt $(PROMPT)

report: ## Print one run, or compare two: make report RUNS="baseline v1"
	uv run python -m evals.report $(or $(RUNS),$(NAME))

CASE ?= e02
demo: ## Run one dataset case and print each step: make demo CASE=x01
	uv run triage --case $(CASE)
