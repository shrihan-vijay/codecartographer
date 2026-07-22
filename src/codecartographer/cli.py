from pathlib import Path

import typer

from codecartographer.agent import DEFAULT_MODEL, AgentAnswer, run_agent_stream
from codecartographer.db.session import session_scope
from codecartographer.embedder import Embedder
from codecartographer.indexer import index_repo
from codecartographer.logging import configure_logging
from codecartographer.queries import (
    find_callees,
    find_callers,
    find_hotspots,
    get_latest_run,
    get_stats,
    search_chunks,
)

app = typer.Typer(name="codecart", help="Code intelligence CLI for CodeCartographer.")


@app.callback()
def main() -> None:
    configure_logging()


@app.command()
def index(repo_path: Path = typer.Argument(..., help="Path to the repository to index.")) -> None:
    """Full index of a repo: parse, resolve, and persist the symbol/call graph."""
    summary = index_repo(repo_path)
    typer.echo(f"Indexed {summary.repo_path} @ {summary.commit_sha[:12]}")
    typer.echo(f"  files:     {summary.file_count}")
    typer.echo(f"  nodes:     {summary.node_count}")
    typer.echo(f"  edges:     {summary.edge_count}")
    typer.echo(f"  chunks:    {summary.chunk_count}")
    typer.echo(f"  unresolved calls: {summary.unresolved_count}")


@app.command()
def callers(
    symbol_name: str = typer.Argument(..., help="Name of the symbol to find callers of."),
    depth: int = typer.Option(1, "--depth", help="Maximum traversal depth."),
) -> None:
    """Who (transitively, up to --depth) calls this symbol."""
    with session_scope() as session:
        run = get_latest_run(session)
        if run is None:
            typer.echo(
                "No completed indexing run found. Run `codecart index <repo_path>` first.", err=True
            )
            raise typer.Exit(code=1)
        rows = find_callers(session, run.id, symbol_name, depth)
        if not rows:
            typer.echo(f"No callers found for '{symbol_name}'.")
            return
        for row in rows:
            typer.echo(
                f"[depth {row.depth}] {row.qualified_name}  ({row.file_path}:{row.start_line})"
            )


@app.command()
def callees(
    symbol_name: str = typer.Argument(..., help="Name of the symbol to find callees of."),
    depth: int = typer.Option(1, "--depth", help="Maximum traversal depth."),
) -> None:
    """What (transitively, up to --depth) this symbol calls."""
    with session_scope() as session:
        run = get_latest_run(session)
        if run is None:
            typer.echo(
                "No completed indexing run found. Run `codecart index <repo_path>` first.", err=True
            )
            raise typer.Exit(code=1)
        rows = find_callees(session, run.id, symbol_name, depth)
        if not rows:
            typer.echo(f"No callees found for '{symbol_name}'.")
            return
        for row in rows:
            typer.echo(
                f"[depth {row.depth}] {row.qualified_name}  ({row.file_path}:{row.start_line})"
            )


@app.command()
def hotspots(
    repo_path: Path = typer.Argument(..., help="Path to the repository."),
    limit: int = typer.Option(20, "--limit", help="Number of files to show."),
) -> None:
    """Top files by churn x complexity proxy (function count)."""
    with session_scope() as session:
        run = get_latest_run(session, repo_path=str(repo_path.resolve()))
        if run is None:
            typer.echo(
                "No completed indexing run found for this repo. Run `codecart index` first.",
                err=True,
            )
            raise typer.Exit(code=1)
        rows = find_hotspots(session, run.id, limit=limit)
        if not rows:
            typer.echo("No file metrics found.")
            return
        typer.echo(f"{'score':>6}  {'churn':>6}  {'funcs':>6}  {'loc':>6}  file")
        for row in rows:
            stats_prefix = (
                f"{row.score:>6}  {row.git_churn:>6}  {row.function_count:>6}  {row.loc:>6}"
            )
            typer.echo(f"{stats_prefix}  {row.file_path}")


@app.command()
def search(
    query: str = typer.Argument(..., help="Natural-language search query."),
    repo_path: Path = typer.Argument(..., help="Path to the repository."),
    limit: int = typer.Option(10, "--limit", help="Number of results to show."),
) -> None:
    """Semantic search over indexed code chunks (functions/methods/classes)."""
    with session_scope() as session:
        run = get_latest_run(session, repo_path=str(repo_path.resolve()))
        if run is None:
            typer.echo(
                "No completed indexing run found for this repo. Run `codecart index` first.",
                err=True,
            )
            raise typer.Exit(code=1)
        query_vector = Embedder().embed([query])[0]
        results = search_chunks(session, run.id, query_vector, limit=limit)
        if not results:
            typer.echo("No chunks found for this run.")
            return
        for r in results:
            typer.echo(f"{r.distance:.4f}  {r.qualified_name}  ({r.file_path}:{r.start_line})")


@app.command()
def ask(
    question: str = typer.Argument(..., help="Natural-language question about the codebase."),
    repo_path: Path = typer.Argument(..., help="Path to the repository."),
    model: str = typer.Option(DEFAULT_MODEL, "--model", help="Ollama model to use."),
) -> None:
    """Ask a question about the indexed repo, answered by an agent with graph/search tools."""
    with session_scope() as session:
        resolved = repo_path.resolve()
        run = get_latest_run(session, repo_path=str(resolved))
        if run is None:
            typer.echo(
                "No completed indexing run found for this repo. Run `codecart index` first.",
                err=True,
            )
            raise typer.Exit(code=1)
        result: AgentAnswer | None = None
        for event in run_agent_stream(session, run, resolved, question, model=model):
            if isinstance(event, AgentAnswer):
                result = event
            else:
                typer.echo(f"... calling {event.tool_name}({dict(event.arguments)})", err=True)
        assert result is not None
        typer.echo(result.answer)
        if result.citations:
            typer.echo("\nCitations:")
            for c in result.citations:
                lines = f":{c.start_line}" if c.start_line else ""
                if c.end_line and c.end_line != c.start_line:
                    lines += f"-{c.end_line}"
                symbol = f"  ({c.symbol_name})" if c.symbol_name else ""
                typer.echo(f"  {c.file_path}{lines}{symbol}")


@app.command()
def stats() -> None:
    """Node/edge counts by type for the most recent indexing run."""
    with session_scope() as session:
        run = get_latest_run(session)
        if run is None:
            typer.echo(
                "No completed indexing run found. Run `codecart index <repo_path>` first.", err=True
            )
            raise typer.Exit(code=1)
        result = get_stats(session, run)
        typer.echo(f"Run: {result.run.repo_path} @ {result.run.commit_sha[:12]}")
        typer.echo("Nodes:")
        for node_type, count in sorted(result.node_counts.items()):
            typer.echo(f"  {node_type:<10} {count}")
        typer.echo("Edges:")
        for edge_type, count in sorted(result.edge_counts.items()):
            typer.echo(f"  {edge_type:<10} {count}")
        typer.echo(f"Unresolved calls: {result.unresolved_count}")


if __name__ == "__main__":
    app()
