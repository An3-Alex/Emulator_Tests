"""Offline source-update tests with a temporary local Git remote."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
from github_update import UpdateError, check_updates, install_updates
import github_update


@unittest.skipUnless(shutil.which("git"), "Git executable required")
class GithubUpdateTests(unittest.TestCase):
    @staticmethod
    def git(where: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments], cwd=where, capture_output=True, text=True,
            check=True,
        ).stdout.strip()

    def test_fast_forward_only_and_dirty_checkout_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = root / "remote.git"
            self.git(root, "init", "--bare", "-b", "main", str(remote))
            author = root / "author"
            self.git(root, "clone", str(remote), str(author))
            self.git(author, "config", "user.name", "Offline Test")
            self.git(author, "config", "user.email", "offline@example.invalid")
            (author / "version.txt").write_text("v1", encoding="utf-8")
            self.git(author, "add", "version.txt")
            self.git(author, "commit", "-m", "first")
            self.git(author, "push", "origin", "main")
            consumer = root / "consumer"
            self.git(root, "clone", str(remote), str(consumer))

            with patch.object(github_update, "ALLOWED_REMOTES", {str(remote)}):
                self.assertEqual(check_updates(consumer), 0)
                (author / "version.txt").write_text("v2", encoding="utf-8")
                self.git(author, "commit", "-am", "second")
                self.git(author, "push", "origin", "main")
                self.assertEqual(check_updates(consumer), 1)
                install_updates(consumer)
                self.assertEqual((consumer / "version.txt").read_text(encoding="utf-8"), "v2")
                (consumer / "version.txt").write_text("local edits", encoding="utf-8")
                with self.assertRaisesRegex(UpdateError, "Lokale Quellcode-Änderungen"):
                    check_updates(consumer)


if __name__ == "__main__":
    unittest.main()
