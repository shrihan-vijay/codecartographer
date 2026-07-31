# Phase 5: PyPI packaging (installable without a git clone)

Status: Implemented

## Goal

Let someone get the `codecart` CLI and the FastAPI backend via `pip install codecartographer`
instead of cloning the repo, so the Chrome extension (Phase 4) has something meaningfully easier
to point at than "clone this repo, `uv sync`, run from source." Publishing to PyPI itself (account,
`twine upload`/trusted publishing) is a manual step for the user to take, same as Phase 4's Chrome
Web Store submission, not something this phase automates.

## Scope

- In scope: bundling the Alembic migrations inside the installable package (previously they lived
  at the repo root, outside `src/`, so `pip install` alone never shipped them) and a `codecart
  db-init` command that runs them from the installed package, no `alembic.ini` or git clone needed.
- Out of scope: actually publishing to PyPI (account creation, API token or trusted-publishing
  GitHub Action, `twine upload`), bundling `docker-compose.yml` (Postgres itself still has to come
  from somewhere, a pip package can't include "a running Postgres"), a hosted/managed Postgres
  option.

## Design

**Root cause of the gap**: `[tool.hatch.build.targets.wheel] packages = ["src/codecartographer"]`
only ships what's under `src/`. `migrations/` lived at the repo root, so a `pip install` gave
someone the CLI with no way to create the schema it needs.

**Fix: move, don't duplicate.** `migrations/` moves to `src/codecartographer/migrations/` — one
copy, not two. This is picked up automatically by hatchling's existing wheel config (no new
`[tool.hatch.build]` entries needed, everything under `src/codecartographer/` ships, same as
`static/index.html` already does). The root `alembic.ini` used by `uv run alembic upgrade head`
during local dev keeps working unchanged in *usage*, only its `script_location` value moves to
point at the new path.

**`codecart db-init`** (`cli.py`): builds an Alembic `Config` object programmatically (no
`alembic.ini` file needed at runtime) with `script_location` set to
`Path(__file__).parent / "migrations"` — i.e. wherever the package actually got installed — and
calls `alembic.command.upgrade(cfg, "head")`. `migrations/env.py` already reads the DB URL from
`codecartographer.config.settings` rather than a static ini value, so it needs no changes at all.

**Not duplicating logic**: `make db-up` / `uv run alembic upgrade head` (repo-checkout dev
workflow) and `codecart db-init` (pip-install workflow) both ultimately run the exact same
migration scripts from the exact same directory. Two entry points, one source of truth.

## Acceptance criteria

- [x] `migrations/` moved to `src/codecartographer/migrations/` via `git mv`, preserving history.
- [x] `alembic.ini`'s `script_location` updated; `uv run alembic upgrade head` still works from
      a repo checkout.
- [x] `pyproject.toml`'s ruff `extend-exclude` updated to the new migrations path.
- [x] New `codecart db-init` command runs the bundled migrations via a programmatic Alembic
      `Config`, verified against the real dev database.
- [x] `specs/phase-1-indexing-foundation.md`'s reference to `migrations/versions/` updated to the
      new path so it doesn't silently go stale, per this project's own spec-maintenance rule.
