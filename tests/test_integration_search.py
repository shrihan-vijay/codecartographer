import subprocess
from pathlib import Path

import pytest

from codecartographer.db.session import session_scope
from codecartographer.embedder import Embedder
from codecartographer.indexer import index_repo
from codecartographer.queries import get_latest_run, search_chunks


def _git(repo_path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo_path, check=True, capture_output=True)


@pytest.fixture()
def semantic_fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def connect_to_database(host, port):\n"
        '    """Opens a connection to the Postgres database and returns a client."""\n'
        '    return f"connected to {host}:{port}"\n'
        "\n\n"
        "def send_email(to_address, subject, body):\n"
        '    """Sends an email notification to the given address."""\n'
        '    return f"sent to {to_address}"\n'
        "\n\n"
        "def calculate_total_price(items, tax_rate):\n"
        '    """Sums item prices and applies sales tax."""\n'
        "    return sum(items) * (1 + tax_rate)\n"
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


def test_index_populates_chunks(semantic_fixture_repo: Path, db_session) -> None:
    summary = index_repo(semantic_fixture_repo)

    assert summary.chunk_count == 3


def test_search_ranks_semantically_relevant_symbol_first(
    semantic_fixture_repo: Path, db_session
) -> None:
    embedder = Embedder()
    index_repo(semantic_fixture_repo, embedder=embedder)

    with session_scope() as session:
        run = get_latest_run(session, repo_path=str(semantic_fixture_repo.resolve()))
        assert run is not None

        query_vector = embedder.embed(["database connection setup"])[0]
        results = search_chunks(session, run.id, query_vector, limit=5)

        assert results
        assert results[0].qualified_name == "app.connect_to_database"


def test_reindexing_replaces_chunks_not_appends(semantic_fixture_repo: Path, db_session) -> None:
    embedder = Embedder()
    first = index_repo(semantic_fixture_repo, embedder=embedder)
    second = index_repo(semantic_fixture_repo, embedder=embedder)

    assert first.chunk_count == second.chunk_count == 3
