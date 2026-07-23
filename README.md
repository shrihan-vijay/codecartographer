# CodeCartographer

CodeCartographer indexes large repos into (a) a symbol/call graph via static analysis and
(b) AST-aware semantic chunks in pgvector, then serves cited Q&A over the codebase via an
agentic retrieval layer running on a **local, offline LLM (Ollama)** — no API key, no
per-call cost, no code leaving your machine.

```
$ codecart ask "What does the walker module do?" .
... calling search_code({'query': 'walker module', 'limit': '5'})

The walker module is a recursive descent parser that traverses the directory tree
rooted at the specified repository path, yielding tuples of the current directory
path, subdirectories, and files. It also handles per-file scope and import
walking via `_walk_scope` and `_walk_imports`.

Citations:
  codecartographer.parsers.python_parser.PythonParser._walk_scope
  codecartographer.parsers.python_parser.module_name_from_path
  codecartographer.walker._walk
```

## What it does

- **Static analysis**: tree-sitter parses Python and TypeScript into a symbol/call graph
  (functions, classes, methods, CALLS/IMPORTS/CONTAINS edges), persisted in Postgres and
  queried via recursive CTEs (`callers`/`callees` at arbitrary depth, hotspot ranking).
  Resolution is deliberately best-effort with graceful failure — an unresolved call is
  recorded with a reason, not silently guessed.
- **Semantic search**: AST-aware chunking + local sentence-transformer embeddings
  (`all-MiniLM-L6-v2`) stored in pgvector, so search works on meaning, not just symbol names.
- **Agentic Q&A**: an agent (Ollama, `llama3.1` by default) with tools for graph traversal,
  semantic search, and source reading answers natural-language questions about the codebase
  and cites the exact symbols/lines it used.
- **Chrome extension**: a side-panel client (`extension/`) for the same `/ask` API, so you
  can ask questions about an indexed repo without leaving the browser.

Built in four phases — see [`specs/`](specs/) for the design doc behind each one:

| Phase | What it adds |
| --- | --- |
| 1 — Indexing foundation | tree-sitter parsing → symbol/call graph in Postgres |
| 2 — Embeddings | AST-aware chunking + pgvector semantic search |
| 3 — Agentic Q&A | tool-calling agent over Ollama, cited answers, FastAPI backend |
| 4 — Chrome extension | side-panel client on top of the Phase 3 API |

## Setup

Requires [uv](https://docs.astral.sh/uv/), Docker (or a local Postgres 16 + pgvector), and
[Ollama](https://ollama.com) for the Q&A layer.

```
make setup              # uv sync
make db-up               # docker compose up postgres+pgvector, then alembic upgrade head
ollama pull llama3.1     # one-time, ~4.7GB — only needed for `codecart ask`
```

## Usage

```
make index REPO=/path/to/repo   # or: uv run codecart index <path>

uv run codecart callers <symbol_name> --depth 3
uv run codecart callees <symbol_name> --depth 3
uv run codecart hotspots <repo_path>
uv run codecart search "<natural language query>" <repo_path>
uv run codecart ask "<question>" <repo_path>
uv run codecart stats
```

To use the web UI or the Chrome extension instead of the CLI for `ask`:

```
uv run uvicorn codecartographer.api:app
```

Then either open `http://localhost:8000` in a browser, or load `extension/` as an unpacked
Chrome extension (see [`extension/README.md`](extension/README.md)) for a side-panel client.

## Development

```
make test    # uv run pytest — requires a live Postgres via CODECART_DATABASE_URL
make lint    # ruff check + mypy
```

See [`CLAUDE.md`](CLAUDE.md) for architecture notes (pipeline layout, qualified-name
conventions, resolution semantics, known gaps) and [`specs/README.md`](specs/README.md) for
the spec-driven development convention this project follows.
