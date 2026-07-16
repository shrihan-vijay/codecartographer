from pathlib import Path

from codecartographer.embedder import Embedder, build_chunk_text
from codecartographer.parsers.python_parser import PythonParser

FIXTURES = Path(__file__).parent / "fixtures" / "python"


def test_chunk_text_includes_signature_and_body() -> None:
    source = (FIXTURES / "sibling.py").read_bytes()
    result = PythonParser().parse("sibling.py", source)
    symbol = next(s for s in result.symbols if s.name == "helper")

    text = build_chunk_text(FIXTURES, symbol)

    assert "def helper(x)" in text
    assert "return x * 2" in text


def test_chunk_text_includes_docstring_when_present(tmp_path: Path) -> None:
    source = b'def foo():\n    """Does something useful."""\n    return 1\n'
    (tmp_path / "mod.py").write_bytes(source)
    result = PythonParser().parse("mod.py", source)
    symbol = result.symbols[0]

    text = build_chunk_text(tmp_path, symbol)

    assert "Does something useful." in text
    assert "def foo()" in text
    assert "return 1" in text


def test_chunk_text_handles_missing_file_gracefully(tmp_path: Path) -> None:
    source = b"def foo():\n    return 1\n"
    result = PythonParser().parse("nonexistent.py", source)
    symbol = result.symbols[0]

    # File doesn't actually exist under tmp_path -- must not raise, body just omitted.
    text = build_chunk_text(tmp_path, symbol)

    assert "def foo()" in text


def test_embedder_produces_expected_dimension() -> None:
    vectors = Embedder().embed(["hello world", "foo bar"])

    assert len(vectors) == 2
    assert len(vectors[0]) == 384
    assert len(vectors[1]) == 384


def test_embedder_handles_empty_input() -> None:
    assert Embedder().embed([]) == []
