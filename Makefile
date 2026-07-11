# Decima 0.3 developer commands (handoff §13.1).
# Legacy reference implementation lives in ./heartbeat and is exercised by `make smoke`.

.PHONY: help install-dev format lint type test smoke run build clean check

help:
	@echo "Decima 0.3 make targets:"
	@echo "  make install-dev   editable install + dev tooling"
	@echo "  make format        ruff format (new code only; heartbeat excluded)"
	@echo "  make lint          ruff check"
	@echo "  make type          mypy (strict on decima/, legacy excluded)"
	@echo "  make test          pytest"
	@echo "  make smoke         run the legacy heartbeat oracle (the frozen baseline)"
	@echo "  make check         format-check + lint + type + test (the CI gate, local)"
	@echo "  make build         build the wheel/sdist"
	@echo "  make run           run the legacy Decima heartbeat"

install-dev:
	python3 -m pip install -e ".[dev]"

format:
	ruff format .

lint:
	ruff format --check .
	ruff check .

type:
	mypy

test:
	pytest

smoke:
	cd heartbeat && python3 -u smoke.py

run:
	cd heartbeat && python3 run.py

build:
	python3 -m build

check: lint type test

clean:
	rm -rf build dist *.egg-info .mypy_cache .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
