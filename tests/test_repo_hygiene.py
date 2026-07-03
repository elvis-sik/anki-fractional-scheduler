from __future__ import annotations

import json
import py_compile
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ABSOLUTE_PATH_NEEDLES = tuple(
    str(path)
    for path in (ROOT, ROOT.parent)
    if str(path) not in {"", "."}
)

SKIP_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".uv-cache",
    ".venv",
    ".venv-apkg",
    "__pycache__",
    "backups",
    "build",
    "coverage",
    "dist",
    "drafts",
    "input",
    "media",
    "node_modules",
    "out",
    "templates",
    "tmp",
}

SKIP_PREFIXES = {
    "data/derived/",
    "data/raw/",
    "polymath/",
}

PORTABLE_SUFFIXES = {
    "",
    ".css",
    ".example",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".yaml",
    ".yml",
}

FORBIDDEN_TRACKED_FILES = {
    ".env",
    "addon/meta.json",
}

FORBIDDEN_TRACKED_SUFFIXES = {
    ".ankiaddon",
    ".apkg",
    ".colpkg",
    ".db",
    ".log",
    ".sqlite",
}

PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
OP_REFERENCE_RE = re.compile("op" + r"://[^\s\"']+")
OP_REFERENCE_ALLOWED = {
    ".env.example",
}


def git_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return [Path(line) for line in result.stdout.splitlines() if line]


def is_skipped(path: Path) -> bool:
    as_posix = path.as_posix()
    return bool(SKIP_PARTS.intersection(path.parts)) or any(
        as_posix.startswith(prefix) for prefix in SKIP_PREFIXES
    )


def readable_text(path: Path) -> str | None:
    try:
        return (ROOT / path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError:
        return None


class RepositoryHygieneTest(unittest.TestCase):
    def test_readme_exists(self) -> None:
        self.assertTrue(
            any((ROOT / name).exists() for name in ("README.md", "README.rst", "README")),
            "repository should have a README",
        )

    def test_claude_imports_agents_when_present(self) -> None:
        agents = ROOT / "AGENTS.md"
        if not agents.exists():
            self.skipTest("AGENTS.md is not present")
        claude = ROOT / "CLAUDE.md"
        self.assertTrue(claude.exists(), "CLAUDE.md should exist when AGENTS.md exists")
        self.assertIn("@AGENTS.md", claude.read_text(encoding="utf-8"))

    def test_no_workspace_absolute_paths_in_portable_files(self) -> None:
        offenders: list[str] = []
        for path in git_files():
            if is_skipped(path) or path.suffix not in PORTABLE_SUFFIXES:
                continue
            text = readable_text(path)
            if text is not None and any(needle in text for needle in ABSOLUTE_PATH_NEEDLES):
                offenders.append(path.as_posix())

        self.assertEqual([], offenders, "portable files should use relative paths")

    def test_no_tracked_local_state_or_artifacts(self) -> None:
        offenders: list[str] = []
        for path in git_files():
            as_posix = path.as_posix()
            if as_posix in FORBIDDEN_TRACKED_FILES or path.suffix in FORBIDDEN_TRACKED_SUFFIXES:
                offenders.append(as_posix)

        self.assertEqual([], offenders, "local state, logs, and release artifacts should not be tracked")

    def test_no_secret_material_in_portable_files(self) -> None:
        offenders: list[str] = []
        for path in git_files():
            if is_skipped(path) or path.suffix not in PORTABLE_SUFFIXES:
                continue
            text = readable_text(path)
            if text is None:
                continue
            as_posix = path.as_posix()
            if PRIVATE_KEY_RE.search(text):
                offenders.append(as_posix)
            if OP_REFERENCE_RE.search(text) and as_posix not in OP_REFERENCE_ALLOWED:
                offenders.append(as_posix)

        self.assertEqual([], sorted(set(offenders)), "secret material should not be tracked")

    def test_tracked_json_files_parse(self) -> None:
        for path in git_files():
            if is_skipped(path) or path.suffix != ".json":
                continue
            with self.subTest(path=path.as_posix()):
                text = readable_text(path)
                if text is None:
                    self.skipTest(f"{path} is not UTF-8 text")
                json.loads(text)

    def test_tracked_python_files_compile(self) -> None:
        for path in git_files():
            if is_skipped(path) or path.suffix != ".py":
                continue
            with self.subTest(path=path.as_posix()):
                py_compile.compile(str(ROOT / path), doraise=True)


def run_path_check() -> int:
    suite = unittest.TestSuite()
    suite.addTest(
        RepositoryHygieneTest("test_no_workspace_absolute_paths_in_portable_files")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--path-only"]:
        raise SystemExit(run_path_check())
    unittest.main()
