from pathlib import Path

from codecartographer.parsers.base import SymbolKind
from codecartographer.parsers.typescript_parser import TypeScriptParser

FIXTURES = Path(__file__).parent / "fixtures" / "typescript"


def parse_fixture(name: str) -> tuple:
    source = (FIXTURES / name).read_bytes()
    parser = TypeScriptParser()
    return parser.parse(name, source)


def test_arrow_function_captured_as_function() -> None:
    result = parse_fixture("tricky.ts")
    by_qn = {s.qualified_name: s for s in result.symbols}

    assert "tricky#add" in by_qn
    add = by_qn["tricky#add"]
    assert add.kind == SymbolKind.FUNCTION
    assert "a: number, b: number" in add.signature


def test_arrow_function_docstring_from_jsdoc() -> None:
    result = parse_fixture("tricky.ts")
    by_qn = {s.qualified_name: s for s in result.symbols}
    assert by_qn["tricky#add"].docstring == "Adds two numbers."


def test_reexports_captured_as_imports() -> None:
    result = parse_fixture("tricky.ts")
    modules = {i.module for i in result.imports}
    assert "./helper" in modules
    assert "./reexport" in modules


def test_class_methods_and_decorator() -> None:
    result = parse_fixture("tricky.ts")
    by_qn = {s.qualified_name: s for s in result.symbols}

    assert by_qn["tricky#Widget"].kind == SymbolKind.CLASS

    method = by_qn["tricky#Widget.method"]
    assert method.kind == SymbolKind.METHOD
    assert method.parent_qualified_name == "tricky#Widget"

    compute = by_qn["tricky#Widget.compute"]
    assert compute.kind == SymbolKind.METHOD


def test_decorator_call_attributed_to_class_scope() -> None:
    result = parse_fixture("tricky.ts")
    calls_by_caller: dict[str, list[str]] = {}
    for c in result.calls:
        calls_by_caller.setdefault(c.caller_qualified_name, []).append(c.callee_name)

    assert "Deco" in calls_by_caller["tricky#Widget"]
    assert "this.compute" in calls_by_caller["tricky#Widget.method"]
    assert "add" in calls_by_caller["tricky#Widget.compute"]
