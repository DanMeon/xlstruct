# Contributing to XLStruct

Thanks for your interest in contributing! AI-assisted contributions (issue creation, coding, reviews) are welcome.

## Before You Submit

- `uv sync --group dev` to install dependencies
- `uv run pytest tests/ -v --ignore=tests/integration` must pass
- `uv run ruff check src/ tests/` and `uv run mypy src/xlstruct/` must pass
- Pre-commit hooks run these automatically

## Code Style

- Python 3.11+, `T | None` (not `Optional[T]`), Pydantic V2

## Pull Requests

1. Fork → feature branch → make changes with tests → PR against `main`
2. Keep PRs focused — one feature or fix per PR
