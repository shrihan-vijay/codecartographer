import subprocess
from pathlib import Path


def compute_loc(file_path: Path) -> int:
    with file_path.open(encoding="utf-8", errors="ignore") as f:
        return sum(1 for _ in f)


def compute_git_churn(repo_path: Path, relative_file_path: Path) -> int:
    """Number of commits touching the file, via `git log --follow --oneline`.
    Returns 0 if the repo has no history for the file (e.g. untracked, or repo_path
    isn't a git repository).
    """
    result = subprocess.run(
        ["git", "log", "--follow", "--oneline", "--", str(relative_file_path.as_posix())],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return 0
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return len(lines)
