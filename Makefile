.PHONY: format format-check lint typecheck test all clean

# The gate. Checks only — `make all` never rewrites files. CI runs this.
all: format-check lint typecheck test

# Rewrite code to house style (the only writing target).
format:
	poetry run black .

# Fail (without writing) if formatting is off.
format-check:
	poetry run black --check .

# Lint.
lint:
	poetry run flake8 .

# Static types (snippets/ is scratch, not shipped).
typecheck:
	poetry run mypy . --exclude 'snippets/'

# Tests with coverage.
test:
	poetry run pytest -v tests/ --cov=. --cov-report=term-missing

# Clean caches.
clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
