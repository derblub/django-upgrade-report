"""Read a project's dependencies from lockfiles, requirement files or an environment."""

from __future__ import annotations

import codecs
import configparser
import functools
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from packaging.markers import Marker
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

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
    external: str | None = None
    """Where the package comes from when not from PyPI, e.g. ``git https://github.com/x/y``.

    ``None`` means "resolved from PyPI". External packages are never looked up on pypi.org.
    """


@dataclass
class DependencySet:
    source: str
    """Human readable description of where the dependencies came from."""
    dependencies: dict[str, Dependency]
    python: str | None = None
    """The project's Python as ``X.Y``, when it can be determined."""
    python_source: str = ""
    """Where :attr:`python` came from, e.g. ``.python-version``."""


class NoDependenciesFound(Exception):
    pass


class SourceError(Exception):
    """A project file exists but cannot be read (bad encoding, invalid TOML or JSON)."""


PYPI_SIMPLE = "https://pypi.org/simple"
_Reader = Callable[[Path, "str | None"], "dict[str, Dependency]"]
LOCKFILES = ("uv.lock", "poetry.lock", "pdm.lock", "Pipfile.lock")
SUPPORTED = (
    "uv.lock, poetry.lock, pdm.lock, Pipfile.lock, requirements*.txt, requirements/*.txt "
    "or pyproject.toml"
)


def load(project: Path, python: str | None = None) -> DependencySet:
    """Find the most precise dependency source for ``project``.

    An explicit interpreter wins, then lockfiles (exact versions, including
    transitive dependencies), then requirement files and ``pyproject.toml``.
    ``project`` may also be one of those files, which is then read directly.
    """
    if python:
        return from_environment(python)
    if project.is_file():
        return _load_file(project)
    if not project.is_dir():
        raise NoDependenciesFound(f"{project} does not exist")

    py, py_source = _detect_python(project)
    for name in LOCKFILES:
        path = project / name
        if path.is_file():
            return _dependency_set(name, _load_lockfile(path, py), py, py_source)

    found: dict[str, Dependency] = {}
    used: list[str] = []
    for path in _requirement_files(project):
        deps = parse_requirements(path, python=py)
        if deps:
            used.append(path.relative_to(project).as_posix())
            _merge(found, deps)
    pyproject = project / "pyproject.toml"
    if pyproject.is_file():
        deps = _from_pyproject(pyproject, py)
        if deps:
            used.append("pyproject.toml")
            _merge(found, deps)
    if not found:
        raise NoDependenciesFound(f"No {SUPPORTED} dependencies found in {project}")
    return _dependency_set(", ".join(used), found, py, py_source)


def _load_file(path: Path) -> DependencySet:
    py, py_source = _detect_python(path.parent)
    if path.name in LOCKFILES:
        deps = _load_lockfile(path, py)
    elif path.name == "pyproject.toml":
        deps = _from_pyproject(path, py)
    elif path.suffix in (".txt", ".in"):
        deps = parse_requirements(path, python=py)
    else:
        raise NoDependenciesFound(f"Cannot read dependencies from {path}: expected {SUPPORTED}")
    if not deps:
        raise NoDependenciesFound(f"No dependencies found in {path}")
    return _dependency_set(str(path), deps, py, py_source)


def _dependency_set(
    source: str, deps: dict[str, Dependency], py: str | None, py_source: str
) -> DependencySet:
    py = _python_x_y(py)
    return DependencySet(source, deps, py, py_source if py else "")


def _python_x_y(python: str | None) -> str | None:
    """``python`` as ``X.Y``, or ``None`` when it is not a version (e.g. ``3.x``)."""
    try:
        v = Version(python) if python else None
    except InvalidVersion:
        return None
    return f"{v.major}.{v.minor}" if v else None


def _tolerant(read: _Reader) -> _Reader:
    """A value of the wrong type in a hand-edited file is a SourceError, not a crash."""

    @functools.wraps(read)
    def wrapper(path: Path, python: str | None = None) -> dict[str, Dependency]:
        try:
            return read(path, python)
        except (TypeError, AttributeError, KeyError) as exc:
            raise SourceError(
                f"{path} has a value this tool cannot read ({type(exc).__name__}: {exc}). "
                "Was it edited by hand? Regenerate it with its tool."
            ) from exc

    return wrapper


def _load_lockfile(path: Path, python: str | None) -> dict[str, Dependency]:
    loaders: dict[str, Callable[[Path, str | None], dict[str, Dependency]]] = {
        "uv.lock": _from_uv_lock,
        "poetry.lock": _from_poetry_lock,
        "pdm.lock": _from_pdm_lock,
        "Pipfile.lock": _from_pipfile_lock,
    }
    deps = loaders[path.name](path, python)
    if not deps:
        raise NoDependenciesFound(
            f"{path} lists no packages. It may be empty or written by an unsupported version "
            f"of its tool: regenerate it, or pass a requirements file or pyproject.toml instead."
        )
    return deps


# --- merging -----------------------------------------------------------------


def _precision(dep: Dependency) -> int:
    return 2 if dep.version else 1 if dep.spec else 0


def _combine(old: Dependency, new: Dependency) -> Dependency:
    """A pin beats a range beats nothing, regardless of order; the first pin wins."""
    best = old if _precision(old) >= _precision(new) else new
    return replace(best, external=old.external or new.external)


def _add(into: dict[str, Dependency], dep: Dependency) -> None:
    existing = into.get(dep.name)
    into[dep.name] = dep if existing is None else _combine(existing, dep)


def _merge(into: dict[str, Dependency], deps: dict[str, Dependency]) -> None:
    for dep in deps.values():
        _add(into, dep)


def _dep(
    name: str, version: str | None = None, spec: str = "", external: str | None = None
) -> Dependency:
    if version is not None and not isinstance(version, str):
        raise TypeError(f"the version of {name} is {version!r}, not a string")
    return Dependency(canonicalize_name(name), version, spec, external)


def _all_from_index(deps: dict[str, Dependency], index_url: str) -> None:
    """The project installs from ``index_url`` instead of PyPI: keep its names off pypi.org.

    Django itself is public and its release history is needed, so it stays.
    """
    if _is_pypi(index_url):
        return
    where = f"index {_clean_url(index_url)}"
    for name, dep in deps.items():
        if dep.external is None and name != "django":
            deps[name] = replace(dep, external=where)


# --- reading files -----------------------------------------------------------

_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _read_text(path: Path) -> str:
    """Read UTF-8 (with or without BOM), or UTF-16/32 with a BOM, like pip does."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SourceError(f"Could not read {path}: {exc.strerror or exc}") from exc
    encoding = next((enc for bom, enc in _BOMS if raw.startswith(bom)), "utf-8")
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError as exc:
        raise SourceError(
            f"Could not read {path}: it is not valid {encoding.upper()} "
            f"({exc.reason} at byte {exc.start}). Save it as UTF-8."
        ) from exc


def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(_read_text(path))
    except tomllib.TOMLDecodeError as exc:
        raise SourceError(f"{path} is not valid TOML: {exc}") from exc


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        raise SourceError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SourceError(f"{path} is not a JSON object")
    return data


def _table(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _tables(value: object) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


# --- describing sources that are not PyPI ------------------------------------

_VCS = ("git", "hg", "svn", "bzr")


def _clean_url(url: str) -> str:
    """Drop credentials, query and fragment, so tokens never end up in a report."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url.split("#")[0]
    netloc = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


# Public mirrors of all of PyPI: what they serve is on pypi.org under the same name.
_PYPI_MIRRORS = frozenset(
    {
        "pypi.tuna.tsinghua.edu.cn",
        "mirrors.tuna.tsinghua.edu.cn",
        "mirrors.aliyun.com",
        "pypi.mirrors.ustc.edu.cn",
        "mirrors.ustc.edu.cn",
        "mirrors.cloud.tencent.com",
        "mirrors.tencent.com",
        "repo.huaweicloud.com",
        "mirrors.huaweicloud.com",
        "pypi.douban.com",
        "mirrors.bfsu.edu.cn",
        "mirror.baidu.com",
        "mirrors.163.com",
        "mirror.sjtu.edu.cn",
        "mirrors.sjtug.sjtu.edu.cn",
        "mirror.nju.edu.cn",
    }
)


def _is_pypi(index_url: str) -> bool:
    """PyPI itself, under any of its names, or a public mirror of it."""
    parts = urlsplit(_clean_url(index_url.strip()))
    host = (parts.hostname or "").lower()
    path = parts.path.rstrip("/")
    if host in ("pypi.org", "pypi.python.org", "pypi.io"):
        return path == "/simple"
    return host in _PYPI_MIRRORS and ("simple" in path or "pypi" in path)


def _describe_url(url: str) -> str:
    vcs = re.match(r"^(git|hg|svn|bzr)\+(.*)$", url)
    if vcs:
        return f"{vcs.group(1)} {_clean_url(vcs.group(2))}"
    if url.startswith("file:"):
        return f"path {urlsplit(url).path}"
    return f"url {_clean_url(url)}"


def _vcs_external(entry: dict) -> str | None:
    for vcs in _VCS:
        if vcs in entry:
            return f"{vcs} {_clean_url(str(entry[vcs]))}"
    return None


# --- the project's Python ----------------------------------------------------

_OPERATORS = r"(\^|~=|~|===|==|!=|>=|<=|>|<)"


def _version_key(version: str) -> Version:
    try:
        return Version(version)
    except InvalidVersion:
        return Version("0")


def _major_minor(version: str) -> str:
    return ".".join((version.split(".") + ["0"])[:2])


def _full(python: str) -> str:
    return python if python.count(".") >= 2 else f"{_major_minor(python)}.0"


def _pep440_clauses(constraint: str) -> list[str]:
    """Translate one Poetry or PEP 440 constraint (without ``||``) into PEP 440 clauses."""
    clauses = []
    text = re.sub(_OPERATORS + r"\s+", r"\1", constraint.strip())
    for token in filter(None, re.split(r"[,\s]+", text)):
        match = re.fullmatch(_OPERATORS + r"?v?([\w.*+!-]+)", token)
        if not match or match.group(2) == "*":
            continue
        op, version = match.group(1) or "", match.group(2)
        numbers = [int(n) for n in re.findall(r"\d+", version)[:3]] or [0]
        if op == "^":
            index = next((i for i, n in enumerate(numbers) if n), len(numbers) - 1)
            upper = numbers[:index] + [numbers[index] + 1]
            clauses += [f">={version}", "<" + ".".join(map(str, upper))]
        elif op == "~":
            upper = [numbers[0] + 1] if len(numbers) == 1 else [numbers[0], numbers[1] + 1]
            clauses += [f">={version}", "<" + ".".join(map(str, upper))]
        elif op == "" and "*" not in version:
            clauses.append(f"=={version}.*" if version.count(".") < 2 else f"=={version}")
        else:
            clauses.append(f"{op or '=='}{version}")
    return clauses


def _python_matches(constraint: str, python: str) -> bool:
    for alternative in constraint.split("||"):
        try:
            if SpecifierSet(",".join(_pep440_clauses(alternative))).contains(_full(python)):
                return True
        except InvalidSpecifier:
            return True
    return False


def _lower_bound(constraint: object) -> str | None:
    """The lowest ``X.Y`` a (Poetry or PEP 440) Python constraint allows."""
    if not isinstance(constraint, str) or not constraint.strip():
        return None
    bounds = []
    for alternative in constraint.split("||"):
        lows = [
            _major_minor(re.sub(r"\.\*$", "", clause.lstrip("=~>")))
            for clause in _pep440_clauses(alternative)
            if clause.startswith((">=", ">", "==", "~="))
        ]
        if not lows:
            return None
        bounds.append(max(lows, key=_version_key))
    return min(bounds, key=_version_key)


def _python_version_file(directory: Path) -> str | None:
    for line in _read_text(directory / ".python-version").splitlines():
        match = re.search(r"(\d+\.\d+(?:\.\d+)?)", line.split("#")[0])
        if match:
            return match.group(1)
    return None


def _uv_lock_python(directory: Path) -> str | None:
    return _lower_bound(_read_toml(directory / "uv.lock").get("requires-python"))


def _pyproject_python(directory: Path) -> str | None:
    project = _table(_read_toml(directory / "pyproject.toml").get("project"))
    return _lower_bound(project.get("requires-python"))


def _poetry_python(directory: Path) -> str | None:
    tool = _table(_read_toml(directory / "pyproject.toml").get("tool"))
    constraint = _table(_table(tool.get("poetry")).get("dependencies")).get("python")
    if isinstance(constraint, dict):
        constraint = constraint.get("version")
    return _lower_bound(constraint)


def _pipfile_python(directory: Path) -> str | None:
    requires = _table(_table(_read_json(directory / "Pipfile.lock").get("_meta")).get("requires"))
    version = requires.get("python_version") or requires.get("python_full_version")
    return version if isinstance(version, str) and re.fullmatch(r"\d+\.\d+.*", version) else None


_PYTHON_FINDERS: tuple[tuple[str, str, Callable[[Path], str | None]], ...] = (
    (".python-version", ".python-version", _python_version_file),
    ("uv.lock", "uv.lock requires-python", _uv_lock_python),
    ("pyproject.toml", "pyproject.toml requires-python", _pyproject_python),
    ("pyproject.toml", "pyproject.toml Poetry python", _poetry_python),
    ("Pipfile.lock", "Pipfile.lock python_version", _pipfile_python),
)


def _detect_python(directory: Path) -> tuple[str | None, str]:
    """The project's Python version (it may include the patch) and where it came from."""
    for filename, label, finder in _PYTHON_FINDERS:
        if not (directory / filename).is_file():
            continue
        try:
            found = finder(directory)
        except SourceError:
            continue  # a broken file is reported when it is read as a dependency source
        if found and _python_x_y(found):
            return found, label
    return None, ""


def _environment(python: str | None) -> dict[str, str]:
    """Marker environment of the project's Python on Linux, where Django apps are deployed."""
    env = {"extra": ""}
    if python:
        env |= {
            "python_version": _major_minor(python),
            "python_full_version": _full(python),
            "sys_platform": "linux",
            "platform_system": "Linux",
            "os_name": "posix",
            "platform_machine": "x86_64",
            "implementation_name": "cpython",
            "platform_python_implementation": "CPython",
        }
    return env


def _marker_matches(marker: str, python: str | None) -> bool:
    try:
        return Marker(marker).evaluate(_environment(python))
    except Exception:  # an invalid marker or one using names packaging does not know
        return True


# --- picking one of several lockfile entries for the same package ------------

_FALLBACK_PYTHONS = ["2.7"] + [f"3.{minor}" for minor in range(4, 20)]


def _entry_matches(entry: dict, python: str) -> bool:
    for key in ("resolution-markers", "markers", "marker"):  # uv, poetry, pdm
        value = entry.get(key)
        if isinstance(value, dict):
            value = list(value.values())
        values = [v for v in (value if isinstance(value, list) else [value]) if isinstance(v, str)]
        values = [v for v in values if v.strip()]
        if values and not any(_marker_matches(v, python) for v in values):
            return False
    for key in ("requires_python", "python-versions"):  # pdm, poetry
        value = entry.get(key)
        if isinstance(value, str) and value.strip() and not _python_matches(value, python):
            return False
    return True


def _pick(entries: list[dict], python: str | None) -> dict:
    """The entry for the project's Python, else the one for the lowest Python that has one."""
    if len(entries) == 1:
        return entries[0]
    for candidate in ([python] if python else []) + _FALLBACK_PYTHONS:
        for entry in entries:
            if _entry_matches(entry, candidate):
                return entry
    return entries[0]


def _by_name(packages: Iterable[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for package in packages:
        if isinstance(package.get("name"), str):
            grouped.setdefault(canonicalize_name(package["name"]), []).append(package)
    return grouped


# --- lockfiles ---------------------------------------------------------------


def _uv_external(source: dict) -> str | None:
    if "registry" in source:
        registry = str(source["registry"])
        return None if _is_pypi(registry) else f"index {_clean_url(registry)}"
    if "git" in source:
        return f"git {_clean_url(str(source['git']))}"
    for key in ("path", "directory", "editable"):
        if key in source:
            return f"path {source[key]}"
    if "url" in source:
        return f"url {_clean_url(str(source['url']))}"
    return None


@_tolerant
def _from_uv_lock(path: Path, python: str | None) -> dict[str, Dependency]:
    data = _read_toml(path)
    manifest = _table(data.get("manifest"))
    members = {canonicalize_name(m) for m in manifest.get("members", []) if isinstance(m, str)}
    packages = _tables(data.get("package")) or _tables(data.get("distribution"))  # uv < 0.3
    deps: dict[str, Dependency] = {}
    for name, entries in _by_name(packages).items():
        package = _pick(entries, python)
        source = _table(package.get("source"))
        if name in members or "virtual" in source or source.get("editable") == ".":
            continue  # the project itself or a workspace member
        external = _uv_external(source)
        if "version" in package or external:
            _add(deps, _dep(name, package.get("version"), external=external))
    return deps


def _poetry_external(source: dict) -> str | None:
    kind, url = source.get("type"), str(source.get("url", ""))
    if kind == "git":
        return f"git {_clean_url(url)}"
    if kind in ("directory", "file"):
        return f"path {url}"
    if kind == "url":
        return f"url {_clean_url(url)}"
    if kind == "legacy" and not _is_pypi(url):
        return f"index {_clean_url(url)}"
    return None


@_tolerant
def _from_poetry_lock(path: Path, python: str | None) -> dict[str, Dependency]:
    deps: dict[str, Dependency] = {}
    for name, entries in _by_name(_tables(_read_toml(path).get("package"))).items():
        package = _pick(entries, python)
        external = _poetry_external(_table(package.get("source")))
        if "version" in package or external:
            _add(deps, _dep(name, package.get("version"), external=external))
    return deps


def _pdm_external(package: dict) -> str | None:
    if "path" in package:
        return f"path {package['path']}"
    if "url" in package:
        return f"url {_clean_url(str(package['url']))}"
    if "revision" in package:
        return f"vcs revision {package['revision']}"
    return None


@_tolerant
def _from_pdm_lock(path: Path, python: str | None) -> dict[str, Dependency]:
    deps: dict[str, Dependency] = {}
    for name, entries in _by_name(_tables(_read_toml(path).get("package"))).items():
        package = _pick(entries, python)
        if package.get("path") == ".":
            continue  # the project itself
        external = _vcs_external(package) or _pdm_external(package)
        if "version" in package or external:
            _add(deps, _dep(name, package.get("version"), external=external))
    return deps


def _pipfile_external(entry: dict) -> str | None:
    if "path" in entry:
        return f"path {entry['path']}"
    if "file" in entry:
        return _describe_url(str(entry["file"]))
    if entry.get("editable"):
        return "editable"
    return None


@_tolerant
def _from_pipfile_lock(path: Path, python: str | None) -> dict[str, Dependency]:
    data = _read_json(path)
    indexes = {
        s.get("name"): str(s.get("url", ""))
        for s in _tables(_table(data.get("_meta")).get("sources"))
    }
    # Without an "index", pipenv installs from the first source.
    first = next(iter(indexes), None)
    deps: dict[str, Dependency] = {}
    for section in ("default", "develop"):
        for name, entry in _table(data.get(section)).items():
            entry = _table(entry)
            if entry.get("path") == ".":
                continue  # the project itself
            version = str(entry.get("version", ""))
            pinned = version[2:] if version.startswith("==") else None
            external = _vcs_external(entry) or _pipfile_external(entry)
            if external is None and canonicalize_name(name) != "django":
                url = indexes.get(entry.get("index", first))
                if url and not _is_pypi(url):
                    external = f"index {_clean_url(url)}"
            _add(deps, _dep(name, pinned, external=external))
    return deps


# --- requirement files -------------------------------------------------------


def _requirement_files(project: Path) -> list[Path]:
    files = sorted(project.glob("requirements*.txt"))
    files += sorted((project / "requirements").glob("*.txt"))
    # Constraint files only pin what other files list; they are read through "-c".
    return [f for f in files if "constraint" not in f.name.lower()]


def parse_requirements(
    path: Path, _seen: set[Path] | None = None, *, python: str | None = None
) -> dict[str, Dependency]:
    """Parse a requirements file and everything it includes.

    Entries of ``-c`` constraint files only fill in versions of packages listed
    elsewhere; they never add packages of their own.
    """
    constraints: dict[str, Dependency] = {}
    indexes: list[str] = []
    deps = _parse_requirement_file(path, _seen or set(), constraints, set(), python, indexes)
    for name, dep in deps.items():
        if name in constraints:
            deps[name] = _combine(dep, constraints[name])
    if indexes:  # like pip, the last --index-url wins, whichever file it is in
        _all_from_index(deps, indexes[-1])
    return deps


def _parse_requirement_file(
    path: Path,
    seen: set[Path],
    constraints: dict[str, Dependency],
    seen_constraints: set[Path],
    python: str | None,
    indexes: list[str],
) -> dict[str, Dependency]:
    path = path.resolve()
    if path in seen or not path.is_file():
        return {}
    seen.add(path)

    deps: dict[str, Dependency] = {}
    for line in _logical_lines(_read_text(path)):
        index = re.match(r"^(-i|--index-url)(?:\s*=\s*|\s*)(\S+)", line)
        if index is not None:
            indexes.append(index.group(2))
            continue
        include = re.match(r"^(-r|--requirement|-c|--constraint)(?:\s*=\s*|\s*)(\S+)", line)
        if include is None:
            dep = _parse_line(line, path, python)
            if dep is not None:
                _add(deps, dep)
            continue
        target = path.parent / include.group(2)
        if include.group(1) in ("-c", "--constraint"):
            included = _parse_requirement_file(
                target, seen_constraints, constraints, seen_constraints, python, indexes
            )
            _merge(constraints, included)
        else:
            included = _parse_requirement_file(
                target, seen, constraints, seen_constraints, python, indexes
            )
            _merge(deps, included)
    return deps


def _logical_lines(text: str) -> list[str]:
    """Lines without comments, with backslash continuations joined like pip does."""
    lines: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).rstrip()
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        line = (pending + line).strip()
        pending = ""
        if line:
            lines.append(line)
    if pending.strip():
        lines.append(pending.strip())
    return lines


def _parse_line(line: str, path: Path, python: str | None) -> Dependency | None:
    editable = re.match(r"^(-e|--editable)(?:\s*=\s*|\s*)(\S+)", line)
    if editable:
        return _editable(editable.group(2), path)
    if line.startswith("-"):
        return None  # --index-url, --find-links, ...
    line = re.split(r"\s+--?[A-Za-z]", line)[0].strip()  # per-line options such as --hash
    if re.match(r"^(git|hg|svn|bzr)\+|^[a-z]+://|^file:", line):
        name = _egg_name(line) or _url_name(line)
        return _dep(name, external=_describe_url(line)) if name else None
    if re.match(r"^(\.{1,2}|~)?[/\\]|^\.$|^\.\.$|^[A-Za-z]:[/\\]", line):
        return _local(line, path)
    return _parse_requirement(line, python)


def _egg_name(url: str) -> str | None:
    match = re.search(r"[#&]egg=([A-Za-z0-9][A-Za-z0-9._-]*)", url)
    return match.group(1) if match else None


def _url_name(url: str) -> str | None:
    """``https://x/pkg-1.0.zip`` is ``pkg``, ``git+https://x/y.git@v1`` is ``y``."""
    path = urlsplit(re.sub(r"^(git|hg|svn|bzr)\+", "", url)).path.split("@")[0]
    last = path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*?(?=-\d|\.tar|\.zip|\.whl|\.tgz|$)", last)
    return match.group(0) if match else None


def _editable(target: str, requirements_file: Path) -> Dependency | None:
    if re.match(r"^(git|hg|svn|bzr)\+|^[a-z]+://", target):
        name = _egg_name(target) or _url_name(target)
        return _dep(name, external=_describe_url(target)) if name else None
    return _local(target, requirements_file)


def _local(target: str, requirements_file: Path) -> Dependency | None:
    """A local directory or archive: never looked up, and never left out of the report."""
    location = re.sub(r"\[.*\]$", "", target.split("#")[0])
    if location.startswith("file:"):
        location = urlsplit(location).path
    directory = (requirements_file.parent / Path(location).expanduser()).resolve()
    if requirements_file.is_relative_to(directory):
        return None  # "-e ." is the project itself
    name = _egg_name(target) or _local_project_name(directory) or _path_name(directory)
    return _dep(name, external=f"path {location}") if name else None


def _path_name(path: Path) -> str | None:
    """``libs/app`` is ``app``; ``dist/django_app-1.0.tar.gz`` is ``django_app``."""
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._]*?(?=-\d|\.tar|\.zip|\.whl|$)", path.name)
    return match.group(0) if match else None


def _local_project_name(directory: Path) -> str | None:
    if not directory.is_dir():
        return None
    try:
        data = _read_toml(directory / "pyproject.toml")
    except SourceError:
        data = {}
    tool = _table(data.get("tool"))
    for table in (_table(data.get("project")), _table(tool.get("poetry"))):
        if isinstance(table.get("name"), str):
            return table["name"]
    config = configparser.ConfigParser()
    try:
        config.read(directory / "setup.cfg", encoding="utf-8")
        return config.get("metadata", "name", fallback=None)
    except (configparser.Error, UnicodeDecodeError):
        return None


def _parse_requirement(line: str, python: str | None = None) -> Dependency | None:
    try:
        req = Requirement(line)
    except InvalidRequirement:
        return None
    if req.marker is not None and not _marker_matches(str(req.marker), python):
        return None
    if req.url:
        return _dep(req.name, external=_describe_url(req.url))
    specs = list(req.specifier)
    version = None
    if len(specs) == 1 and specs[0].operator in ("==", "===") and "*" not in specs[0].version:
        version = specs[0].version
    return _dep(req.name, version, "" if version else str(req.specifier))


def _parse_lines(lines: object, python: str | None) -> dict[str, Dependency]:
    deps: dict[str, Dependency] = {}
    for line in lines if isinstance(lines, list) else []:
        dep = _parse_requirement(line, python) if isinstance(line, str) else None
        if dep is not None:
            _add(deps, dep)
    return deps


# --- pyproject.toml ----------------------------------------------------------


@_tolerant
def _from_pyproject(path: Path, python: str | None = None) -> dict[str, Dependency]:
    """Each table is read on its own and merged: a pin anywhere beats a range elsewhere."""
    data = _read_toml(path)
    project = _table(data.get("project"))
    tool = _table(data.get("tool"))
    poetry = _table(tool.get("poetry"))

    tables = [_parse_lines(project.get("dependencies"), python)]
    tables += [
        _parse_lines(e, python) for e in _table(project.get("optional-dependencies")).values()
    ]
    tables += [_parse_lines(g, python) for g in _table(data.get("dependency-groups")).values()]

    indexes = {s.get("name"): str(s.get("url", "")) for s in _tables(poetry.get("source"))}
    poetry_tables = [_table(poetry.get("dependencies")), _table(poetry.get("dev-dependencies"))]
    poetry_tables += [
        _table(_table(group).get("dependencies")) for group in _table(poetry.get("group")).values()
    ]
    tables += [_from_poetry_table(table, indexes, python) for table in poetry_tables]

    deps: dict[str, Dependency] = {}
    for table in tables:
        _merge(deps, table)
    primary = _poetry_primary_index(_tables(poetry.get("source")))
    if primary:  # Poetry 2 installs [project] dependencies from its sources, too
        _all_from_index(deps, primary)
    _apply_uv_sources(deps, _table(tool.get("uv")))
    return deps


def _poetry_primary_index(sources: list[dict]) -> str | None:
    """A private source that replaces PyPI.

    Poetry searches primary sources (the default priority) first and turns the implicit PyPI
    off when there is one, unless PyPI is configured as a primary source itself.
    """
    primary = [
        s
        for s in sources
        if s.get("priority", "default" if s.get("default") else "primary") in ("primary", "default")
        and not s.get("secondary")
    ]
    if any(_is_pypi(str(s.get("url", ""))) or not s.get("url") for s in primary):
        return None  # PyPI itself is primary
    return str(primary[0]["url"]) if primary else None


def _from_poetry_table(
    table: dict, indexes: dict[str, str], python: str | None
) -> dict[str, Dependency]:
    deps: dict[str, Dependency] = {}
    for name, constraint in table.items():
        if name.lower() == "python":
            continue
        if isinstance(constraint, list):  # multiple constraints, e.g. one per Python version
            constraint = _pick_poetry_constraint(_tables(constraint), python)
        external = None
        if isinstance(constraint, dict):
            external = _poetry_dependency_external(constraint, indexes)
            constraint = constraint.get("version", "")
        if not isinstance(constraint, str):
            continue
        constraint = constraint.strip()
        exact = re.fullmatch(r"=?=?\s*(\d[\w.]*)", constraint)
        if exact and not constraint.startswith(("^", "~", ">", "<")):
            _add(deps, _dep(name, exact.group(1), external=external))
        else:
            _add(deps, _dep(name, None, _poetry_spec(constraint), external))
    return deps


def _poetry_spec(constraint: str) -> str:
    """``^2.4`` as ``>=2.4,<3``; alternatives stay joined by ``||``."""
    alternatives = []
    for alternative in constraint.split("||"):
        clauses = _pep440_clauses(alternative)
        if not clauses:
            return ""  # "*" or empty: anything
        alternatives.append(",".join(clauses))
    return " || ".join(alternatives)


def _pick_poetry_constraint(options: list[dict], python: str | None) -> dict | None:
    """The constraint whose ``python`` and ``markers`` match the project, else the first."""
    for option in options if python else []:
        wanted, marker = option.get("python"), option.get("markers")
        if isinstance(wanted, str) and not _python_matches(wanted, python):
            continue
        if isinstance(marker, str) and not _marker_matches(marker, python):
            continue
        return option
    return options[0] if options else None


def _poetry_dependency_external(constraint: dict, indexes: dict[str, str]) -> str | None:
    if external := _vcs_external(constraint):
        return external
    if "path" in constraint:
        return f"path {constraint['path']}"
    if "url" in constraint:
        return f"url {_clean_url(str(constraint['url']))}"
    url = indexes.get(constraint.get("source"))
    if url and not _is_pypi(url):
        return f"index {_clean_url(url)}"
    return None


def _apply_uv_sources(deps: dict[str, Dependency], uv: dict) -> None:
    """``[tool.uv.sources]`` moves a dependency to git, a path or another index."""
    indexes = {i.get("name"): str(i.get("url", "")) for i in _tables(uv.get("index"))}
    default = next(
        (str(i.get("url", "")) for i in _tables(uv.get("index")) if i.get("default")), ""
    )
    for name, source in _table(uv.get("sources")).items():
        canonical = canonicalize_name(name)
        if isinstance(source, list):
            source = next(iter(_tables(source)), {})
        if canonical not in deps or not isinstance(source, dict):
            continue
        if source.get("workspace"):
            del deps[canonical]  # a workspace member, like the project itself
            continue
        if "index" in source:
            url = indexes.get(source["index"], "")
            external = None if _is_pypi(url) else f"index {_clean_url(url) or source['index']}"
        else:
            external = _uv_external(source)
        if external:
            deps[canonical] = replace(deps[canonical], external=external)
    if default:  # replaces PyPI for everything without a source of its own
        _all_from_index(deps, default)


# --- a live environment ------------------------------------------------------

# Runs inside the target interpreter, which may be as old as Python 2.7: no f-strings.
# Distributions come in sys.path order, so the first one per name is the one Python imports.
_LIST_DISTRIBUTIONS = """
import json, sys
packages = []
try:
    import importlib.metadata as m
except ImportError:
    try:
        import importlib_metadata as m
    except ImportError:
        m = None
if m is not None:
    for d in m.distributions():
        name = d.metadata.get("Name")
        if name:
            packages.append([name, d.version, d.read_text("direct_url.json")])
else:
    import pkg_resources
    for d in pkg_resources.working_set:
        direct = None
        if getattr(d, "has_metadata", None) and d.has_metadata("direct_url.json"):
            direct = d.get_metadata("direct_url.json")
        packages.append([d.project_name, d.version, direct])
python = "%d.%d.%d" % tuple(sys.version_info[:3])
sys.stdout.write("\\n" + MARKER + json.dumps({"python": python, "packages": packages}))
"""
# Wrappers and sitecustomize may print banners first; the listing follows this marker.
_MARKER = "--- django-upgrade-report packages ---"


def _tail(text: str, lines: int = 5, width: int = 200) -> str:
    """The last ``lines`` lines, each cut to ``width`` characters."""
    tail = text.strip().splitlines()[-lines:]
    return "\n".join(line if len(line) <= width else line[: width - 1] + "…" for line in tail)


def _direct_url_external(text: object) -> str | None:
    """Where pip or uv installed a package from, per PEP 610's ``direct_url.json``.

    Only direct installs (VCS, local directory or editable, URL or file) write it.
    """
    try:
        info = json.loads(text) if isinstance(text, str) else None
    except ValueError:
        return None
    if not isinstance(info, dict) or not isinstance(info.get("url"), str):
        return None
    url = info["url"]
    vcs = _table(info.get("vcs_info")).get("vcs")
    if vcs:
        return f"{vcs} {_clean_url(url)}"
    if "dir_info" in info or url.startswith("file:"):
        return f"path {urlsplit(url).path}"
    return f"url {_clean_url(url)}"


def from_environment(python: str) -> DependencySet:
    try:
        result = subprocess.run(
            [python, "-c", f"MARKER = {_MARKER!r}\n" + _LIST_DISTRIBUTIONS],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise NoDependenciesFound(f"Could not run {python}: {exc.strerror or exc}") from exc
    except subprocess.CalledProcessError as exc:
        reason = _tail(exc.stderr or exc.stdout or "") or f"exit status {exc.returncode}"
        raise NoDependenciesFound(f"Could not list packages with {python}:\n{reason}") from exc
    before, marker, listing = result.stdout.rpartition(_MARKER)
    try:
        if not marker:
            raise ValueError("no marker")
        data = json.loads(listing)
        packages = [(str(p[0]), str(p[1]), (p[2:] or [None])[0]) for p in data["packages"]]
    except (ValueError, KeyError, TypeError) as exc:
        output = _tail(before if marker else result.stdout) or _tail(result.stderr) or "no output"
        raise NoDependenciesFound(
            f"Could not list packages with {python}: unexpected output:\n{output}"
        ) from exc

    deps: dict[str, Dependency] = {}
    for name, version, direct_url in packages:
        dep = _dep(name, version, external=_direct_url_external(direct_url))
        deps.setdefault(dep.name, dep)  # the first on sys.path is the one Python imports
    py = data.get("python") if isinstance(data.get("python"), str) else None
    return _dependency_set(f"packages installed for {python}", deps, py, "--python")
