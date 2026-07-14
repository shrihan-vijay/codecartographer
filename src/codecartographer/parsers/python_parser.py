from pathlib import PurePosixPath

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from codecartographer.parsers.base import (
    ParsedCall,
    ParsedImport,
    ParsedSymbol,
    ParseResult,
    SymbolKind,
)

_LANGUAGE = Language(tspython.language())

# Statements whose direct children may themselves be definitions/statements we walk
# into (module root, and function/class bodies). Definitions nested inside if/for/
# try/with blocks are a known gap: kept out of scope for Phase 1 to avoid a much
# more elaborate control-flow-aware walk for a rare pattern.
_DEFINITION_TYPES = {"function_definition", "class_definition"}


def _text(source: bytes, node: Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def module_name_from_path(file_path: str) -> str:
    parts = list(PurePosixPath(file_path).parts)
    # src-layout convention (PEP 517/518): "src/" is an on-disk container, not part
    # of the importable package name -- `src/pkg/mod.py` imports as `pkg.mod`, not
    # `src.pkg.mod`. Without this, absolute imports in src-layout repos (like this
    # one) never match a real module and same-package call resolution silently
    # degrades to near-zero.
    if len(parts) > 1 and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts:
        parts[-1] = PurePosixPath(parts[-1]).stem
    return ".".join(parts)


class PythonParser:
    language = "python"

    def __init__(self) -> None:
        self._parser = Parser(_LANGUAGE)

    def parse(self, file_path: str, source: bytes) -> ParseResult:
        tree = self._parser.parse(source)
        module_path = module_name_from_path(file_path)
        result = ParseResult()

        self._walk_imports(tree.root_node, file_path, source, result.imports)
        self._walk_scope(
            container_node=tree.root_node,
            file_path=file_path,
            module_path=module_path,
            name_stack=[],
            parent_qualified_name=None,
            parent_kind=None,
            caller_qualified_name=file_path,
            source=source,
            result=result,
        )
        return result

    # -- symbols + calls --------------------------------------------------

    def _walk_scope(
        self,
        container_node: Node,
        file_path: str,
        module_path: str,
        name_stack: list[str],
        parent_qualified_name: str | None,
        parent_kind: SymbolKind | None,
        caller_qualified_name: str,
        source: bytes,
        result: ParseResult,
    ) -> None:
        call_nodes: list[Node] = []

        for child in container_node.children:
            if child.type == "decorated_definition":
                inner = next((c for c in child.children if c.type in _DEFINITION_TYPES), None)
                for dec in (c for c in child.children if c.type == "decorator"):
                    self._collect_calls(dec, call_nodes)
                if inner is not None:
                    self._handle_definition(
                        def_node=inner,
                        span_node=child,
                        file_path=file_path,
                        module_path=module_path,
                        name_stack=name_stack,
                        parent_qualified_name=parent_qualified_name,
                        parent_kind=parent_kind,
                        source=source,
                        result=result,
                    )
            elif child.type in _DEFINITION_TYPES:
                self._handle_definition(
                    def_node=child,
                    span_node=child,
                    file_path=file_path,
                    module_path=module_path,
                    name_stack=name_stack,
                    parent_qualified_name=parent_qualified_name,
                    parent_kind=parent_kind,
                    source=source,
                    result=result,
                )
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
        if node.type in _DEFINITION_TYPES or node.type == "decorated_definition":
            return
        if node.type == "call":
            out.append(node)
        for child in node.children:
            self._collect_calls(child, out)

    def _handle_definition(
        self,
        def_node: Node,
        span_node: Node,
        file_path: str,
        module_path: str,
        name_stack: list[str],
        parent_qualified_name: str | None,
        parent_kind: SymbolKind | None,
        source: bytes,
        result: ParseResult,
    ) -> None:
        name_node = def_node.child_by_field_name("name")
        if name_node is None:
            return
        name = _text(source, name_node)
        new_stack = [*name_stack, name]
        qualified_name = (
            f"{module_path}.{'.'.join(new_stack)}" if module_path else ".".join(new_stack)
        )

        if def_node.type == "class_definition":
            kind = SymbolKind.CLASS
        else:
            kind = SymbolKind.METHOD if parent_kind == SymbolKind.CLASS else SymbolKind.FUNCTION

        result.symbols.append(
            ParsedSymbol(
                kind=kind,
                name=name,
                qualified_name=qualified_name,
                parent_qualified_name=parent_qualified_name,
                file_path=file_path,
                start_line=span_node.start_point[0] + 1,
                end_line=def_node.end_point[0] + 1,
                signature=self._signature(source, def_node),
                docstring=self._docstring(source, def_node),
                language=self.language,
            )
        )

        body = def_node.child_by_field_name("body")
        if body is not None:
            self._walk_scope(
                container_node=body,
                file_path=file_path,
                module_path=module_path,
                name_stack=new_stack,
                parent_qualified_name=qualified_name,
                parent_kind=kind,
                caller_qualified_name=qualified_name,
                source=source,
                result=result,
            )

    def _signature(self, source: bytes, def_node: Node) -> str:
        body = def_node.child_by_field_name("body")
        end = body.start_byte if body is not None else def_node.end_byte
        raw = source[def_node.start_byte : end].decode("utf-8", errors="ignore").rstrip()
        if raw.endswith(":"):
            raw = raw[:-1]
        return " ".join(raw.split())

    def _docstring(self, source: bytes, def_node: Node) -> str | None:
        body = def_node.child_by_field_name("body")
        if body is None or not body.children:
            return None
        first = body.children[0]
        if first.type != "expression_statement" or not first.children:
            return None
        string_node = first.children[0]
        if string_node.type != "string":
            return None
        parts = [_text(source, c) for c in string_node.children if c.type == "string_content"]
        if not parts:
            return None
        return "".join(parts).strip()

    # -- imports ------------------------------------------------------------

    def _walk_imports(
        self, node: Node, file_path: str, source: bytes, out: list[ParsedImport]
    ) -> None:
        if node.type == "import_statement":
            self._handle_import(node, file_path, source, out)
        elif node.type == "import_from_statement":
            self._handle_import_from(node, file_path, source, out)
        for child in node.children:
            self._walk_imports(child, file_path, source, out)

    def _handle_import(
        self, node: Node, file_path: str, source: bytes, out: list[ParsedImport]
    ) -> None:
        line = node.start_point[0] + 1
        for child in node.children:
            if child.type == "dotted_name":
                out.append(
                    ParsedImport(
                        file_path=file_path,
                        module=_text(source, child),
                        line=line,
                        is_relative=False,
                    )
                )
            elif child.type == "aliased_import":
                dotted = next((c for c in child.children if c.type == "dotted_name"), None)
                if dotted is not None:
                    out.append(
                        ParsedImport(
                            file_path=file_path,
                            module=_text(source, dotted),
                            line=line,
                            is_relative=False,
                        )
                    )

    def _handle_import_from(
        self, node: Node, file_path: str, source: bytes, out: list[ParsedImport]
    ) -> None:
        module_node = next(
            (c for c in node.children if c.type in ("dotted_name", "relative_import")), None
        )
        if module_node is None:
            return
        module_text = _text(source, module_node)
        is_relative = module_node.type == "relative_import"
        line = node.start_point[0] + 1
        out.append(
            ParsedImport(
                file_path=file_path, module=module_text, line=line, is_relative=is_relative
            )
        )

        if is_relative:
            # `from . import sibling` / `from .pkg import sibling` -- `sibling` may
            # itself be a submodule rather than a name defined in the package's
            # __init__.py. Emit it as a second candidate; resolution decides which
            # (if either) matches a real file in the repo.
            base = module_text if module_text.endswith(".") else module_text + "."
            for name in self._import_from_names(node, source):
                out.append(
                    ParsedImport(
                        file_path=file_path, module=base + name, line=line, is_relative=True
                    )
                )

    def _import_from_names(self, node: Node, source: bytes) -> list[str]:
        names = []
        past_import_kw = False
        for child in node.children:
            if child.type == "import":
                past_import_kw = True
                continue
            if not past_import_kw:
                continue
            if child.type in ("dotted_name", "identifier"):
                names.append(_text(source, child))
            elif child.type == "aliased_import":
                original = next(
                    (c for c in child.children if c.type in ("dotted_name", "identifier")), None
                )
                if original is not None:
                    names.append(_text(source, original))
        return [n for n in names if n and n != "*"]
