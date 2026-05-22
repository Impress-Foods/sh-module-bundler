.PHONY: install lint test precommit lock

install:
	uv sync --extra dev

lint:
	ruff check .

test:
	pytest

precommit:
	pre-commit run --all-files

lock:
	uv lock
