"""FastAPI backend for Phase 3: POST /ask runs the agent loop and returns the final
answer; POST /ask/stream streams tool-call progress events as newline-delimited JSON
followed by the final answer, so the frontend isn't staring at a blank page for the
10-60+ seconds a multi-tool-call question can take against a local model. GET / serves
the static frontend. Run with `uvicorn codecartographer.api:app`. See
specs/phase-3-agentic-qa.md.
"""

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from codecartographer.agent import AgentAnswer, ToolCallEvent, run_agent, run_agent_stream
from codecartographer.db.session import session_scope
from codecartographer.embedder import Embedder
from codecartographer.queries import get_latest_run

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Loading the embedding model is slow -- construct it once at startup and reuse it
    # across every request instead of once per /ask call.
    app.state.embedder = Embedder()
    yield


app = FastAPI(title="CodeCartographer", lifespan=_lifespan)


class AskRequest(BaseModel):
    repo_path: str
    question: str


class CitationOut(BaseModel):
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    symbol_name: str | None = None


class AskResponse(BaseModel):
    answer: str
    citations: list[CitationOut]


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.post("/ask", response_model=AskResponse)
def ask(request: AskRequest, http_request: Request) -> AskResponse:
    repo_path = Path(request.repo_path).resolve()
    with session_scope() as session:
        run = get_latest_run(session, repo_path=str(repo_path))
        if run is None:
            raise HTTPException(
                status_code=404,
                detail=f"No completed indexing run found for {repo_path}. "
                "Run `codecart index <repo_path>` first.",
            )
        result = run_agent(
            session, run, repo_path, request.question, embedder=http_request.app.state.embedder
        )
        return AskResponse(
            answer=result.answer,
            citations=[
                CitationOut(
                    file_path=c.file_path,
                    start_line=c.start_line,
                    end_line=c.end_line,
                    symbol_name=c.symbol_name,
                )
                for c in result.citations
            ],
        )


@app.post("/ask/stream")
def ask_stream(request: AskRequest, http_request: Request) -> StreamingResponse:
    repo_path = Path(request.repo_path).resolve()
    with session_scope() as session:
        if get_latest_run(session, repo_path=str(repo_path)) is None:
            raise HTTPException(
                status_code=404,
                detail=f"No completed indexing run found for {repo_path}. "
                "Run `codecart index <repo_path>` first.",
            )

    embedder = http_request.app.state.embedder
    return StreamingResponse(
        _stream_ndjson(repo_path, request.question, embedder), media_type="application/x-ndjson"
    )


def _stream_ndjson(repo_path: Path, question: str, embedder: Embedder) -> Iterator[str]:
    # A fresh session for the lifetime of the streamed response -- the one used for the
    # existence check above is already closed by the time this generator runs.
    with session_scope() as session:
        run = get_latest_run(session, repo_path=str(repo_path))
        if run is None:
            yield json.dumps({"type": "error", "detail": "indexing run disappeared"}) + "\n"
            return
        try:
            for event in run_agent_stream(session, run, repo_path, question, embedder=embedder):
                yield json.dumps(_event_to_json(event)) + "\n"
        except Exception as exc:  # surface as a stream event, not a silently dropped connection
            yield json.dumps({"type": "error", "detail": str(exc)}) + "\n"


def _event_to_json(event: ToolCallEvent | AgentAnswer) -> dict[str, Any]:
    if isinstance(event, ToolCallEvent):
        return {"type": "tool_call", "tool_name": event.tool_name, "arguments": event.arguments}
    return {
        "type": "final",
        "answer": event.answer,
        "citations": [
            {
                "file_path": c.file_path,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "symbol_name": c.symbol_name,
            }
            for c in event.citations
        ],
    }
