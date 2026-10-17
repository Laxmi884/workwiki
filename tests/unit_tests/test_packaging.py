"""The installed package carries every file the code reads at runtime (skills, templates).

An editable install (what `langgraph dev` and the `langgraph up` image use) reads from `src/`, so a
file missing from `package-data` only breaks a real wheel install. This test catches it without
building one.
"""

import fnmatch
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


def _package_data() -> dict[str, list[str]]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    setuptools = pyproject["tool"]["setuptools"]
    assert setuptools["packages"]["find"]["where"] == ["src"]
    return setuptools["package-data"]


def _owning_package(path: Path) -> Path:
    folder = path.parent
    while not (folder / "__init__.py").exists():
        folder = folder.parent
    return folder


def test_every_runtime_data_file_is_package_data():
    globs = _package_data()
    missing = []
    for path in sorted((SRC / "llm_wiki").rglob("*")):
        if (
            not path.is_file()
            or path.suffix in {".py", ".pyc"}
            or "__pycache__" in path.parts
        ):
            continue
        package = _owning_package(path)
        name = ".".join(package.relative_to(SRC).parts)
        relative = path.relative_to(package).as_posix()
        patterns = globs.get(name, []) + globs.get("*", [])
        if not any(fnmatch.fnmatch(relative, pattern) for pattern in patterns):
            missing.append(f"{name}: {relative}")
    assert missing == []
