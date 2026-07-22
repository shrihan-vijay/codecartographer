"""Agentic Q&A loop: a local Ollama model with tools wrapping Phase 1 (graph queries) and
Phase 2 (semantic search), plus a source-reading tool, concluding via a terminal
`provide_final_answer` tool call. See specs/phase-3-agentic-qa.md.
"""

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ollama
from sqlalchemy.orm import Session

from codecartographer.db.models import IndexingRun
from codecartographer.embedder import Embedder
from codecartographer.queries import find_callees, find_callers, search_chunks

DEFAULT_MODEL = "llama3.1"
DEFAULT_MAX_ITERATIONS = 8

SYSTEM_PROMPT = """You are a code Q&A assistant with tools to inspect a real, indexed codebase.

Answer only from what your tools return -- never from general knowledge about how code
"usually" works or what a library "probably" does. Use search_code to find symbols by
meaning, get_callers/get_callees to trace the call graph, and read_source to quote exact
code before citing it.

When you are ready to conclude, call provide_final_answer exactly once. If you cannot
ground a claim in a tool result, say so explicitly in `answer` and leave `citations` empty
or partial -- do not fabricate a citation."""

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Find symbols (functions/methods/classes) by natural-language meaning, "
                "using semantic search over the indexed codebase."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language description of what to find.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_callers",
            "description": "Find symbols that (transitively) call the given symbol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol_name": {"type": "string", "description": "Name of the symbol."},
                    "depth": {
                        "type": "integer",
                        "description": "Maximum traversal depth (default 1).",
                    },
                },
                "required": ["symbol_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_callees",
            "description": "Find symbols that the given symbol (transitively) calls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol_name": {"type": "string", "description": "Name of the symbol."},
                    "depth": {
                        "type": "integer",
                        "description": "Maximum traversal depth (default 1).",
                    },
                },
                "required": ["symbol_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_source",
            "description": "Read exact source lines from a file in the repo, to quote real code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Repo-relative file path, e.g. src/pkg/module.py.",
                    },
                    "start_line": {"type": "integer", "description": "1-indexed start line."},
                    "end_line": {
                        "type": "integer",
                        "description": "1-indexed end line, inclusive.",
                    },
                },
                "required": ["file_path", "start_line", "end_line"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "provide_final_answer",
            "description": (
                "Conclude and give the final answer to the user's question. Call this exactly "
                "once, when ready to answer -- including when the answer is that the codebase "
                "doesn't contain what was asked."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string", "description": "The final answer text."},
                    "citations": {
                        "type": "array",
                        "description": "Citations backing the answer; empty list if none apply.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "file_path": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "end_line": {"type": "integer"},
                                "symbol_name": {"type": "string"},
                            },
                            "required": ["file_path"],
                        },
                    },
                },
                "required": ["answer", "citations"],
            },
        },
    },
]


@dataclass
class Citation:
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    symbol_name: str | None = None


@dataclass
class AgentAnswer:
    answer: str
    citations: list[Citation] = field(default_factory=list)


@dataclass
class ToolCallEvent:
    """Emitted by run_agent_stream right before a tool executes, so a caller (the API,
    the CLI) can show progress during what can otherwise be a many-second-long request.
    """

    tool_name: str
    arguments: Mapping[str, Any]


AgentEvent = ToolCallEvent | AgentAnswer


def run_agent(
    session: Session,
    run: IndexingRun,
    repo_path: Path,
    question: str,
    *,
    model: str = DEFAULT_MODEL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    client: ollama.Client | None = None,
    embedder: Embedder | None = None,
) -> AgentAnswer:
    """Run the tool-calling loop and return only the final answer. See run_agent_stream
    for progress events (which tool is being called) as they happen.
    """
    final: AgentAnswer | None = None
    for event in run_agent_stream(
        session,
        run,
        repo_path,
        question,
        model=model,
        max_iterations=max_iterations,
        client=client,
        embedder=embedder,
    ):
        if isinstance(event, AgentAnswer):
            final = event
    assert final is not None  # run_agent_stream always ends with exactly one AgentAnswer
    return final


def run_agent_stream(
    session: Session,
    run: IndexingRun,
    repo_path: Path,
    question: str,
    *,
    model: str = DEFAULT_MODEL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    client: ollama.Client | None = None,
    embedder: Embedder | None = None,
) -> Iterator[AgentEvent]:
    """Run the tool-calling loop until provide_final_answer is called or max_iterations
    is hit, yielding a ToolCallEvent before each tool executes and exactly one
    AgentAnswer as the final, terminal event. `client` and `embedder` are injectable so
    tests can stub the model call without a running Ollama server or downloading the
    embedding model.
    """
    client = client or ollama.Client()
    # Constructed once and reused across iterations -- loading the embedding model is
    # slow, and a single question can call search_code more than once.
    embedder = embedder or Embedder()
    messages: list[Any] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    for _ in range(max_iterations):
        response = client.chat(model=model, messages=messages, tools=TOOLS)
        message = response.message
        messages.append(message)

        tool_calls = message.tool_calls or []
        if not tool_calls:
            # Best effort: a model that never calls provide_final_answer still gets a
            # usable (uncited) response rather than an empty one -- see spec's stated
            # local-model reliability tradeoff.
            yield AgentAnswer(answer=message.content or "", citations=[])
            return

        for call in tool_calls:
            name = call.function.name
            arguments = call.function.arguments
            if name == "provide_final_answer":
                yield _parse_final_answer(arguments)
                return
            yield ToolCallEvent(tool_name=name, arguments=arguments)
            result = _execute_tool(session, run, repo_path, embedder, name, arguments)
            messages.append({"role": "tool", "tool_name": name, "content": result})

    yield AgentAnswer(
        answer="I wasn't able to reach a grounded answer within the allotted steps.",
        citations=[],
    )


def _parse_final_answer(arguments: Mapping[str, Any]) -> AgentAnswer:
    answer = str(arguments.get("answer", ""))
    raw_citations = arguments.get("citations") or []
    citations = [
        Citation(
            file_path=str(c.get("file_path", "")),
            start_line=c.get("start_line"),
            end_line=c.get("end_line"),
            symbol_name=c.get("symbol_name"),
        )
        for c in raw_citations
        if isinstance(c, dict) and c.get("file_path")
    ]
    return AgentAnswer(answer=answer, citations=citations)


def _execute_tool(
    session: Session,
    run: IndexingRun,
    repo_path: Path,
    embedder: Embedder,
    name: str,
    arguments: Mapping[str, Any],
) -> str:
    try:
        if name == "search_code":
            return _tool_search_code(session, run, embedder, arguments)
        if name == "get_callers":
            return _tool_get_callers(session, run, arguments)
        if name == "get_callees":
            return _tool_get_callees(session, run, arguments)
        if name == "read_source":
            return _tool_read_source(repo_path, arguments)
        return json.dumps({"error": f"unknown tool '{name}'"})
    except Exception as exc:  # tool failures go back to the model, not the caller
        return json.dumps({"error": str(exc)})


def _tool_search_code(
    session: Session,
    run: IndexingRun,
    embedder: Embedder,
    arguments: Mapping[str, Any],
) -> str:
    query = str(arguments.get("query", ""))
    limit = int(arguments.get("limit") or 5)
    query_vector = embedder.embed([query])[0]
    results = search_chunks(session, run.id, query_vector, limit=limit)
    return json.dumps(
        [
            {
                "qualified_name": r.qualified_name,
                "file_path": r.file_path,
                "start_line": r.start_line,
                "node_type": r.node_type,
                "content": r.content,
            }
            for r in results
        ]
    )


def _tool_get_callers(session: Session, run: IndexingRun, arguments: Mapping[str, Any]) -> str:
    symbol_name = str(arguments.get("symbol_name", ""))
    depth = int(arguments.get("depth") or 1)
    rows = find_callers(session, run.id, symbol_name, depth)
    return _graph_rows_to_json(rows)


def _tool_get_callees(session: Session, run: IndexingRun, arguments: Mapping[str, Any]) -> str:
    symbol_name = str(arguments.get("symbol_name", ""))
    depth = int(arguments.get("depth") or 1)
    rows = find_callees(session, run.id, symbol_name, depth)
    return _graph_rows_to_json(rows)


def _graph_rows_to_json(rows: list[Any]) -> str:
    return json.dumps(
        [
            {
                "qualified_name": r.qualified_name,
                "file_path": r.file_path,
                "start_line": r.start_line,
                "depth": r.depth,
            }
            for r in rows
        ]
    )


def _tool_read_source(repo_path: Path, arguments: Mapping[str, Any]) -> str:
    file_path = str(arguments.get("file_path", ""))
    start_line = int(arguments.get("start_line") or 1)
    end_line = int(arguments.get("end_line") or start_line)

    repo_root = repo_path.resolve()
    resolved = (repo_root / file_path).resolve()
    if not resolved.is_relative_to(repo_root):
        return json.dumps({"error": "file_path escapes the repository root"})
    if not resolved.is_file():
        return json.dumps({"error": f"no such file: {file_path}"})

    lines = resolved.read_text(encoding="utf-8", errors="ignore").splitlines()
    start = max(start_line - 1, 0)
    end = min(end_line, len(lines))
    if start >= end:
        return json.dumps({"error": "start_line/end_line out of range"})
    content = "\n".join(lines[start:end])
    return json.dumps({"file_path": file_path, "start_line": start_line, "content": content})
