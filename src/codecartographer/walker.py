from collections.abc import Iterator
from pathlib import Path

import pathspec

SOURCE_EXTENSIONS = {".py", ".ts", ".tsx"}

# Skipped regardless of .gitignore contents -- these are never source we want to index,
# and repos frequently don't bother gitignoring them (e.g. vendored deps checked in,
# or a missing .gitignore entry shouldn't pull megabytes of node_modules into the index).
ALWAYS_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
    ".next",
    "out",
    "target",
    "vendor",
    ".eggs",
}


def _load_gitignore(repo_path: Path) -> "pathspec.PathSpec[pathspec.pattern.Pattern]":
    gitignore_file = repo_path / ".gitignore"
    if not gitignore_file.is_file():
        return pathspec.PathSpec.from_lines("gitwildmatch", [])
    lines = gitignore_file.read_text(errors="ignore").splitlines()
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def walk_source_files(repo_path: Path) -> Iterator[Path]:
    """Yield repo-relative paths to Python/TypeScript source files under repo_path,
    skipping vendored/generated directories and anything matched by the repo's
    top-level .gitignore.
    """
    repo_path = repo_path.resolve()
    spec = _load_gitignore(repo_path)

    for dirpath, dirnames, filenames in _walk(repo_path):
        rel_dir = dirpath.relative_to(repo_path)

        dirnames[:] = [
            d
            for d in dirnames
            if d not in ALWAYS_SKIP_DIRS
            and not d.endswith(".egg-info")
            and not spec.match_file(str((rel_dir / d).as_posix()) + "/")
        ]

        for filename in filenames:
            if Path(filename).suffix not in SOURCE_EXTENSIONS:
                continue
            rel_file = rel_dir / filename
            if spec.match_file(rel_file.as_posix()):
                continue
            yield rel_file


def _walk(repo_path: Path) -> Iterator[tuple[Path, list[str], list[str]]]:
    import os

    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames.sort()
        filenames.sort()
        yield Path(dirpath), dirnames, filenames
