.PHONY: test lint format static_type_check setup-hooks clean all

# Default target
all: black lint static_type_check test

# Run tests with pytest
test:
	@echo "Running tests with coverage..."
	poetry run pytest -v tests/ --cov=. --cov-report=term-missing

# Run linting with flake8
lint:
	@echo "Running linter..."
	poetry run flake8 .

# Format code with black
black:
	@echo "Running formatter..."
	poetry run black .

# Run static type checking with mypy
static_type_check:
	@echo "Running static type checker..."
	poetry run mypy . --exclude 'snippets/'

# Setup pre-commit hooks
setup-hooks:
	@echo "Setting up git hooks..."
	git config core.hooksPath .githooks
	chmod +x .githooks/pre-commit

# Clean up cache directories
clean:
	@echo "Cleaning up..."
	rm -rf .pytest_cache
	rm -rf .mypy_cache
	rm -rf .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +