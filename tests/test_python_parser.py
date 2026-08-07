from pathlib import Path

from codecartographer.parsers.base import SymbolKind
from codecartographer.parsers.python_parser import PythonParser, module_name_from_path

FIXTURES = Path(__file__).parent / "fixtures" / "python"


def test_module_name_strips_src_layout_prefix() -> None:
    assert module_name_from_path("src/pkg/mod.py") == "pkg.mod"
    assert module_name_from_path("src/pkg/__init__.py") == "pkg"
    assert module_name_from_path("pkg/mod.py") == "pkg.mod"
    assert module_name_from_path("mod.py") == "mod"


def parse_fixture(name: str) -> tuple:
    source = (FIXTURES / name).read_bytes()
    parser = PythonParser()
    return parser.parse(name, source)


def parse_source(source: str, file_path: str = "nested.py") -> tuple:
    return PythonParser().parse(file_path, source.encode())


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


def test_definitions_nested_in_if_else_are_captured() -> None:
    result = parse_source(
        """
def outer():
    if condition():
        def inner_if():
            pass
    else:
        def inner_else():
            pass
"""
    )
    by_qn = {s.qualified_name: s for s in result.symbols}
    assert "nested.outer.inner_if" in by_qn
    assert "nested.outer.inner_else" in by_qn
    assert by_qn["nested.outer.inner_if"].parent_qualified_name == "nested.outer"


def test_definitions_nested_in_for_while_try_with_are_captured() -> None:
    result = parse_source(
        """
def outer():
    for x in items():
        def inner_for():
            pass
    while running():
        def inner_while():
            pass
    try:
        def inner_try():
            pass
    except ValueError:
        def inner_except():
            pass
    finally:
        def inner_finally():
            pass
    with open("f") as fh:
        def inner_with():
            pass
"""
    )
    by_qn = {s.qualified_name: s for s in result.symbols}
    for name in (
        "inner_for",
        "inner_while",
        "inner_try",
        "inner_except",
        "inner_finally",
        "inner_with",
    ):
        assert f"nested.outer.{name}" in by_qn, name


def test_calls_inside_control_flow_nested_definitions_attributed_to_nested_def() -> None:
    result = parse_source(
        """
def outer():
    if condition():
        def inner():
            helper()
"""
    )
    calls_by_caller: dict[str, list[str]] = {}
    for c in result.calls:
        calls_by_caller.setdefault(c.caller_qualified_name, []).append(c.callee_name)

    assert "helper" in calls_by_caller["nested.outer.inner"]
    assert "condition" in calls_by_caller["nested.outer"]
    assert "helper" not in calls_by_caller["nested.outer"]
