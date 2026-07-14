from pathlib import PurePosixPath

import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser

from codecartographer.parsers.base import (
    ParsedCall,
    ParsedImport,
    ParsedSymbol,
    ParseResult,
    SymbolKind,
)

_TS_LANGUAGE = Language(tsts.language_typescript())
_TSX_LANGUAGE = Language(tsts.language_tsx())

_FUNCTION_VALUE_TYPES = {"arrow_function", "function_expression"}

# Node types that terminate a call-collection walk because they're handled as
# separate symbols elsewhere. Mirrors the Python parser's approach; definitions
# nested inside if/for/try blocks are out of scope for Phase 1 (see python_parser).
_OPAQUE_TYPES = {"function_declaration", "class_declaration", "method_definition"}


def _text(source: bytes, node: Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def module_path_from_file(file_path: str) -> str:
    p = PurePosixPath(file_path)
    return str(p.with_suffix(""))


class TypeScriptParser:
    language = "typescript"

    def __init__(self) -> None:
        self._parser = Parser(_TS_LANGUAGE)
        self._tsx_parser = Parser(_TSX_LANGUAGE)

    def parse(self, file_path: str, source: bytes) -> ParseResult:
        parser = self._tsx_parser if file_path.endswith(".tsx") else self._parser
        tree = parser.parse(source)
        module_path = module_path_from_file(file_path)
        result = ParseResult()

        self._walk_scope(
            container_node=tree.root_node,
            file_path=file_path,
            module_path=module_path,
            name_stack=[],
            parent_qualified_name=None,
            caller_qualified_name=file_path,
            source=source,
            result=result,
        )
        return result

    # -- dispatch -------------------------------------------------------

    def _unwrap_export(self, node: Node) -> tuple[Node, bool]:
        """For `export ...` nodes, return the wrapped declaration (if any) so it's
        handled identically to a non-exported one. The bool flags a bare re-export
        (`export {x} from 'y'` / `export * from 'y'`) which has no declaration.
        """
        if node.type != "export_statement":
            return node, False
        declaration = node.child_by_field_name("declaration")
        if declaration is not None:
            return declaration, False
        has_from = any(c.type == "from" for c in node.children)
        return node, has_from

    def _walk_scope(
        self,
        container_node: Node,
        file_path: str,
        module_path: str,
        name_stack: list[str],
        parent_qualified_name: str | None,
        caller_qualified_name: str,
        source: bytes,
        result: ParseResult,
    ) -> None:
        call_nodes: list[Node] = []

        for child in container_node.children:
            if child.type == "decorator":
                self._collect_calls(child, call_nodes)
                continue

            if child.type == "import_statement":
                self._handle_import(child, file_path, source, result.imports)
                continue

            effective, is_bare_reexport = self._unwrap_export(child)
            if is_bare_reexport:
                self._handle_reexport(effective, file_path, source, result.imports)
                continue

            if effective.type == "function_declaration":
                self._emit_named(
                    effective,
                    child,
                    SymbolKind.FUNCTION,
                    file_path,
                    module_path,
                    name_stack,
                    parent_qualified_name,
                    source,
                    result,
                )
            elif effective.type == "class_declaration":
                for dec in (c for c in effective.children if c.type == "decorator"):
                    self._collect_calls(dec, call_nodes)
                self._emit_named(
                    effective,
                    child,
                    SymbolKind.CLASS,
                    file_path,
                    module_path,
                    name_stack,
                    parent_qualified_name,
                    source,
                    result,
                )
            elif effective.type == "method_definition":
                self._emit_named(
                    effective,
                    child,
                    SymbolKind.METHOD,
                    file_path,
                    module_path,
                    name_stack,
                    parent_qualified_name,
                    source,
                    result,
                )
            elif effective.type in ("lexical_declaration", "variable_declaration"):
                for declarator in (
                    c for c in effective.children if c.type == "variable_declarator"
                ):
                    value = declarator.child_by_field_name("value")
                    if value is not None and value.type in _FUNCTION_VALUE_TYPES:
                        self._emit_arrow(
                            declarator,
                            value,
                            child,
                            SymbolKind.FUNCTION,
                            file_path,
                            module_path,
                            name_stack,
                            parent_qualified_name,
                            source,
                            result,
                        )
                    elif value is not None:
                        self._collect_calls(value, call_nodes)
            elif effective.type == "public_field_definition":
                value = effective.child_by_field_name("value")
                if value is not None and value.type in _FUNCTION_VALUE_TYPES:
                    self._emit_arrow(
                        effective,
                        value,
                        child,
                        SymbolKind.METHOD,
                        file_path,
                        module_path,
                        name_stack,
                        parent_qualified_name,
                        source,
                        result,
                    )
                elif value is not None:
                    self._collect_calls(value, call_nodes)
            else:
                self._collect_calls(child, call_nodes)

        for call_node in call_nodes:
            func_field = call_node.child_by_field_name("function")
            if func_field is None:
                continue
            result.calls.append(
                ParsedCall(
                    file_path=file_path,
                    caller_qualified_name=caller_qualified_name,
                    callee_name=_text(source, func_field),
                    line=call_node.start_point[0] + 1,
                )
            )

    def _collect_calls(self, node: Node, out: list[Node]) -> None:
        if node.type in _OPAQUE_TYPES:
            return
        if node.type == "call_expression":
            out.append(node)
        for child in node.children:
            self._collect_calls(child, out)

    # -- symbol emission --------------------------------------------------

    def _emit_named(
        self,
        def_node: Node,
        outer_span_node: Node,
        kind: SymbolKind,
        file_path: str,
        module_path: str,
        name_stack: list[str],
        parent_qualified_name: str | None,
        source: bytes,
        result: ParseResult,
    ) -> None:
        name_node = def_node.child_by_field_name("name")
        name = _text(source, name_node) if name_node is not None else "default"
        self._emit_symbol(
            name,
            def_node,
            def_node,
            outer_span_node,
            kind,
            file_path,
            module_path,
            name_stack,
            parent_qualified_name,
            source,
            result,
        )

    def _emit_arrow(
        self,
        declarator_node: Node,
        arrow_node: Node,
        outer_span_node: Node,
        kind: SymbolKind,
        file_path: str,
        module_path: str,
        name_stack: list[str],
        parent_qualified_name: str | None,
        source: bytes,
        result: ParseResult,
    ) -> None:
        name_node = declarator_node.child_by_field_name("name")
        if name_node is None:
            return
        name = _text(source, name_node)
        self._emit_symbol(
            name,
            arrow_node,
            declarator_node,
            outer_span_node,
            kind,
            file_path,
            module_path,
            name_stack,
            parent_qualified_name,
            source,
            result,
        )

    def _emit_symbol(
        self,
        name: str,
        def_node: Node,
        sig_start_node: Node,
        outer_span_node: Node,
        kind: SymbolKind,
        file_path: str,
        module_path: str,
        name_stack: list[str],
        parent_qualified_name: str | None,
        source: bytes,
        result: ParseResult,
    ) -> None:
        new_stack = [*name_stack, name]
        qualified_name = f"{module_path}#{'.'.join(new_stack)}"
        body = def_node.child_by_field_name("body")

        result.symbols.append(
            ParsedSymbol(
                kind=kind,
                name=name,
                qualified_name=qualified_name,
                parent_qualified_name=parent_qualified_name,
                file_path=file_path,
                start_line=outer_span_node.start_point[0] + 1,
                end_line=def_node.end_point[0] + 1,
                signature=self._signature(
                    source, sig_start_node, body if body is not None else def_node
                ),
                docstring=self._docstring(source, outer_span_node),
                language=self.language,
            )
        )

        if body is not None and body.type in ("statement_block", "class_body"):
            self._walk_scope(
                container_node=body,
                file_path=file_path,
                module_path=module_path,
                name_stack=new_stack,
                parent_qualified_name=qualified_name,
                caller_qualified_name=qualified_name,
                source=source,
                result=result,
            )

    def _signature(self, source: bytes, start_node: Node, body_node: Node) -> str:
        raw = (
            source[start_node.start_byte : body_node.start_byte]
            .decode("utf-8", errors="ignore")
            .rstrip()
        )
        for suffix in ("=>", ":"):
            if raw.endswith(suffix):
                raw = raw[: -len(suffix)].rstrip()
                break
        return " ".join(raw.split())

    def _docstring(self, source: bytes, span_node: Node) -> str | None:
        prev = span_node.prev_sibling
        if prev is None or prev.type != "comment":
            return None
        text = _text(source, prev)
        if not text.startswith("/**"):
            return None
        inner = text.removeprefix("/**").removesuffix("*/")
        lines = [line.strip().removeprefix("*").strip() for line in inner.splitlines()]
        return "\n".join(line for line in lines if line).strip()

    # -- imports ------------------------------------------------------------

    def _handle_import(
        self, node: Node, file_path: str, source: bytes, out: list[ParsedImport]
    ) -> None:
        string_node = next((c for c in node.children if c.type == "string"), None)
        if string_node is None:
            return
        module = self._string_contents(source, string_node)
        out.append(
            ParsedImport(
                file_path=file_path,
                module=module,
                line=node.start_point[0] + 1,
                is_relative=module.startswith("."),
            )
        )

    def _handle_reexport(
        self, node: Node, file_path: str, source: bytes, out: list[ParsedImport]
    ) -> None:
        string_node = next((c for c in node.children if c.type == "string"), None)
        if string_node is None:
            return
        module = self._string_contents(source, string_node)
        out.append(
            ParsedImport(
                file_path=file_path,
                module=module,
                line=node.start_point[0] + 1,
                is_relative=module.startswith("."),
            )
        )

    def _string_contents(self, source: bytes, string_node: Node) -> str:
        fragment = next((c for c in string_node.children if c.type == "string_fragment"), None)
        return (
            _text(source, fragment)
            if fragment is not None
            else _text(source, string_node).strip("'\"")
        )
