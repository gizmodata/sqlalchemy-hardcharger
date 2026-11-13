.PHONY: install lint format unit integration test suite readme all build clean

PYTHON ?= .venv/bin/python

install:
	uv venv
	uv pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy sqlalchemy_hardcharger

format:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

unit:
	$(PYTHON) -m pytest tests/unit

# Starts a real Hardcharger server (the `hardcharger` package); fails if it can't.
integration:
	$(PYTHON) -m pytest tests/integration

test:
	$(PYTHON) -m pytest

# SQLAlchemy's own dialect compliance suite, against a real server. Its pytest plugin takes
# over collection, so it is always a run of its own.
suite:
	$(PYTHON) -m pytest tests/sqlalchemy_suite -p no:cacheprovider -W ignore::pytest.PytestUnknownMarkWarning

# Every Python example in README.md, run as written against a real server.
readme:
	$(PYTHON) scripts/run_readme_examples.py

all: lint test suite readme

build:
	rm -rf dist
	$(PYTHON) -m build --sdist --wheel --outdir dist
	$(PYTHON) -m twine check --strict dist/*

clean:
	rm -rf dist build *.egg-info .pytest_cache .mypy_cache .ruff_cache
