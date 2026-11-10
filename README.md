# CodeCartographer

CodeCartographer indexes large repos into (a) a symbol/call graph via static analysis and
(b) AST-aware semantic chunks in pgvector, then serves cited Q&A over the codebase via an
agentic retrieval layer running on a **local, offline LLM (Ollama)**: no API key, no
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
  Resolution is deliberately best-effort with graceful failure: an unresolved call is
  recorded with a reason, not silently guessed.
- **Semantic search**: AST-aware chunking + local sentence-transformer embeddings
  (`all-MiniLM-L6-v2`) stored in pgvector, so search works on meaning, not just symbol names.
- **Agentic Q&A**: an agent (Ollama, `llama3.1` by default) with tools for graph traversal,
  semantic search, and source reading answers natural-language questions about the codebase
  and cites the exact symbols/lines it used.
- **Chrome extension**: a side-panel client (`extension/`) for the same `/ask` API, so you
  can ask questions about an indexed repo without leaving the browser.

Built in five phases; see [`specs/`](specs/) for the design doc behind each one:

| Phase | What it adds |
| --- | --- |
| 1: Indexing foundation | tree-sitter parsing to symbol/call graph in Postgres |
| 2: Embeddings | AST-aware chunking + pgvector semantic search |
| 3: Agentic Q&A | tool-calling agent over Ollama, cited answers, FastAPI backend |
| 4: Chrome extension | side-panel client on top of the Phase 3 API |
| 5: PyPI packaging | bundled migrations + `codecart db-init`, groundwork for installing without a git clone (not currently published) |

## Setup

Requires [uv](https://docs.astral.sh/uv/), Docker (or a local Postgres 16 + pgvector), and
[Ollama](https://ollama.com) for the Q&A layer.

```
make setup              # uv sync
make db-up               # docker compose up postgres+pgvector, then alembic upgrade head
ollama pull llama3.1     # one-time, ~4.7GB, only needed for `codecart ask`
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

## Known limitations and next steps

Handoff notes for whoever picks this up next. Each item links back to the phase spec that
deliberately scoped it out.

- **Local model reliability.** `llama3.1` (8B) sometimes emits malformed tool calls or answers
  without calling `provide_final_answer`, which means no citations. The agent recovers tool
  calls written as plain JSON text, but it can't repair JSON that is actually malformed.
  Narrow questions ("Which functions call X?") are more reliable than broad ones. The
  highest-value next step is evaluating a stronger tool-calling model (e.g. `qwen2.5`) via
  `--model`, with a small fixed question set to measure the success rate.
- **Unresolved calls.** Calls into the standard library and third-party packages are recorded
  in `unresolved_calls` (reason `no_matching_symbol`) rather than guessed, so on a typical repo
  they outnumber resolved calls. This is intentional ([Phase 1](specs/phase-1-indexing-foundation.md)),
  but classifying them (stdlib vs. third-party vs. truly unknown) would make the numbers easier
  to read.
- **Full re-index only.** Re-indexing replaces the whole run; there's no incremental,
  changed-files-only indexing ([Phase 2](specs/phase-2-embeddings.md)).
- **Single-turn Q&A, no auth.** No conversation memory, no multi-user support
  ([Phase 3](specs/phase-3-agentic-qa.md)).
- **Extension needs a typed repo path.** It doesn't auto-detect the repo from a GitHub/GitLab
  page or annotate code inline ([Phase 4](specs/phase-4-chrome-extension.md)).
- **Not on PyPI yet.** Packaging is ready, but nothing has been published
  ([Phase 5](specs/phase-5-pypi-packaging.md)).

## Development

```
make test    # uv run pytest, requires a live Postgres via CODECART_DATABASE_URL
make lint    # ruff check + mypy
```

Note: the test suite truncates every table in the database it points at, including any repo
you've indexed. Re-run `codecart index` after testing before a demo.

See [`CLAUDE.md`](CLAUDE.md) for architecture notes (pipeline layout, qualified-name
conventions, resolution semantics, known gaps) and [`specs/README.md`](specs/README.md) for
the spec-driven development convention this project follows.
