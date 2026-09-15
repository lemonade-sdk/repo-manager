"""Real git repositories on disk. The store's whole contract is git behavior, and a mocked
subprocess would only prove that the mock agrees with itself."""

import subprocess
import tempfile
import unittest
from pathlib import Path


def git(path, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(path), *args], text=True, capture_output=True, check=check
    )


def init_repo(path, initial="main"):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", initial)
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "commit.gpgsign", "false")
    return path


def commit_file(path, name, content, message=""):
    (path / name).parent.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(content, encoding="utf-8")
    git(path, "add", name)
    git(path, "commit", "-q", "-m", message or f"add {name}")
    return git(path, "rev-parse", "HEAD").stdout.strip()


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="repo-manager-test-")
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
