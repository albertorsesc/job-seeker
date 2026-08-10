# The gate. Nothing gets committed until `make test` is green.
#
# Order is deliberate: fix what a machine can fix, then run the checks that need a human,
# cheapest first. Otherwise the one real failure arrives buried in formatting noise.
#
# Tools come from .venv rather than PATH, so this cannot silently pass against whatever ruff
# happens to be installed globally. Run `uv pip install -e ".[dev,mcp]"` first.

BIN := .venv/bin

.DEFAULT_GOAL := test

.PHONY: test
test:
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .
	$(BIN)/mypy
	$(BIN)/pytest

# Deliberately not part of `test`. The gate runs in under a second, and that speed is why it gets
# run before every commit; instrumenting every line would trade the habit for a number. Run this
# when changing what the tests reach, not on every edit.
.PHONY: coverage
coverage:
	$(BIN)/pytest --cov --cov-report=term-missing
