"""Tests for the Phase 3 agent tool-calling loop, stubbed against a fake Ollama client
so the suite never needs Ollama running or a model pulled. See specs/phase-3-agentic-qa.md.
"""

from pathlib import Path
from typing import Any

import ollama
import pytest

from codecartographer.agent import AgentAnswer, ToolCallEvent, run_agent, run_agent_stream
from codecartographer.db.models import IndexingRun


def _tool_call(name: str, arguments: dict[str, Any]) -> ollama.Message.ToolCall:
    return ollama.Message.ToolCall(
        function=ollama.Message.ToolCall.Function(name=name, arguments=arguments)
    )


def _assistant_message(
    tool_calls: list[ollama.Message.ToolCall] | None = None, content: str = ""
) -> ollama.Message:
    return ollama.Message(role="assistant", content=content, tool_calls=tool_calls)


class FakeOllamaClient:
    """Returns one queued response per .chat() call, in order."""

    def __init__(self, responses: list[ollama.Message]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat(self, *, model: str, messages: list[Any], tools: list[Any]) -> ollama.ChatResponse:
        self.calls.append({"model": model, "messages": list(messages), "tools": tools})
        message = self._responses.pop(0)
        return ollama.ChatResponse(model=model, message=message)


class _UnusedEmbedder:
    """Stands in for Embedder() in tests that never call search_code -- avoids loading
    the real sentence-transformers model and fails loudly if search_code is invoked
    unexpectedly."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("search_code should not have been called in this test")


@pytest.fixture()
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n")
    return repo


def _fake_run() -> IndexingRun:
    return IndexingRun(id=1, repo_path="unused", commit_sha="deadbeef")


def test_immediate_final_answer_returns_citations(source_repo: Path) -> None:
    final_call = _tool_call(
        "provide_final_answer",
        {
            "answer": "add() sums its two arguments.",
            "citations": [{"file_path": "app.py", "start_line": 1, "end_line": 2}],
        },
    )
    client = FakeOllamaClient([_assistant_message([final_call])])

    result = run_agent(
        session=None,  # type: ignore[arg-type]
        run=_fake_run(),
        repo_path=source_repo,
        question="What does add do?",
        client=client,
        embedder=_UnusedEmbedder(),  # type: ignore[arg-type]
    )

    assert result.answer == "add() sums its two arguments."
    assert len(result.citations) == 1
    assert result.citations[0].file_path == "app.py"
    assert result.citations[0].start_line == 1
    assert len(client.calls) == 1


def test_tool_call_then_final_answer_reads_real_source(source_repo: Path) -> None:
    read_call = _tool_call("read_source", {"file_path": "app.py", "start_line": 1, "end_line": 2})
    final_call = _tool_call(
        "provide_final_answer",
        {
            "answer": "add(a, b) returns a + b.",
            "citations": [{"file_path": "app.py", "start_line": 1, "symbol_name": "add"}],
        },
    )
    client = FakeOllamaClient(
        [_assistant_message([read_call]), _assistant_message([final_call])]
    )

    result = run_agent(
        session=None,  # type: ignore[arg-type]
        run=_fake_run(),
        repo_path=source_repo,
        question="What does add do?",
        client=client,
        embedder=_UnusedEmbedder(),  # type: ignore[arg-type]
    )

    assert result.answer == "add(a, b) returns a + b."
    assert result.citations[0].symbol_name == "add"
    assert len(client.calls) == 2

    # The tool result fed back to the model on the second call must contain the real
    # file contents, not a paraphrase.
    second_call_messages = client.calls[1]["messages"]
    tool_result_message = second_call_messages[-1]
    assert tool_result_message["role"] == "tool"
    assert "return a + b" in tool_result_message["content"]


def test_stream_yields_tool_call_event_before_final_answer(source_repo: Path) -> None:
    read_call = _tool_call("read_source", {"file_path": "app.py", "start_line": 1, "end_line": 2})
    final_call = _tool_call("provide_final_answer", {"answer": "done", "citations": []})
    client = FakeOllamaClient(
        [_assistant_message([read_call]), _assistant_message([final_call])]
    )

    events = list(
        run_agent_stream(
            session=None,  # type: ignore[arg-type]
            run=_fake_run(),
            repo_path=source_repo,
            question="What does add do?",
            client=client,
            embedder=_UnusedEmbedder(),  # type: ignore[arg-type]
        )
    )

    assert len(events) == 2
    assert isinstance(events[0], ToolCallEvent)
    assert events[0].tool_name == "read_source"
    assert events[0].arguments["file_path"] == "app.py"
    assert isinstance(events[1], AgentAnswer)
    assert events[1].answer == "done"


def test_no_tool_call_falls_back_to_message_content(source_repo: Path) -> None:
    client = FakeOllamaClient([_assistant_message(content="I'm not sure.")])

    result = run_agent(
        session=None,  # type: ignore[arg-type]
        run=_fake_run(),
        repo_path=source_repo,
        question="What does add do?",
        client=client,
        embedder=_UnusedEmbedder(),  # type: ignore[arg-type]
    )

    assert result.answer == "I'm not sure."
    assert result.citations == []


def test_max_iterations_cap_stops_an_endless_tool_loop(source_repo: Path) -> None:
    read_call = _tool_call("read_source", {"file_path": "app.py", "start_line": 1, "end_line": 2})
    # The model never calls provide_final_answer -- every response asks for another tool call.
    client = FakeOllamaClient([_assistant_message([read_call]) for _ in range(10)])

    result = run_agent(
        session=None,  # type: ignore[arg-type]
        run=_fake_run(),
        repo_path=source_repo,
        question="What does add do?",
        client=client,
        max_iterations=3,
        embedder=_UnusedEmbedder(),  # type: ignore[arg-type]
    )

    assert len(client.calls) == 3
    assert result.citations == []
    assert "allotted steps" in result.answer


def test_read_source_rejects_path_escaping_repo_root(source_repo: Path) -> None:
    escape_call = _tool_call(
        "read_source", {"file_path": "../../etc/passwd", "start_line": 1, "end_line": 1}
    )
    final_call = _tool_call("provide_final_answer", {"answer": "no such file", "citations": []})
    client = FakeOllamaClient(
        [_assistant_message([escape_call]), _assistant_message([final_call])]
    )

    run_agent(
        session=None,  # type: ignore[arg-type]
        run=_fake_run(),
        repo_path=source_repo,
        question="Read /etc/passwd",
        client=client,
        embedder=_UnusedEmbedder(),  # type: ignore[arg-type]
    )

    tool_result_message = client.calls[1]["messages"][-1]
    assert "escapes the repository root" in tool_result_message["content"]
