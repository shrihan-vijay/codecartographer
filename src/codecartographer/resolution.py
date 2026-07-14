import posixpath
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from codecartographer.parsers.base import ParsedCall, ParsedImport, ParsedSymbol, SymbolKind
from codecartographer.parsers.python_parser import module_name_from_path


@dataclass
class ResolvedImportEdge:
    src_file_path: str
    dst_file_path: str
    line: int


@dataclass
class ResolvedContainsEdge:
    parent_key: str
    """FILE qualified_name (its file_path) or a symbol's qualified_name."""
    child_qualified_name: str


@dataclass
class ResolvedCallEdge:
    caller_qualified_name: str
    callee_qualified_name: str
    line: int


@dataclass
class UnresolvedCallRecord:
    caller_qualified_name: str
    called_name: str
    line: int
    reason: str


@dataclass
class ResolutionResult:
    import_edges: list[ResolvedImportEdge]
    contains_edges: list[ResolvedContainsEdge]
    call_edges: list[ResolvedCallEdge]
    unresolved_calls: list[UnresolvedCallRecord]


def resolve(
    symbols: list[ParsedSymbol],
    imports: list[ParsedImport],
    calls: list[ParsedCall],
    file_paths: list[str],
) -> ResolutionResult:
    symbol_by_qn = {s.qualified_name: s for s in symbols}
    symbols_by_file: dict[str, list[ParsedSymbol]] = defaultdict(list)
    for s in symbols:
        symbols_by_file[s.file_path].append(s)

    contains_edges = [
        ResolvedContainsEdge(
            parent_key=s.parent_qualified_name
            if s.parent_qualified_name is not None
            else s.file_path,
            child_qualified_name=s.qualified_name,
        )
        for s in symbols
    ]

    py_files = [f for f in file_paths if f.endswith(".py")]
    ts_files = [f for f in file_paths if f.endswith((".ts", ".tsx"))]
    import_edges = _resolve_python_imports(imports, py_files) + _resolve_ts_imports(
        imports, ts_files
    )

    imported_files_by_src: dict[str, set[str]] = defaultdict(set)
    for ie in import_edges:
        imported_files_by_src[ie.src_file_path].add(ie.dst_file_path)

    call_edges = []
    unresolved_calls = []
    for call in calls:
        resolved_qn, reason = _resolve_call(
            call, symbol_by_qn, symbols_by_file, imported_files_by_src
        )
        if resolved_qn is not None:
            call_edges.append(ResolvedCallEdge(call.caller_qualified_name, resolved_qn, call.line))
        else:
            unresolved_calls.append(
                UnresolvedCallRecord(
                    call.caller_qualified_name,
                    call.callee_name,
                    call.line,
                    reason or "no_matching_symbol",
                )
            )

    return ResolutionResult(import_edges, contains_edges, call_edges, unresolved_calls)


# -- import resolution ----------------------------------------------------


def _resolve_python_imports(
    imports: list[ParsedImport], py_files: list[str]
) -> list[ResolvedImportEdge]:
    module_to_file = {module_name_from_path(f): f for f in py_files}
    edges = []
    for imp in imports:
        if not imp.file_path.endswith(".py"):
            continue
        if not imp.is_relative:
            target = module_to_file.get(imp.module)
            if target and target != imp.file_path:
                edges.append(ResolvedImportEdge(imp.file_path, target, imp.line))
            continue

        level = 0
        while level < len(imp.module) and imp.module[level] == ".":
            level += 1
        rest = imp.module[level:]
        rest_parts = rest.split(".") if rest else []

        importer_module = module_name_from_path(imp.file_path)
        importer_parts = importer_module.split(".") if importer_module else []
        if imp.file_path.endswith("__init__.py"):
            current_package_parts = importer_parts
        else:
            current_package_parts = importer_parts[:-1]

        if level - 1 > len(current_package_parts):
            continue  # goes above the repo root; unresolvable
        base_parts = current_package_parts[: len(current_package_parts) - (level - 1)]
        target_module = ".".join(p for p in (*base_parts, *rest_parts) if p)
        target_file = module_to_file.get(target_module)
        if target_file and target_file != imp.file_path:
            edges.append(ResolvedImportEdge(imp.file_path, target_file, imp.line))
    return edges


def _resolve_ts_imports(
    imports: list[ParsedImport], ts_files: list[str]
) -> list[ResolvedImportEdge]:
    ts_file_set = set(ts_files)
    edges = []
    for imp in imports:
        if not imp.file_path.endswith((".ts", ".tsx")):
            continue
        if not imp.is_relative:
            continue  # bare specifiers are external packages, not resolvable to a repo file
        importer_dir = str(PurePosixPath(imp.file_path).parent)
        base = posixpath.normpath(posixpath.join(importer_dir, imp.module))
        candidates = [base, f"{base}.ts", f"{base}.tsx", f"{base}/index.ts", f"{base}/index.tsx"]
        for candidate in candidates:
            normalized = posixpath.normpath(candidate)
            if normalized in ts_file_set and normalized != imp.file_path:
                edges.append(ResolvedImportEdge(imp.file_path, normalized, imp.line))
                break
    return edges


# -- call resolution --------------------------------------------------------


def _enclosing_class(qualified_name: str, symbol_by_qn: dict[str, ParsedSymbol]) -> str | None:
    symbol = symbol_by_qn.get(qualified_name)
    if symbol is None:
        return None
    if symbol.kind == SymbolKind.CLASS:
        return symbol.qualified_name
    parent = symbol.parent_qualified_name
    while parent is not None:
        parent_symbol = symbol_by_qn.get(parent)
        if parent_symbol is None:
            return None
        if parent_symbol.kind == SymbolKind.CLASS:
            return parent_symbol.qualified_name
        parent = parent_symbol.parent_qualified_name
    return None


def _resolve_call(
    call: ParsedCall,
    symbol_by_qn: dict[str, ParsedSymbol],
    symbols_by_file: dict[str, list[ParsedSymbol]],
    imported_files_by_src: dict[str, set[str]],
) -> tuple[str | None, str | None]:
    parts = call.callee_name.split(".")
    caller_symbol = symbol_by_qn.get(call.caller_qualified_name)
    caller_file = (
        caller_symbol.file_path if caller_symbol is not None else call.caller_qualified_name
    )

    if len(parts) >= 2 and parts[0] in ("self", "this"):
        class_qn = _enclosing_class(call.caller_qualified_name, symbol_by_qn)
        if class_qn is None:
            return None, "no_matching_symbol"
        candidate = f"{class_qn}.{parts[-1]}"
        target = symbol_by_qn.get(candidate)
        if target is not None and target.kind == SymbolKind.METHOD:
            return candidate, None
        return None, "no_matching_symbol"

    candidate_name = parts[-1]

    intra_file = [
        s
        for s in symbols_by_file.get(caller_file, [])
        if s.name == candidate_name
        and s.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.CLASS)
    ]
    if len(intra_file) == 1:
        return intra_file[0].qualified_name, None
    if len(intra_file) > 1:
        return None, "ambiguous_multiple_candidates"

    same_package_candidates = [
        s
        for dst_file in imported_files_by_src.get(caller_file, ())
        for s in symbols_by_file.get(dst_file, [])
        if s.parent_qualified_name is None
        and s.name == candidate_name
        and s.kind in (SymbolKind.FUNCTION, SymbolKind.CLASS)
    ]
    if len(same_package_candidates) == 1:
        return same_package_candidates[0].qualified_name, None
    if len(same_package_candidates) > 1:
        return None, "ambiguous_multiple_candidates"

    return None, "no_matching_symbol"
