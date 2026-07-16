# Phase 2: AST-aware semantic chunks in pgvector

Status: Implemented

## Goal

Give every symbol found in Phase 1 a vector embedding representing its *meaning*, stored in
Postgres via pgvector, so it can be found by semantic similarity search rather than only by
exact name/structure. This is the second of the two maps (structural + semantic) that Phase 3's
agentic Q&A layer will need. No LLM/agent/chat code and no web API in this phase — same
restraint as Phase 1, just extending which pieces of infrastructure exist.

## Scope

- In scope: chunking symbols into embeddable text, generating embeddings with a **local** model
  (no API key, no external network calls), storing them in a new pgvector-backed table keyed to
  existing `nodes.id`, a vector similarity index, and a `codecart search "<query>"` CLI command
  that returns ranked results (file/symbol/line — no generated answer).
- Out of scope: Phase 3's agent/chat/cited-answer layer, a web API, incremental
  (diff-based) re-embedding, hybrid/re-ranked search, embedding FILE-level nodes (only
  FUNCTION/METHOD/CLASS for now — a file's "meaning" is really just the sum of its symbols).

## Design

**Embedding model**: `sentence-transformers` running `all-MiniLM-L6-v2` locally (384-dimensional
vectors). Chosen for being small, fast on CPU, and dependency-light relative to code-specialized
alternatives — a reasonable first pass, swappable later behind one function if quality isn't
good enough once we're actually using it in Phase 3.

**Chunk text** per symbol = signature + docstring (if any) + the actual source body, sliced from
the file using the `start_line`/`end_line` we already store on each `Node`. This is new work —
Phase 1 never re-reads a symbol's body text, only extracts it from the AST during parsing.

**Schema** (new Alembic migration, additive — doesn't touch Phase 1's tables):

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE chunks (
    id              BIGSERIAL PRIMARY KEY,
    indexing_run_id BIGINT NOT NULL REFERENCES indexing_runs(id) ON DELETE CASCADE,
    node_id         BIGINT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    content         TEXT NOT NULL,           -- the exact text that was embedded (for debugging/display)
    embedding       VECTOR(384) NOT NULL,
    model_name      TEXT NOT NULL,           -- e.g. "all-MiniLM-L6-v2" -- lets us tell old/new embeddings apart if the model ever changes
    UNIQUE (indexing_run_id, node_id)
);

CREATE INDEX idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
```

Follows Phase 1's existing pattern: scoped by `indexing_run_id`, cascade-deleted on re-index via
the same `passive_deletes=True` approach already in `db/models.py`.

**Pipeline addition**: a new `embedder.py` runs after `indexer.py` persists nodes (needs real
`node.id`s to key off of) — for each FUNCTION/METHOD/CLASS node, build its chunk text, embed it,
insert into `chunks`. Wired into `codecart index` as an additional step, not a separate command
(consistent with "index = build the whole map").

**Search**: `codecart search "<query>" --repo <path> --limit N` embeds the query with the same
model, then does a pgvector `ORDER BY embedding <=> :query_embedding LIMIT N` against the latest
run for that repo, printing `file_path:start_line`, symbol name, and a snippet.

**pgvector blocker, as actually resolved**: rather than upgrading the local Postgres version (which
would have diverged from `docker-compose.yml`'s pg16 pin), built `pgvector` v0.8.5 from source
against postgresql@16 directly (`make PG_CONFIG=.../pg16/bin/pg_config install`) — same version
Homebrew already ships a binary for, just compiled for the Postgres version this project actually
targets. Verified via `CREATE EXTENSION` + a real distance query before building anything on top
of it.

**Additional fix made along the way**: `Embedder` originally called out to Hugging Face Hub on
every load (checking for model updates) even with a fully cached model, which quietly violated
the "no external network calls" scope item above. Fixed by trying `local_files_only=True` first
and only falling back to a network fetch on a genuine first-ever run.

## Acceptance criteria

- [x] `pgvector` extension loads successfully in the dev database.
- [x] `codecart index <repo>` also populates `chunks`, one row per FUNCTION/METHOD/CLASS node,
      idempotent per commit same as Phase 1.
- [x] `codecart search "<query>" <repo>` returns ranked, plausible results against a real
      multi-file fixture (e.g. searching for "database connection setup" surfaces the actual
      DB-connection-related function, not an unrelated one).
- [x] Unit test(s) for chunk-text extraction (signature + docstring + body slicing).
- [x] Integration test: index a fixture repo, embed it, run a search, assert the expected symbol
      is in the top results.
- [x] `ruff check`, `mypy src`, full pytest suite clean, same bar as Phase 1.
