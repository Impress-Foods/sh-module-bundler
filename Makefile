.PHONY: install lint test precommit lock

install:
	uv sync --extra dev

lint:
	uv run ruff check .

test:
	uv run pytest

precommit:
	uv run pre-commit run --all-files

lock:
	uv lock
