import subprocess
from pathlib import Path

import pytest

from codecartographer.db.models import EdgeType, NodeType
from codecartographer.db.session import session_scope
from codecartographer.indexer import index_repo


def _git(repo_path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo_path, check=True, capture_output=True)


@pytest.fixture()
def fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)

    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "utils.py").write_text(
        "def helper(x):\n"
        "    return x + 1\n"
        "\n"
        "\n"
        "class Widget:\n"
        "    def bump(self):\n"
        "        return helper(1)\n"
    )
    (repo / "pkg" / "main.py").write_text(
        "from .utils import helper\n\n\ndef run():\n    return helper(1)\n"
    )
    (repo / "app.ts").write_text(
        'import { compute } from "./lib";\n'
        "\n"
        "export function main(): number {\n"
        "  return compute(1, 2);\n"
        "}\n"
    )
    (repo / "lib.ts").write_text(
        "export function compute(a: number, b: number): number {\n  return a + b;\n}\n"
    )

    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-q",
        "-m",
        "initial",
    )
    return repo


def test_index_repo_produces_expected_graph(fixture_repo: Path, db_session) -> None:
    summary = index_repo(fixture_repo)

    assert summary.file_count == 5
    assert summary.unresolved_count == 0

    with session_scope() as session:
        from codecartographer.queries import get_latest_run

        run = get_latest_run(session, repo_path=str(fixture_repo.resolve()))
        assert run is not None
        assert run.commit_sha == summary.commit_sha

        nodes = {n.qualified_name: n for n in run.nodes}
        edges = [(e.edge_type, e.src_node_id, e.dst_node_id, e.line) for e in run.edges]

        assert "pkg.main.run" in nodes
        assert "pkg.utils.helper" in nodes
        assert nodes["pkg.utils.Widget.bump"].node_type == NodeType.METHOD

        def has_edge(edge_type: EdgeType, src_qn: str, dst_qn: str) -> bool:
            src_id = nodes[src_qn].id
            dst_id = nodes[dst_qn].id
            return any(e[0] == edge_type and e[1] == src_id and e[2] == dst_id for e in edges)

        # Python cross-file resolution: main.run() -> utils.helper()
        assert has_edge(EdgeType.CALLS, "pkg.main.run", "pkg.utils.helper")
        # Python same-file resolution: Widget.bump() -> helper()
        assert has_edge(EdgeType.CALLS, "pkg.utils.Widget.bump", "pkg.utils.helper")
        # Python IMPORTS: main.py -> utils.py
        assert has_edge(EdgeType.IMPORTS, "pkg/main.py", "pkg/utils.py")
        # TS cross-file resolution: app.ts main() -> lib.ts compute()
        assert has_edge(EdgeType.CALLS, "app#main", "lib#compute")
        # TS IMPORTS: app.ts -> lib.ts
        assert has_edge(EdgeType.IMPORTS, "app.ts", "lib.ts")
        # CONTAINS: file -> top-level symbol
        assert has_edge(EdgeType.CONTAINS, "pkg/utils.py", "pkg.utils.helper")
        # CONTAINS: class -> method
        assert has_edge(EdgeType.CONTAINS, "pkg.utils.Widget", "pkg.utils.Widget.bump")


@pytest.fixture()
def same_line_calls_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def helper(x):\n"
        "    return x + 1\n"
        "\n"
        "\n"
        "def run():\n"
        "    return [helper(1), helper(2)]\n"
    )
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-q",
        "-m",
        "initial",
    )
    return repo


def test_two_calls_to_same_symbol_on_one_line_does_not_violate_edge_uniqueness(
    same_line_calls_repo: Path, db_session
) -> None:
    # `[helper(1), helper(2)]` is two distinct call sites resolving to the same callee
    # on the same source line -- the CALLS edge model is keyed by (src, dst, line), not
    # column, so this collapses to one edge. Indexing must not crash on the resulting
    # duplicate-key insert.
    summary = index_repo(same_line_calls_repo)
    assert summary.unresolved_count == 0

    with session_scope() as session:
        from codecartographer.queries import get_latest_run

        run = get_latest_run(session, repo_path=str(same_line_calls_repo.resolve()))
        assert run is not None

        nodes = {n.qualified_name: n for n in run.nodes}
        calls_edges = [
            e
            for e in run.edges
            if e.edge_type == EdgeType.CALLS
            and e.src_node_id == nodes["app.run"].id
            and e.dst_node_id == nodes["app.helper"].id
        ]
        assert len(calls_edges) == 1


def test_reindexing_same_commit_is_idempotent(fixture_repo: Path, db_session) -> None:
    first = index_repo(fixture_repo)
    second = index_repo(fixture_repo)

    assert first.commit_sha == second.commit_sha
    assert first.node_count == second.node_count
    assert first.edge_count == second.edge_count

    with session_scope() as session:
        from sqlalchemy import func, select

        from codecartographer.db.models import IndexingRun

        run_count = session.execute(
            select(func.count())
            .select_from(IndexingRun)
            .where(IndexingRun.repo_path == str(fixture_repo.resolve()))
        ).scalar_one()
        assert run_count == 1
