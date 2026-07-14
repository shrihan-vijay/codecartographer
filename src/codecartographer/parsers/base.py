import enum
from dataclasses import dataclass, field


class SymbolKind(enum.StrEnum):
    FUNCTION = "FUNCTION"
    METHOD = "METHOD"
    CLASS = "CLASS"


@dataclass
class ParsedSymbol:
    kind: SymbolKind
    name: str
    qualified_name: str
    parent_qualified_name: str | None
    """Qualified name of the enclosing symbol (class/function), or None if the
    symbol is top-level in the file. Used to derive CONTAINS edges; when None the
    symbol is contained directly by its FILE node."""
    file_path: str
    start_line: int
    end_line: int
    signature: str
    docstring: str | None
    language: str


@dataclass
class ParsedImport:
    file_path: str
    module: str
    """Raw imported module text as written, e.g. 'os.path', '.utils', '../lib'."""
    line: int
    is_relative: bool


@dataclass
class ParsedCall:
    file_path: str
    caller_qualified_name: str
    """Qualified name of the enclosing symbol, or the file's own qualified_name
    (its repo-relative path) if the call occurs at module top level."""
    callee_name: str
    """Raw callee text as written, e.g. 'foo', 'self.bar', 'obj.method'."""
    line: int


@dataclass
class ParseResult:
    symbols: list[ParsedSymbol] = field(default_factory=list)
    imports: list[ParsedImport] = field(default_factory=list)
    calls: list[ParsedCall] = field(default_factory=list)
