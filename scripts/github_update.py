"""Conservative update path for a private GitHub source checkout.

Never replaces owner files or rewrites local source changes. Packaged releases
will need a separate signed-artifact updater; this only covers Git checkouts.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


REMOTE = "https://github.com/An3-Alex/Emulator_Tests.git"
ALLOWED_REMOTES = {
    REMOTE,
    REMOTE.removesuffix(".git"),
    "git@github.com:An3-Alex/Emulator_Tests.git",
}


class UpdateError(RuntimeError):
    pass


def _git(project: Path, *arguments: str, timeout: int = 60) -> str:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", *arguments], cwd=project, env=environment,
            capture_output=True, text=True, errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"Git konnte nicht ausgeführt werden: {exc}") from exc
    if result.returncode:
        message = (result.stderr or result.stdout).strip()
        raise UpdateError(message or f"Git-Fehler {result.returncode}")
    return result.stdout.strip()


def verify_checkout(project: Path) -> None:
    if not (project / ".git").exists():
        raise UpdateError("Dies ist kein Git-Checkout. Bitte den privaten Quellcode über GitHub klonen.")
    origin = _git(project, "remote", "get-url", "origin")
    if origin.rstrip("/") not in ALLOWED_REMOTES:
        raise UpdateError(f"Unbekanntes Git-Repository: {origin}")
    if _git(project, "branch", "--show-current") != "main":
        raise UpdateError("Updates sind derzeit nur auf dem Branch 'main' verfügbar.")
    if _git(project, "status", "--porcelain", "--untracked-files=no"):
        raise UpdateError("Lokale Quellcode-Änderungen vorhanden; Update wurde nicht gestartet.")


def check_updates(project: Path) -> int:
    verify_checkout(project)
    _git(project, "fetch", "origin", "main", timeout=90)
    ahead = int(_git(project, "rev-list", "--count", "origin/main..HEAD"))
    if ahead:
        raise UpdateError("Lokale Commits sind nicht auf GitHub; automatisches Update verweigert.")
    return int(_git(project, "rev-list", "--count", "HEAD..origin/main"))


def install_updates(project: Path) -> str:
    verify_checkout(project)
    # Only the previously fetched main branch is merged, never an arbitrary ref.
    return _git(project, "merge", "--ff-only", "origin/main", timeout=90)
