from pathlib import Path

from sentence_transformers import SentenceTransformer

from codecartographer.parsers.base import ParsedSymbol

MODEL_NAME = "all-MiniLM-L6-v2"


def build_chunk_text(repo_path: Path, symbol: ParsedSymbol) -> str:
    """signature + docstring + source body, in that order, joined for embedding."""
    parts = [symbol.signature]
    if symbol.docstring:
        parts.append(symbol.docstring)
    body = _read_body(repo_path, symbol)
    if body:
        parts.append(body)
    return "\n\n".join(parts)


def _read_body(repo_path: Path, symbol: ParsedSymbol) -> str | None:
    try:
        lines = (
            (repo_path / symbol.file_path).read_text(encoding="utf-8", errors="ignore").splitlines()
        )
    except OSError:
        return None
    # start_line/end_line are 1-indexed and inclusive.
    start = max(symbol.start_line - 1, 0)
    end = min(symbol.end_line, len(lines))
    if start >= end:
        return None
    return "\n".join(lines[start:end])


class Embedder:
    """Wraps a local sentence-transformers model. Loading the model is slow (downloads
    on first use, then loads weights into memory) -- construct one Embedder and reuse
    it across an indexing run rather than per-symbol.
    """

    def __init__(self, model_name: str = MODEL_NAME) -> None:
        self.model_name = model_name
        try:
            # Once cached, never phone home to check for updates -- "local" should mean
            # no network calls, not just "no API key".
            self._model = SentenceTransformer(model_name, local_files_only=True)
        except OSError:
            self._model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        return [vector.tolist() for vector in vectors]
