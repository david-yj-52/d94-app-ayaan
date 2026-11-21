import subprocess
from pathlib import Path

import pytest

IDENTITY = {
    "GIT_AUTHOR_NAME": "ayaan-test",
    "GIT_AUTHOR_EMAIL": "ayaan-test@example.com",
    "GIT_COMMITTER_NAME": "ayaan-test",
    "GIT_COMMITTER_EMAIL": "ayaan-test@example.com",
}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(cwd: Path, name: str, content: str, msg: str | None = None) -> None:
    (cwd / name).write_text(content)
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", msg or f"edit {name}")


@pytest.fixture
def origin(tmp_path, monkeypatch) -> Path:
    """Bare repo with one commit on main. Identity via env so every clone can commit."""
    for k, v in IDENTITY.items():
        monkeypatch.setenv(k, v)
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(bare), str(seed))
    git(seed, "config", "user.name", IDENTITY["GIT_AUTHOR_NAME"])
    git(seed, "config", "user.email", IDENTITY["GIT_AUTHOR_EMAIL"])
    git(seed, "checkout", "-q", "-b", "main")
    commit(seed, "README.md", "hello\n", "init")
    git(seed, "push", "-q", "origin", "main")
    return bare


@pytest.fixture
def repo(origin) -> dict:
    return {
        "repoNm": "r1",
        "gitUrl": str(origin),
        "baseBranch": "main",
        "deployBranch": "deploy",
        "sortOrd": 1,
    }


@pytest.fixture
def seed(tmp_path) -> Path:
    """Working clone of origin, for pushing commits 'from elsewhere' (e.g. onto main)."""
    return tmp_path / "seed"
