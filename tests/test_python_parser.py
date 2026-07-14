from pathlib import Path

from codecartographer.parsers.base import SymbolKind
from codecartographer.parsers.python_parser import PythonParser

FIXTURES = Path(__file__).parent / "fixtures" / "python"


def parse_fixture(name: str) -> tuple:
    source = (FIXTURES / name).read_bytes()
    parser = PythonParser()
    return parser.parse(name, source)


def test_module_docstring_and_imports() -> None:
    result = parse_fixture("tricky.py")
    modules = {i.module for i in result.imports}
    assert "os" in modules
    assert ".sibling" in modules


def test_nested_function_is_captured_with_correct_parent() -> None:
    result = parse_fixture("tricky.py")
    by_qn = {s.qualified_name: s for s in result.symbols}

    assert "tricky.top_level" in by_qn
    assert "tricky.top_level.nested" in by_qn

    nested = by_qn["tricky.top_level.nested"]
    assert nested.kind == SymbolKind.FUNCTION
    assert nested.parent_qualified_name == "tricky.top_level"


def test_top_level_docstring_extracted() -> None:
    result = parse_fixture("tricky.py")
    by_qn = {s.qualified_name: s for s in result.symbols}
    assert by_qn["tricky.top_level"].docstring == "Top level docstring."


def test_class_and_methods_with_decorator() -> None:
    result = parse_fixture("tricky.py")
    by_qn = {s.qualified_name: s for s in result.symbols}

    assert by_qn["tricky.Derived"].kind == SymbolKind.CLASS
    assert by_qn["tricky.Derived"].docstring == "A derived class."

    static_method = by_qn["tricky.Derived.static_method"]
    assert static_method.kind == SymbolKind.METHOD
    assert static_method.parent_qualified_name == "tricky.Derived"
    # decorator line included in the symbol span, but not in the signature text
    assert "@staticmethod" not in static_method.signature
    assert "def static_method" in static_method.signature

    instance_method = by_qn["tricky.Derived.instance_method"]
    assert instance_method.kind == SymbolKind.METHOD


def test_calls_attributed_to_nearest_enclosing_scope() -> None:
    result = parse_fixture("tricky.py")
    calls_by_caller: dict[str, list[str]] = {}
    for c in result.calls:
        calls_by_caller.setdefault(c.caller_qualified_name, []).append(c.callee_name)

    assert "helper" in calls_by_caller["tricky.top_level.nested"]
    assert "nested" in calls_by_caller["tricky.top_level"]
    assert "top_level" in calls_by_caller["tricky.Derived.static_method"]
    assert "self.static_method" in calls_by_caller["tricky.Derived.instance_method"]


def test_signature_excludes_body() -> None:
    result = parse_fixture("sibling.py")
    by_qn = {s.qualified_name: s for s in result.symbols}
    assert by_qn["sibling.helper"].signature == "def helper(x)"
