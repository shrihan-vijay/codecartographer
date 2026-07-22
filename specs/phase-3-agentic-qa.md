# Phase 3: agentic retrieval layer for cited Q&A

Status: Implemented

## Goal

Let a user ask a natural-language question about an indexed repo and get an answer backed by
real citations (file + line) into the actual codebase — using both maps built in Phases 1 and 2
(structural graph + semantic search) via an agent that decides what to look up, rather than a
fixed retrieval pipeline. This is the first phase that makes real LLM calls and the first phase
with a UI a user opens in a browser.

## Scope

- In scope: a locally-hosted LLM (via Ollama) with tools wrapping Phase 1 (graph queries) and
  Phase 2 (semantic search) plus a source-reading tool; a small FastAPI backend (`POST /ask`); a
  minimal static HTML/JS frontend (no build step) served by the same backend; a `codecart ask`
  CLI command for terminal use without the web server.
- Out of scope: multi-turn conversation memory/persistence, authentication/multi-user support,
  production deployment/hosting, IDE integrations, streaming the answer token-by-token to the UI
  (v1 waits for the full answer).

## Design

**Runtime: Ollama, not the Claude API.** Per the user's explicit choice, Phase 3 runs entirely
against a locally-hosted open-weight model via [Ollama](https://ollama.com), not a paid hosted
API. This makes Phase 3 **genuinely free with no per-call cost and no API key** — the earlier
draft's "confirm every call" constraint doesn't apply here since there's no metering, only the
one-time setup cost of installing Ollama and downloading a model (a multi-GB download, done once,
needs explicit confirmation before running given its size — see Prerequisite below).

**Model**: `llama3.1:8b` by default (~4.7GB) — confirmed by Ollama's own documentation to support
tool calling, which is the one hard requirement for this design to work at all. Configurable via
a `--model` flag. Coding-focused local models (`qwen2.5-coder` and others) may also support tool
calling and could well produce better answers about code specifically, but their tool-calling
support wasn't reliably confirmed while researching this spec — worth trying as a follow-up
experiment once the `llama3.1` path works end-to-end, not as the initial default.

**Known tradeoff, stated plainly**: a local 7-8B model is meaningfully less reliable at
multi-step agentic tool use than a frontier hosted model. Expect some of: skipping a tool call it
should have made and answering from general knowledge instead, malformed tool-call arguments, or
looping oddly. This is a real capability gap inherent to the free/local choice, not a bug to
chase down — the acceptance criteria below are written as "best effort, verify by trying" rather
than hard guarantees for this reason.

**Tools**, each a thin wrapper around code that already exists — unchanged from the original
design, only the runtime calling them changes:

| Tool | Wraps | Purpose |
|---|---|---|
| `search_code(query, limit)` | `queries.search_chunks` (Phase 2) | Find symbols by meaning |
| `get_callers(symbol_name, depth)` | `queries.find_callers` (Phase 1) | "What calls this?" |
| `get_callees(symbol_name, depth)` | `queries.find_callees` (Phase 1) | "What does this call?" |
| `read_source(file_path, start_line, end_line)` | Direct file read | Quote real code, not a paraphrase |
| `provide_final_answer(answer, citations)` | — (terminal tool) | Forces the model to conclude with a structured `{answer: str, citations: [{file_path, start_line, end_line, symbol_name}]}` shape rather than free text with citations the frontend would have to guess at parsing |

**Loop implementation**: a manual loop against Ollama's `chat()` API (the `ollama` Python
package), since Ollama has no equivalent of Anthropic's SDK "Tool Runner" helper — send messages
+ `tools`, check the response for `tool_calls`, execute the matching Python function, append the
result, repeat until `provide_final_answer` is called or a max-iteration cap is hit (guards
against a small model looping indefinitely instead of concluding).

**System prompt** instructs the model to answer only from tool results (never from general
knowledge about how code "usually" works), and to call `provide_final_answer` with an empty or
partial `citations` list plus an explicit "I couldn't find this in the codebase" in `answer` when
it can't ground a claim — mirrors Phase 1/2's "don't guess" philosophy. Given the tradeoff above,
expect this instruction to be followed less reliably than it would be by a frontier model.

**Backend**: `POST /ask` — body `{repo_path: str, question: str}`, response
`{answer: str, citations: [...]}`. Looks up the latest indexing run for `repo_path` (same
`get_latest_run` used by the CLI today), runs the tool loop, returns the final answer. No new
database tables — this phase is read-only against what Phases 1-2 already persisted.

**Frontend**: one static HTML file (inline CSS/JS, no Node/npm/build step) with a text input and
an answer area; citations render as `file:line` links. FastAPI serves it directly at `/`, so the
whole thing is still one Python process (`uvicorn codecartographer.api:app`).

**CLI**: `codecart ask "<question>" <repo_path>` — same backend logic, prints the answer and
citations to the terminal. Useful for testing without the web server running.

**New dependencies**: `ollama` (Python package, talks to the local Ollama server over HTTP —
default `http://localhost:11434`), `fastapi`, `uvicorn`. No `anthropic` SDK, no API key handling.

**Prerequisite (one-time, needs explicit confirmation before running)**: install Ollama
(`brew install ollama`), then pull the model (`ollama pull llama3.1`) — a multi-GB download.
After that, Ollama runs locally as a background service and every subsequent call is free.

## Acceptance criteria

Criteria marked "best effort" acknowledge the local-model reliability tradeoff above — the goal
is "works well enough to demonstrate the concept," not flawless tool selection every time.

- [x] Ollama installed and `llama3.1` pulled and confirmed working (`ollama run llama3.1 "hi"`
      returns a response) before any application code is tested against it.
- [x] `codecart ask "<question>" <repo_path>` returns an answer with citations in the terminal.
- [x] `uvicorn codecartographer.api:app` serves a working page at `/` — typing a question and
      submitting it returns an answer with clickable citations, no page reload needed.
- [x] Manually verified against a real indexed repo: citations point at file:line that actually
      supports the claim made (spot-checked, not just "citations exist").
- [~] Best effort: the model uses more than one tool type across a few different questions in
      manual testing (e.g. a "what does X call" question uses `get_callees`; a "where is X
      handled" question uses `search_code`) — confirmed `read_source` firing correctly via native
      tool_calls; didn't observe `get_callers`/`get_callees`/`search_code` in the small manual
      sample, worth trying more questions but not blocking per "best effort".
- [~] Best effort: asked a question with no real answer in the repo, the model says so more often
      than it fabricates a citation — not yet spot-checked with an adversarial question; worth
      trying before relying on this in a demo.
- [x] At least one test exercises the tool-calling/citation-parsing logic against a
      stubbed/mocked Ollama client (no real model call, deterministic) — so the automated test
      suite doesn't require Ollama to be running or a model to be pulled to pass in CI.
      (`tests/test_agent.py`, 5 tests.)
- [x] `ruff check`, `mypy src`, full pytest suite clean, same bar as prior phases.

**Observed in manual testing**: `llama3.1:8b` reliably uses `read_source` via real Ollama
tool_calls (confirmed by the returned answer quoting exact file content), but does not reliably
call `provide_final_answer` as a structured tool call — it often ends its turn with free text
that includes an ad-hoc "Citations:" section instead. The loop's no-tool-call fallback handles
this correctly (returns the text as `answer`, empty `citations` list) rather than crashing or
hanging, but it means the structured `citations` field in the API/CLI response is less reliable
than the answer text itself for this model. This is the tradeoff the spec called out in advance,
not a bug in the loop — confirmed by feeding the same fake ollama client scripted tool-call
sequences in `tests/test_agent.py`, where structured citations parse correctly when the model
does call `provide_final_answer` properly.
