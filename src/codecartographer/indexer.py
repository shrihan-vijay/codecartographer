import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from codecartographer.db.models import (
    Edge,
    EdgeType,
    FileMetric,
    IndexingRun,
    Node,
    NodeType,
    UnresolvedCall,
)
from codecartographer.db.session import session_scope
from codecartographer.logging import get_logger
from codecartographer.metrics import compute_git_churn, compute_loc
from codecartographer.parsers.base import ParsedCall, ParsedImport, ParsedSymbol, SymbolKind
from codecartographer.parsers.python_parser import PythonParser
from codecartographer.parsers.typescript_parser import TypeScriptParser
from codecartographer.resolution import resolve
from codecartographer.walker import walk_source_files

logger = get_logger(__name__)

_SYMBOL_NODE_TYPE = {
    SymbolKind.FUNCTION: NodeType.FUNCTION,
    SymbolKind.METHOD: NodeType.METHOD,
    SymbolKind.CLASS: NodeType.CLASS,
}


@dataclass
class IndexSummary:
    repo_path: str
    commit_sha: str
    file_count: int
    node_count: int
    edge_count: int
    unresolved_count: int


def _get_commit_sha(repo_path: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _parse_files(
    repo_path: Path, files: list[Path]
) -> tuple[
    list[ParsedSymbol], list[ParsedImport], list[ParsedCall], list[tuple[str, int, int, int]]
]:
    py_parser = PythonParser()
    ts_parser = TypeScriptParser()

    all_symbols: list[ParsedSymbol] = []
    all_imports: list[ParsedImport] = []
    all_calls: list[ParsedCall] = []
    file_metrics_data: list[tuple[str, int, int, int]] = []

    for rel_path in files:
        rel_str = rel_path.as_posix()
        source = (repo_path / rel_path).read_bytes()
        parser = py_parser if rel_str.endswith(".py") else ts_parser
        result = parser.parse(rel_str, source)

        all_symbols.extend(result.symbols)
        all_imports.extend(result.imports)
        all_calls.extend(result.calls)

        loc = compute_loc(repo_path / rel_path)
        churn = compute_git_churn(repo_path, rel_path)
        function_count = sum(
            1 for s in result.symbols if s.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD)
        )
        file_metrics_data.append((rel_str, loc, function_count, churn))

    return all_symbols, all_imports, all_calls, file_metrics_data


def _replace_existing_run(session: Session, repo_path: str, commit_sha: str) -> None:
    existing = session.execute(
        select(IndexingRun).where(
            IndexingRun.repo_path == repo_path, IndexingRun.commit_sha == commit_sha
        )
    ).scalar_one_or_none()
    if existing is not None:
        session.delete(existing)
        session.flush()


def index_repo(repo_path: Path) -> IndexSummary:
    repo_path = repo_path.resolve()
    commit_sha = _get_commit_sha(repo_path)

    files = sorted(walk_source_files(repo_path))
    file_paths = [f.as_posix() for f in files]
    logger.info("walked_repo", repo_path=str(repo_path), file_count=len(files))

    all_symbols, all_imports, all_calls, file_metrics_data = _parse_files(repo_path, files)
    resolution = resolve(all_symbols, all_imports, all_calls, file_paths)
    logger.info(
        "resolved_edges",
        imports=len(resolution.import_edges),
        contains=len(resolution.contains_edges),
        calls=len(resolution.call_edges),
        unresolved_calls=len(resolution.unresolved_calls),
    )

    with session_scope() as session:
        _replace_existing_run(session, str(repo_path), commit_sha)

        run = IndexingRun(repo_path=str(repo_path), commit_sha=commit_sha)
        session.add(run)
        session.flush()

        file_nodes = [
            Node(
                indexing_run_id=run.id,
                node_type=NodeType.FILE,
                name=PurePosixPath(rel_str).name,
                qualified_name=rel_str,
                file_path=rel_str,
                language="python" if rel_str.endswith(".py") else "typescript",
            )
            for rel_str in file_paths
        ]
        symbol_nodes = [
            Node(
                indexing_run_id=run.id,
                node_type=_SYMBOL_NODE_TYPE[s.kind],
                name=s.name,
                qualified_name=s.qualified_name,
                file_path=s.file_path,
                start_line=s.start_line,
                end_line=s.end_line,
                signature=s.signature,
                docstring=s.docstring,
                language=s.language,
            )
            for s in all_symbols
        ]
        session.add_all(file_nodes + symbol_nodes)
        session.flush()

        node_id_by_qn = {n.qualified_name: n.id for n in (*file_nodes, *symbol_nodes)}

        edges: list[Edge] = []
        for ce in resolution.contains_edges:
            src_id = node_id_by_qn.get(ce.parent_key)
            dst_id = node_id_by_qn.get(ce.child_qualified_name)
            if src_id is not None and dst_id is not None:
                edges.append(
                    Edge(
                        indexing_run_id=run.id,
                        edge_type=EdgeType.CONTAINS,
                        src_node_id=src_id,
                        dst_node_id=dst_id,
                    )
                )
        for ie in resolution.import_edges:
            src_id = node_id_by_qn.get(ie.src_file_path)
            dst_id = node_id_by_qn.get(ie.dst_file_path)
            if src_id is not None and dst_id is not None:
                edges.append(
                    Edge(
                        indexing_run_id=run.id,
                        edge_type=EdgeType.IMPORTS,
                        src_node_id=src_id,
                        dst_node_id=dst_id,
                        line=ie.line,
                    )
                )
        for cae in resolution.call_edges:
            src_id = node_id_by_qn.get(cae.caller_qualified_name)
            dst_id = node_id_by_qn.get(cae.callee_qualified_name)
            if src_id is not None and dst_id is not None:
                edges.append(
                    Edge(
                        indexing_run_id=run.id,
                        edge_type=EdgeType.CALLS,
                        src_node_id=src_id,
                        dst_node_id=dst_id,
                        line=cae.line,
                    )
                )
        session.add_all(edges)

        unresolved_rows = []
        for u in resolution.unresolved_calls:
            src_id = node_id_by_qn.get(u.caller_qualified_name)
            if src_id is not None:
                unresolved_rows.append(
                    UnresolvedCall(
                        indexing_run_id=run.id,
                        src_node_id=src_id,
                        called_name=u.called_name,
                        line=u.line,
                        reason=u.reason,
                    )
                )
        session.add_all(unresolved_rows)

        session.add_all(
            FileMetric(
                indexing_run_id=run.id,
                file_path=rel_str,
                loc=loc,
                function_count=function_count,
                git_churn=churn,
            )
            for rel_str, loc, function_count, churn in file_metrics_data
        )

        node_count = len(node_id_by_qn)
        edge_count = len(edges)
        unresolved_count = len(unresolved_rows)
        run.node_count = node_count
        run.edge_count = edge_count
        run.unresolved_count = unresolved_count
        run.completed_at = datetime.now(UTC)

    return IndexSummary(
        repo_path=str(repo_path),
        commit_sha=commit_sha,
        file_count=len(file_paths),
        node_count=node_count,
        edge_count=edge_count,
        unresolved_count=unresolved_count,
    )
