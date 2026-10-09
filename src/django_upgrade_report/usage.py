"""Which installed packages the project's code uses: read locally, never imported, never sent.

A package counts as used when one of its module names appears in the code: an import, a
dotted path in a string (``INSTALLED_APPS``, ``MIDDLEWARE``, ``ENGINE`` and the like), a
template tag library (``{% load crispy_forms_tags %}``) or a management command a script
runs. Anything unsure counts as used: a note to remove a package must never be wrong.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from packaging.utils import canonicalize_name

MAX_FILES = 20_000
MAX_BYTES = 50 * 1024 * 1024
SKIP_DIRS = {
    ".git",
    ".hg",
    ".venv",
    "venv",
    "env",
    "node_modules",
    ".tox",
    ".nox",
    "build",
    "dist",
    "site-packages",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
NOTE = "not imported or configured in your code: remove it instead?"

# Distributions whose modules are not named like them, from the top_level.txt of their wheels.
MODULES: dict[str, tuple[str, ...]] = {
    "djangorestframework": ("rest_framework",),
    "djangorestframework-simplejwt": ("rest_framework_simplejwt",),
    "djangorestframework-gis": ("rest_framework_gis",),
    "drf-spectacular": ("drf_spectacular",),
    "drf-yasg": ("drf_yasg",),
    "drf-nested-routers": ("rest_framework_nested",),
    "django-filter": ("django_filters",),
    "django-crispy-forms": ("crispy_forms",),
    "crispy-bootstrap5": ("crispy_bootstrap5",),
    "django-cors-headers": ("corsheaders",),
    "django-environ": ("environ",),
    "django-allauth": ("allauth",),
    "django-redis": ("django_redis",),
    "django-storages": ("storages",),
    "django-celery-beat": ("django_celery_beat",),
    "django-celery-results": ("django_celery_results",),
    "django-debug-toolbar": ("debug_toolbar",),
    "django-extensions": ("django_extensions",),
    "django-import-export": ("import_export",),
    "django-model-utils": ("model_utils",),
    "django-mptt": ("mptt",),
    "django-polymorphic": ("polymorphic",),
    "django-reversion": ("reversion",),
    "django-taggit": ("taggit",),
    "django-ckeditor": ("ckeditor", "ckeditor_uploader"),
    "django-guardian": ("guardian",),
    "django-oauth-toolkit": ("oauth2_provider",),
    "django-otp": ("django_otp",),
    "django-phonenumber-field": ("phonenumber_field",),
    "django-simple-history": ("simple_history",),
    "django-tables2": ("django_tables2",),
    "django-two-factor-auth": ("two_factor",),
    "django-anymail": ("anymail",),
    "django-axes": ("axes",),
    "django-compressor": ("compressor",),
    "django-constance": ("constance",),
    "django-countries": ("django_countries",),
    "django-health-check": ("health_check",),
    "django-modeltranslation": ("modeltranslation",),
    "django-money": ("djmoney",),
    "django-picklefield": ("picklefield",),
    "django-solo": ("solo",),
    "django-webpack-loader": ("webpack_loader",),
    "django-widget-tweaks": ("widget_tweaks",),
    "sorl-thumbnail": ("sorl",),
    "easy-thumbnails": ("easy_thumbnails",),
    "social-auth-app-django": ("social_django",),
    "wagtail": ("wagtail",),
    "channels-redis": ("channels_redis",),
    "graphene-django": ("graphene_django",),
    "psycopg2-binary": ("psycopg2",),
    "psycopg-binary": ("psycopg",),
    "mysqlclient": ("MySQLdb",),
    "pillow": ("PIL",),
    "python-dateutil": ("dateutil",),
    "python-dotenv": ("dotenv",),
    "python-decouple": ("decouple",),
    "pyyaml": ("yaml",),
    "beautifulsoup4": ("bs4",),
    "sentry-sdk": ("sentry_sdk",),
    "scikit-learn": ("sklearn",),
    "pyjwt": ("jwt",),
    "dj-database-url": ("dj_database_url",),
    "whitenoise": ("whitenoise",),
}
# Template tag libraries whose name is not a module of their package.
TAG_LIBRARIES = {
    "crispy_forms_tags": "crispy_forms",
    "crispy_forms_filters": "crispy_forms",
    "render_table": "django_tables2",
    "render_bundle": "webpack_loader",
    "thumbnail": "sorl",
    "compress": "compressor",
    "account": "allauth",
    "socialaccount": "allauth",
    "widget_tweaks": "widget_tweaks",
    "humanize": "",
    "static": "",
    "i18n": "",
    "l10n": "",
    "tz": "",
    "cache": "",
}
# Management commands that belong to a package.
COMMANDS = {
    "runserver_plus": "django_extensions",
    "shell_plus": "django_extensions",
    "graph_models": "django_extensions",
    "show_urls": "django_extensions",
    "reset_db": "django_extensions",
    "spectacular": "drf_spectacular",
    "createcachetable": "",
    "rebuild_index": "haystack",
    "update_index": "haystack",
    "compress": "compressor",
    "createinitialrevisions": "reversion",
    "thumbnail": "sorl",
}
# Run, not imported: a server, a test or lint tool, a plugin loaded by entry point.
TOOLS = {
    "gunicorn",
    "uwsgi",
    "uvicorn",
    "daphne",
    "hypercorn",
    "waitress",
    "coverage",
    "ruff",
    "black",
    "isort",
    "flake8",
    "mypy",
    "pylint",
    "tox",
    "nox",
    "pre-commit",
    "ipython",
    "ipdb",
    "honcho",
    "watchdog",
    "factory-boy",
    "django-stubs",
    "djangorestframework-stubs",
    "types-requests",
    "setuptools",
    "wheel",
    "pip",
}
_TOOL_PREFIXES = ("pytest", "flake8-", "sphinx", "mkdocs", "types-", "django-stubs")
_DOTTED = re.compile(r"^[A-Za-z_]\w*(?:\.\w+)+$")
_LOAD = re.compile(r"\{%-?\s*load\s+([^%]+?)\s*-?%\}")
_MANAGE = re.compile(r"manage\.py\s+([a-z_]+)")
_SCRIPTS = ("Makefile", "Procfile", "Dockerfile", "justfile", "tox.ini", "setup.cfg")


@dataclass
class Scan:
    """What the code names, with where it was first seen."""

    names: dict[str, str] = field(default_factory=dict)
    """Top-level module name, lower case, to the first place that names it (``"settings.py:4"``)."""
    files: int = 0
    python: int = 0
    """Python files read: without any, there is no code to judge by."""
    unreadable: list[str] = field(default_factory=list)
    """Python files that could not be parsed: skipped."""
    complete: bool = True
    """False when the project is too big to read: then nothing is called unused."""

    def add(self, name: str, where: str) -> None:
        if name:
            self.names.setdefault(name.lower(), where)


def scan(root: Path) -> Scan:
    found = Scan()
    size = 0
    for directory, dirs, files in os.walk(root):
        here = Path(directory)
        dirs[:] = sorted(
            d for d in dirs if d not in SKIP_DIRS and not (here / d / "pyvenv.cfg").is_file()
        )
        for name in sorted(files):
            path = here / name
            kind = _kind(path, root)
            if kind is None:
                continue
            found.files += 1
            try:
                size += path.stat().st_size
            except OSError:
                continue
            if found.files > MAX_FILES or size > MAX_BYTES:
                found.complete = False
                return found
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            where = path.relative_to(root).as_posix()
            if kind == "python":
                found.python += 1
                if not _python(text, where, found):
                    found.unreadable.append(where)
            elif kind == "template":
                for match in _LOAD.finditer(text):
                    for library in match.group(1).split():
                        if library == "from":
                            break
                        found.add(TAG_LIBRARIES.get(library, library), where)
            else:
                for match in _MANAGE.finditer(text):
                    found.add(COMMANDS.get(match.group(1), ""), where)
    return found


def _kind(path: Path, root: Path) -> str | None:
    if path.suffix == ".py":
        return "python"
    parts = path.relative_to(root).parts
    if path.suffix in (".html", ".txt", ".xml") and "templates" in parts:
        return "template"
    if path.name in _SCRIPTS or path.suffix == ".sh":
        return "script"
    if path.suffix in (".yml", ".yaml") and ".github" in parts:
        return "script"
    return None


def _python(text: str, where: str, found: Scan) -> bool:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        line = f"{where}:{getattr(node, 'lineno', 0)}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0], line)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module.split(".")[0], line)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _DOTTED.match(node.value):
                found.add(node.value.split(".")[0], line)
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)) and _apps(node):
            for item in ast.walk(node.value) if node.value else ():
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    found.add(item.value.split(".")[0], f"{where}:{item.lineno}")
        elif isinstance(node, ast.Call) and _manage_call(node):
            command = node.args[0].value
            found.add(COMMANDS.get(command, ""), line)
    return True


def _apps(node: ast.Assign | ast.AugAssign | ast.AnnAssign) -> bool:
    """``INSTALLED_APPS``, ``THIRD_PARTY_APPS += [...]``: lists of app names, dots or not."""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return any(isinstance(t, ast.Name) and t.id.upper().endswith("APPS") for t in targets)


def _manage_call(node: ast.Call) -> bool:
    """``call_command("shell_plus")``."""
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    first = node.args[0] if node.args else None
    return (
        name == "call_command" and isinstance(first, ast.Constant) and isinstance(first.value, str)
    )


def modules(name: str, known: dict[str, tuple[str, ...]] | None = None) -> tuple[str, ...]:
    """The module names a distribution installs: ``known`` (from the environment, exact),
    else the table, else guesses from its name."""
    canonical = canonicalize_name(name)
    if known and known.get(canonical):
        return known[canonical]
    if canonical in MODULES:
        return MODULES[canonical]
    plain = canonical.replace("-", "_")
    guesses = [plain]
    for prefix in ("django_", "python_"):
        if plain.startswith(prefix) and len(plain) > len(prefix) + 1:
            guesses.append(plain[len(prefix) :])
    return tuple(dict.fromkeys(guesses))


def is_tool(name: str) -> bool:
    canonical = canonicalize_name(name)
    return canonical in TOOLS or canonical.startswith(_TOOL_PREFIXES)


def unused(name: str, found: Scan, known: dict[str, tuple[str, ...]] | None = None) -> bool:
    """True only when the scan was complete and nothing names any of the package's modules."""
    if not found.complete or not found.python or is_tool(name):
        return False
    return where(name, found, known) is None


def where(name: str, found: Scan, known: dict[str, tuple[str, ...]] | None = None) -> str | None:
    """The first place the code names one of the package's modules."""
    for module in modules(name, known):
        if module.lower() in found.names:
            return found.names[module.lower()]
    return None
