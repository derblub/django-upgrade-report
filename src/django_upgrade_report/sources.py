"""Read a project's dependencies from lockfiles, requirement files or an environment."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


@dataclass(frozen=True)
class Dependency:
    name: str
    """Canonical project name, e.g. ``django-debug-toolbar``."""
    version: str | None
    """The exact version in use, when the source pins one."""
    spec: str = ""
    """The declared version range when no exact version is known, e.g. ``>=4.0``."""


@dataclass
class DependencySet:
    source: str
    """Human readable description of where the dependencies came from."""
    dependencies: dict[str, Dependency]


class NoDependenciesFound(Exception):
    pass


def load(project: Path, python: str | None = None) -> DependencySet:
    """Find the most precise dependency source for ``project``.

    An explicit interpreter wins, then lockfiles (exact versions, including
    transitive dependencies), then requirement files and ``pyproject.toml``.
    """
    if python:
        return from_environment(python)

    for name, loader in (
        ("uv.lock", _from_uv_lock),
        ("poetry.lock", _from_poetry_lock),
        ("pdm.lock", _from_poetry_lock),
        ("Pipfile.lock", _from_pipfile_lock),
    ):
        path = project / name
        if path.is_file():
            return DependencySet(name, loader(path))

    found: dict[str, Dependency] = {}
    used: list[str] = []
    for path in _requirement_files(project):
        deps = parse_requirements(path)
        if deps:
            used.append(str(path.relative_to(project)))
            _merge(found, deps)
    pyproject = project / "pyproject.toml"
    if pyproject.is_file():
        deps = _from_pyproject(pyproject)
        if deps:
            used.append("pyproject.toml")
            _merge(found, deps)
    if not found:
        raise NoDependenciesFound(
            f"No uv.lock, poetry.lock, Pipfile.lock, requirements*.txt or pyproject.toml "
            f"dependencies found in {project}"
        )
    return DependencySet(", ".join(used), found)


def _merge(into: dict[str, Dependency], deps: dict[str, Dependency]) -> None:
    for name, dep in deps.items():
        existing = into.get(name)
        if existing is None or (existing.version is None and dep.version is not None):
            into[name] = dep


def _dep(name: str, version: str | None = None, spec: str = "") -> tuple[str, Dependency]:
    canonical = canonicalize_name(name)
    return canonical, Dependency(canonical, version, spec)


# --- lockfiles ---------------------------------------------------------------


def _from_uv_lock(path: Path) -> dict[str, Dependency]:
    data = tomllib.loads(path.read_text())
    deps = {}
    for package in data.get("package", []):
        source = package.get("source", {})
        if "editable" in source or "virtual" in source:
            continue  # the project itself or a workspace member
        if "version" in package:
            deps.update([_dep(package["name"], package["version"])])
    return deps


def _from_poetry_lock(path: Path) -> dict[str, Dependency]:
    data = tomllib.loads(path.read_text())
    return dict(
        _dep(package["name"], package["version"])
        for package in data.get("package", [])
        if "version" in package
    )


def _from_pipfile_lock(path: Path) -> dict[str, Dependency]:
    data = json.loads(path.read_text())
    deps = {}
    for section in ("default", "develop"):
        for name, entry in data.get(section, {}).items():
            version = entry.get("version", "")
            deps.update([_dep(name, version[2:] if version.startswith("==") else None)])
    return deps


# --- requirement files and pyproject.toml ------------------------------------


def _requirement_files(project: Path) -> list[Path]:
    files = sorted(project.glob("requirements*.txt"))
    files += sorted((project / "requirements").glob("*.txt"))
    return files


def parse_requirements(path: Path, _seen: set[Path] | None = None) -> dict[str, Dependency]:
    seen = _seen if _seen is not None else set()
    path = path.resolve()
    if path in seen or not path.is_file():
        return {}
    seen.add(path)

    deps: dict[str, Dependency] = {}
    for raw in path.read_text().splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if not line:
            continue
        include = re.match(r"^(-r|--requirement|-c|--constraint)\s*=?\s*(\S+)", line)
        if include:
            _merge(deps, parse_requirements(path.parent / include.group(2), seen))
            continue
        if line.startswith("-"):
            continue  # -e, --index-url, --hash continuation lines, ...
        line = line.split(" --hash")[0].rstrip(" \\")
        deps.update(_parse_requirement(line))
    return deps


def _parse_requirement(line: str) -> dict[str, Dependency]:
    try:
        req = Requirement(line)
    except InvalidRequirement:
        return {}
    if req.marker is not None and not req.marker.evaluate({"extra": ""}):
        return {}
    specs = list(req.specifier)
    version = None
    if len(specs) == 1 and specs[0].operator in ("==", "===") and "*" not in specs[0].version:
        version = specs[0].version
    return dict([_dep(req.name, version, "" if version else str(req.specifier))])


def _from_pyproject(path: Path) -> dict[str, Dependency]:
    data = tomllib.loads(path.read_text())
    lines: list[str] = []

    project = data.get("project", {})
    lines += project.get("dependencies", [])
    for extra in project.get("optional-dependencies", {}).values():
        lines += extra
    for group in data.get("dependency-groups", {}).values():
        lines += [item for item in group if isinstance(item, str)]

    deps: dict[str, Dependency] = {}
    for line in lines:
        deps.update(_parse_requirement(line))

    poetry = data.get("tool", {}).get("poetry", {})
    tables = [poetry.get("dependencies", {}), poetry.get("dev-dependencies", {})]
    tables += [group.get("dependencies", {}) for group in poetry.get("group", {}).values()]
    for table in tables:
        for name, constraint in table.items():
            if name.lower() == "python":
                continue
            if isinstance(constraint, dict):
                constraint = constraint.get("version", "")
            if not isinstance(constraint, str):
                continue
            exact = re.fullmatch(r"=?=?\s*(\d[\w.]*)", constraint.strip())
            if exact and not constraint.strip().startswith(("^", "~", ">", "<")):
                deps.update([_dep(name, exact.group(1))])
            else:
                deps.update([_dep(name, None, constraint)])
    return deps


# --- a live environment ------------------------------------------------------

_LIST_DISTRIBUTIONS = """
import json, importlib.metadata as m
print(json.dumps({d.metadata["Name"]: d.version for d in m.distributions() if d.metadata["Name"]}))
"""


def from_environment(python: str) -> DependencySet:
    try:
        output = subprocess.run(
            [python, "-c", _LIST_DISTRIBUTIONS],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise NoDependenciesFound(f"Could not list packages with {python}: {exc}") from exc
    deps = dict(_dep(name, version) for name, version in json.loads(output).items())
    return DependencySet(f"packages installed for {python}", deps)
