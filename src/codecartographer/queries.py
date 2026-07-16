from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from codecartographer.db.models import Chunk, Edge, FileMetric, IndexingRun, Node

_TRAVERSE_SQL = {
    "callers": text(
        """
        WITH RECURSIVE roots AS (
            SELECT id FROM nodes WHERE indexing_run_id = :run_id AND name = :symbol_name
        ),
        traverse AS (
            SELECT e.src_node_id AS node_id, 1 AS depth
            FROM edges e
            WHERE e.indexing_run_id = :run_id AND e.edge_type = 'CALLS'
              AND e.dst_node_id IN (SELECT id FROM roots)
            UNION
            SELECT e.src_node_id, t.depth + 1
            FROM edges e
            JOIN traverse t ON e.dst_node_id = t.node_id
            WHERE e.indexing_run_id = :run_id AND e.edge_type = 'CALLS' AND t.depth < :max_depth
        )
        SELECT n.qualified_name, n.file_path, n.start_line, MIN(t.depth) AS depth
        FROM traverse t
        JOIN nodes n ON n.id = t.node_id
        GROUP BY n.qualified_name, n.file_path, n.start_line
        ORDER BY depth, n.qualified_name
        """
    ),
    "callees": text(
        """
        WITH RECURSIVE roots AS (
            SELECT id FROM nodes WHERE indexing_run_id = :run_id AND name = :symbol_name
        ),
        traverse AS (
            SELECT e.dst_node_id AS node_id, 1 AS depth
            FROM edges e
            WHERE e.indexing_run_id = :run_id AND e.edge_type = 'CALLS'
              AND e.src_node_id IN (SELECT id FROM roots)
            UNION
            SELECT e.dst_node_id, t.depth + 1
            FROM edges e
            JOIN traverse t ON e.src_node_id = t.node_id
            WHERE e.indexing_run_id = :run_id AND e.edge_type = 'CALLS' AND t.depth < :max_depth
        )
        SELECT n.qualified_name, n.file_path, n.start_line, MIN(t.depth) AS depth
        FROM traverse t
        JOIN nodes n ON n.id = t.node_id
        GROUP BY n.qualified_name, n.file_path, n.start_line
        ORDER BY depth, n.qualified_name
        """
    ),
}


@dataclass
class GraphRow:
    qualified_name: str
    file_path: str
    start_line: int | None
    depth: int


@dataclass
class HotspotRow:
    file_path: str
    loc: int
    function_count: int
    git_churn: int
    score: int


@dataclass
class SearchResult:
    qualified_name: str
    file_path: str
    start_line: int | None
    node_type: str
    distance: float
    content: str


@dataclass
class StatsResult:
    run: IndexingRun
    node_counts: dict[str, int]
    edge_counts: dict[str, int]
    unresolved_count: int


def get_latest_run(session: Session, repo_path: str | None = None) -> IndexingRun | None:
    query = select(IndexingRun).where(IndexingRun.completed_at.is_not(None))
    if repo_path is not None:
        query = query.where(IndexingRun.repo_path == repo_path)
    query = query.order_by(IndexingRun.completed_at.desc()).limit(1)
    return session.execute(query).scalar_one_or_none()


def find_callers(session: Session, run_id: int, symbol_name: str, max_depth: int) -> list[GraphRow]:
    return _traverse(session, "callers", run_id, symbol_name, max_depth)


def find_callees(session: Session, run_id: int, symbol_name: str, max_depth: int) -> list[GraphRow]:
    return _traverse(session, "callees", run_id, symbol_name, max_depth)


def _traverse(
    session: Session, direction: str, run_id: int, symbol_name: str, max_depth: int
) -> list[GraphRow]:
    rows = session.execute(
        _TRAVERSE_SQL[direction],
        {"run_id": run_id, "symbol_name": symbol_name, "max_depth": max_depth},
    ).all()
    return [
        GraphRow(
            qualified_name=r.qualified_name,
            file_path=r.file_path,
            start_line=r.start_line,
            depth=r.depth,
        )
        for r in rows
    ]


def find_hotspots(session: Session, run_id: int, limit: int = 20) -> list[HotspotRow]:
    score = FileMetric.git_churn * FileMetric.function_count
    query = (
        select(FileMetric)
        .where(FileMetric.indexing_run_id == run_id)
        .order_by(score.desc(), FileMetric.file_path)
        .limit(limit)
    )
    rows = session.execute(query).scalars().all()
    return [
        HotspotRow(
            file_path=r.file_path,
            loc=r.loc,
            function_count=r.function_count,
            git_churn=r.git_churn,
            score=r.git_churn * r.function_count,
        )
        for r in rows
    ]


def search_chunks(
    session: Session, run_id: int, query_embedding: list[float], limit: int = 10
) -> list[SearchResult]:
    distance = Chunk.embedding.cosine_distance(query_embedding).label("distance")
    query = (
        select(Node, Chunk.content, distance)
        .join(Node, Node.id == Chunk.node_id)
        .where(Chunk.indexing_run_id == run_id)
        .order_by(distance)
        .limit(limit)
    )
    rows = session.execute(query).all()
    return [
        SearchResult(
            qualified_name=node.qualified_name,
            file_path=node.file_path,
            start_line=node.start_line,
            node_type=node.node_type.value,
            distance=dist,
            content=content,
        )
        for node, content, dist in rows
    ]


def get_stats(session: Session, run: IndexingRun) -> StatsResult:
    node_rows = session.execute(
        select(Node.node_type, func.count())
        .where(Node.indexing_run_id == run.id)
        .group_by(Node.node_type)
    ).all()
    edge_rows = session.execute(
        select(Edge.edge_type, func.count())
        .where(Edge.indexing_run_id == run.id)
        .group_by(Edge.edge_type)
    ).all()
    return StatsResult(
        run=run,
        node_counts={nt.value: c for nt, c in node_rows},
        edge_counts={et.value: c for et, c in edge_rows},
        unresolved_count=run.unresolved_count or 0,
    )
