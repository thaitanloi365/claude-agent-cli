.PHONY: all install test sync clean credits auth-status

# Default target
all: sync test

# Sync virtual environment dependencies with uv
sync:
	uv sync

# Install globally via uv tool (accessible anywhere in terminal)
install:
	uv tool install --editable . --force

# Run pytest test suite
test:
	uv run pytest -v

# Check credit status
credits:
	uv run cagent credits

# Check auth status
auth-status:
	uv run cagent auth status

# Clean up caches and temporary files
clean:
	rm -rf .pytest_cache .ruff_cache dist build *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
