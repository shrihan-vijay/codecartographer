# Phase 1: deterministic indexing foundation

Status: Implemented

## Goal

Index large repos into a symbol/call graph via static analysis: parse Python and TypeScript
source with tree-sitter, extract symbols and edges, resolve what can be resolved with high
confidence, and persist it to Postgres so it can be queried. This is the foundation the rest of
CodeCartographer (semantic chunking, cited Q&A) builds on.

## Scope

- In scope: tree-sitter parsing (Python + TypeScript/TSX), symbol/edge extraction, best-effort
  call/import resolution with unresolved calls logged (not guessed), Postgres schema +
  Alembic migrations, a CLI (`index`, `callers`, `callees`, `hotspots`, `stats`), unit + one
  integration test.
- Out of scope (later phases): embeddings, pgvector usage, any LLM/agent code, a web API.

## Design

**Tech**: Python 3.11+/uv, src/ layout, `tree-sitter` + `tree-sitter-python` +
`tree-sitter-typescript`, Postgres 16 + pgvector via Docker Compose, SQLAlchemy 2.0 (typed) +
Alembic, Typer, Pydantic v2, pytest, ruff + mypy, structlog.

**Pipeline**: `walker.py` → `parsers/*_parser.py` → `resolution.py` → `indexer.py` → Postgres,
queried via `queries.py`. See CLAUDE.md's Architecture section for the detailed breakdown
(parser decoupling, qualified_name conventions per language, resolution strategy, run-scoping,
`passive_deletes` gotcha) — not duplicated here since that file is the one guaranteed to be read.

**Schema** (approved before implementation; see `migrations/versions/` for the DDL as shipped):
`indexing_runs` (repo_path + commit_sha unique, replace-on-reindex), `nodes` (FILE/FUNCTION/
METHOD/CLASS as one typed table, so pgvector semantic chunks in a later phase can FK to
`nodes.id` regardless of granularity), `edges` (CALLS/IMPORTS/CONTAINS, typed rather than
separate tables so graph queries stay uniform), `unresolved_calls` (calls that couldn't be
confidently resolved, with a `reason`), `file_metrics` (LOC, function_count, git_churn).

## Acceptance criteria

- [x] `codecart index <repo>` parses Python + TS/TSX, persists nodes/edges/metrics, prints a
      summary, and is idempotent per commit (re-indexing the same commit replaces, not appends).
- [x] `codecart callers`/`codecart callees` traverse CALLS edges via a recursive CTE with
      `--depth`.
- [x] `codecart hotspots <repo>` ranks files by churn × function_count.
- [x] `codecart stats` reports node/edge counts by type for the latest run.
- [x] Parser unit tests cover nested functions, decorators, class methods, TS arrow functions,
      and re-exports (`tests/test_python_parser.py`, `tests/test_typescript_parser.py`).
- [x] An integration test indexes a small fixture repo into a real Postgres and asserts specific
      CALLS/IMPORTS/CONTAINS edges exist (`tests/test_integration_index.py`).
- [x] `ruff check`, `mypy src`, and the full pytest suite are clean.
- [x] Verified end-to-end against a real repo (this one, self-indexed) — which is also how the
      src-layout module-resolution bug (fixed in a follow-up commit) was caught.
