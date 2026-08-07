from pathlib import Path

from codecartographer.parsers.base import SymbolKind
from codecartographer.parsers.typescript_parser import TypeScriptParser

FIXTURES = Path(__file__).parent / "fixtures" / "typescript"


def parse_fixture(name: str) -> tuple:
    source = (FIXTURES / name).read_bytes()
    parser = TypeScriptParser()
    return parser.parse(name, source)


def parse_source(source: str, file_path: str = "nested.ts") -> tuple:
    return TypeScriptParser().parse(file_path, source.encode())


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


def test_definitions_nested_in_if_else_are_captured() -> None:
    result = parse_source(
        """
function outer() {
  if (cond()) {
    function innerIf() {}
  } else {
    function innerElse() {}
  }
}
"""
    )
    by_qn = {s.qualified_name: s for s in result.symbols}
    assert "nested#outer.innerIf" in by_qn
    assert "nested#outer.innerElse" in by_qn
    assert by_qn["nested#outer.innerIf"].parent_qualified_name == "nested#outer"


def test_definitions_nested_in_for_try_switch_are_captured() -> None:
    result = parse_source(
        """
function outer() {
  for (const x of items()) {
    class InnerFor {
      method() {}
    }
  }
  try {
    function innerTry() {}
  } catch (e) {
    function innerCatch() {}
  } finally {
    function innerFinally() {}
  }
  switch (val()) {
    case 1:
      function innerCase() {}
      break;
    default:
      function innerDefault() {}
  }
}
"""
    )
    by_qn = {s.qualified_name: s for s in result.symbols}
    assert "nested#outer.InnerFor" in by_qn
    assert "nested#outer.InnerFor.method" in by_qn
    for name in ("innerTry", "innerCatch", "innerFinally", "innerCase", "innerDefault"):
        assert f"nested#outer.{name}" in by_qn, name


def test_calls_inside_control_flow_nested_definitions_attributed_to_nested_def() -> None:
    result = parse_source(
        """
function outer() {
  if (cond()) {
    function inner() { helper(); }
  }
}
"""
    )
    calls_by_caller: dict[str, list[str]] = {}
    for c in result.calls:
        calls_by_caller.setdefault(c.caller_qualified_name, []).append(c.callee_name)

    assert "helper" in calls_by_caller["nested#outer.inner"]
    assert "cond" in calls_by_caller["nested#outer"]
    assert "helper" not in calls_by_caller["nested#outer"]
