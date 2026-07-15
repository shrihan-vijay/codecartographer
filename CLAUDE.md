# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CodeCartographer indexes large repos into (a) a symbol/call graph via static analysis and (b)
AST-aware semantic chunks in pgvector, then serves cited Q&A over the codebase via an agentic
retrieval layer. It's built in phases; **Phase 1 (current) is the deterministic indexing
foundation only — no LLM calls, no embeddings, no pgvector usage, no web API.** Those are
Phases 2-3. See `specs/` for the spec of each phase (read `specs/README.md` first).

## Commands

```
make setup              # uv sync
make db-up              # docker compose up postgres+pgvector, then alembic upgrade head
make db-down             # stop the postgres container
make index REPO=<path>   # uv run codecart index <path>
make test                # uv run pytest
make lint                # uv run ruff check . && uv run mypy src
```

Direct equivalents (useful for running a single test or one-off CLI commands):
```
uv run pytest tests/test_python_parser.py::test_nested_function_is_captured_with_correct_parent
uv run ruff format .
uv run codecart callers <symbol_name> --depth 3
uv run codecart callees <symbol_name> --depth 3
uv run codecart hotspots <repo_path>
uv run codecart stats
uv run alembic revision --autogenerate -m "message"
uv run alembic upgrade head
```

Tests require a live Postgres reachable via `CODECART_DATABASE_URL` (defaults to the
docker-compose credentials: `postgresql+psycopg://codecart:codecart@localhost:5432/codecart`).
`tests/conftest.py` creates tables via `Base.metadata.create_all` if missing and truncates all
tables after each test that uses the `db_session` fixture — it does not use a separate test
database, so don't point `CODECART_DATABASE_URL` at a database you care about when running tests.

If Docker isn't available, a local Postgres 16 + pgvector via Homebrew works too — pgvector
itself isn't required to be loadable for Phase 1 since no table uses a vector column yet.

## Architecture

**Pipeline**: `walker.py` (finds source files) → `parsers/*_parser.py` (tree-sitter → plain
dataclasses) → `resolution.py` (dataclasses → resolved edges, no DB) → `indexer.py`
(orchestrates the above and persists to Postgres via SQLAlchemy) → `queries.py` (recursive-CTE
graph reads) → `cli.py` (Typer commands wiring it together).

**Parsers are DB-decoupled by design.** `parsers/base.py` defines `ParsedSymbol`, `ParsedImport`,
`ParsedCall`, `ParseResult` — plain dataclasses with no SQLAlchemy dependency, so parser unit
tests run without a database. `python_parser.py` and `typescript_parser.py` each implement a
`.parse(file_path, source_bytes) -> ParseResult`. A symbol's `qualified_name` format differs by
language and is unambiguous by construction: Python uses dotted paths matching the module system
(`pkg.mod.Class.method`, with `module_name_from_path` stripping a leading `src/` per PEP
517/518 src-layout convention — imports won't resolve without this for src-layout repos, this
one included). TypeScript uses `path/to/file#Class.method` (no dotted-module convention exists in
TS, so the file path plus a `#`-separated symbol path avoids collisions).

**Resolution is deliberately best-effort with graceful failure, not exhaustive.** `resolution.py`
resolves CALLS via: `self.`/`this.`-prefixed calls resolve to a method on the nearest enclosing
class; other calls try intra-file match by name first, then same-package (only into files the
caller's file successfully imports). Anything else is written to `unresolved_calls` with a
`reason` rather than guessed — **resolution quality matters more than coverage** is a hard
constraint from the spec, not an oversight. IMPORTS edges follow the same philosophy: Python
relative imports are resolved via proper dot-level package-walking, TS relative imports via
path normalization + extension/index guessing; bare/absolute specifiers that don't match a
real file in the repo are simply dropped, not stored as unresolved.

**Everything is scoped by `indexing_run_id`.** There's no single global graph — `nodes`,
`edges`, `unresolved_calls`, and `file_metrics` all key off `indexing_runs(id)`, uniqued on
`(repo_path, commit_sha)`. Re-indexing an already-indexed commit deletes-and-replaces that run
(see `_replace_existing_run` in `indexer.py`) rather than appending or diffing. FILE and symbol
nodes share one `nodes` table (discriminated by `node_type`), and CONTAINS is a first-class edge
type (not a parent-pointer column) specifically so graph queries stay uniform across
CALLS/IMPORTS/CONTAINS via the same recursive-CTE shape (see `queries.py`).

**Node relationships use `passive_deletes=True`, not ORM cascade.** Deleting an `IndexingRun`
relies on the DB's `ON DELETE CASCADE` (declared on every child FK in `db/models.py`) rather than
SQLAlchemy loading and individually deleting children — combining ORM cascade with DB cascade
without `passive_deletes=True` either double-deletes (warnings) or nulls out NOT NULL FKs
(hard failure). Don't remove `passive_deletes=True` without re-testing a delete-and-replace cycle.

**Known gap**: definitions (functions/classes) nested inside `if`/`for`/`try`/`with` blocks are
not walked into by either parser — only direct children of a module/function/class body are
treated as definitions. This is intentional for Phase 1 (rare pattern, high complexity to handle
correctly); see the `_DEFINITION_TYPES`/`_OPAQUE_TYPES` comments in the parser files.

## Spec-driven development

Non-trivial work (a new phase, a significant feature) should have a spec in `specs/` written and
explicitly approved *before* implementation starts — see `specs/README.md` for the convention and
workflow. Small fixes (bug fixes, one-off corrections) don't need one.
