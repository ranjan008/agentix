# Contributing to Agentix

Thank you for your interest in contributing! This document covers everything you need to get started.

---

## Table of contents

- [Who can contribute](#who-can-contribute)
- [Development setup](#development-setup)
- [Branch & PR workflow](#branch--pr-workflow)
- [Coding standards](#coding-standards)
- [Testing](#testing)
- [Commit message format](#commit-message-format)
- [Reviewing & merging](#reviewing--merging)

---

## Who can contribute

Agentix is open source and welcomes external contributions. To keep the codebase healthy:

- **Bug fixes and docs** — anyone can open a PR
- **New features** — open an issue first to discuss before writing code
- **Security fixes** — see [SECURITY.md](SECURITY.md); do NOT open a public issue
- **Breaking changes** — require maintainer approval before work begins

**Direct push to `main` is disabled.** All changes must go through a pull request with at least one approving review.

---

## Development setup

### Prerequisites

- Python 3.11+
- Git

### Steps

```sh
# 1. Fork the repo on GitHub, then clone your fork
git clone https://github.com/<your-username>/agentix.git
cd agentix

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

# 3. Install in editable mode with dev dependencies
python -m pip install -e ".[dev]"

# 4. Copy env template
cp .env.example .env
# Fill in at minimum: ANTHROPIC_API_KEY

# 5. Verify everything works
python -m pytest tests/ -v
python -m ruff check agentix/ tests/
python -m mypy agentix/ --ignore-missing-imports
```

---

## Branch & PR workflow

```
main          ← protected, never push directly
  └─ feat/my-feature      ← your work branch
  └─ fix/some-bug
  └─ docs/update-readme
  └─ chore/dependency-bump
```

1. **Create a branch** from the latest `main`:
   ```sh
   git checkout main && git pull
   git checkout -b feat/my-feature
   ```

2. **Make your changes**, keeping commits focused (one logical change per commit).

3. **Run checks locally before pushing** (same checks CI runs):
   ```sh
   python -m ruff check agentix/ tests/
   python -m mypy agentix/ --ignore-missing-imports
   python -m pytest tests/ -v --tb=short
   ```

4. **Push and open a PR** against `main`. Fill out the PR template fully.

5. **Address review feedback** — push fixup commits, don't force-push during review.

6. **Squash-merge** is the default merge strategy on `main`.

---

## Coding standards

| Tool | Purpose | Config |
|---|---|---|
| `ruff` | Linting + import sorting | `pyproject.toml` |
| `mypy` | Static type checking | `pyproject.toml` |
| `black` | Formatting (via ruff format) | `pyproject.toml` |

Rules of thumb:

- Add type annotations to all public functions and methods
- Keep functions small and single-purpose
- No commented-out code in PRs
- No `print()` in library code — use `logging`
- Secrets and credentials must **never** be hardcoded; read from env vars
- New channel adapters must implement `start()` / `stop()` and inherit from nothing (duck-typed)
- New LLM providers must subclass `BaseLLMProvider` and implement `complete()`

---

## Testing

Tests live in `tests/`. Run them with:

```sh
python -m pytest tests/ -v --tb=short
```

Guidelines:
- Every bug fix should include a regression test
- New features should include at least one happy-path and one error-path test
- Tests must not make real HTTP calls — mock external services
- Async tests use `pytest-asyncio` (already configured in `pyproject.toml`)

---

## Commit message format

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <short summary>

[optional body]
```

Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`

Examples:
```
feat: add Telegram long-polling channel adapter
fix: resolve mypy type errors in identity provider
docs: add enterprise RBAC usage examples
chore: bump anthropic SDK to 0.25
```

PR titles must also follow this format — enforced by CI.

---

## Reviewing & merging

- PRs require **1 approving review** from a maintainer
- CI (lint + type check + tests) must be green
- Changes to `agentix/security/`, `agentix/llm/`, or core watchdog require review from a `@security-reviewers` or `@core-maintainers` team member (enforced via CODEOWNERS)
- Maintainers will not merge PRs that:
  - Lower test coverage on changed files
  - Skip or suppress linting/type errors
  - Add dependencies without discussion
