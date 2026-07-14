# CodeCartographer

Code intelligence system: indexes large repos into a symbol/call graph via
static analysis and AST-aware semantic chunks, then serves cited Q&A over the
codebase via an agentic retrieval layer.

Phase 1 (current): deterministic indexing foundation — tree-sitter based
Python/TypeScript parsing into a Postgres-backed symbol/call graph. No LLM
calls in this phase.

## Setup

```
make setup   # uv sync
make db-up   # start postgres+pgvector, run migrations
make index REPO=/path/to/repo
```
