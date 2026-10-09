"""Release plumbing that only breaks on the day of a release."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from django_upgrade_report import __version__

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

ROOT = Path(__file__).parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_version_is_the_same_everywhere():
    """The release workflow checks the tag against pyproject.toml and __init__.py."""
    assert __version__ == PYPROJECT["project"]["version"]


def test_build_backend_writes_metadata_twine_accepts():
    """hatchling 1.32 writes Metadata-Version 2.5, which `twine check --strict` rejects."""
    hatchling = next(r for r in PYPROJECT["build-system"]["requires"] if r.startswith("hatchling"))
    assert "<1.32" in hatchling


def test_release_moves_the_major_tag_the_readme_uses():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert re.search(r'git tag --force "\$major"', workflow)
    assert "__init__.py" in workflow


def test_pre_commit_hook():
    """pre-commit reads .pre-commit-hooks.yaml from the repository root."""
    text = (ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8")
    lines = [line.removeprefix("- ").strip() for line in text.splitlines() if ": " in line]
    hook = dict(line.split(": ", 1) for line in lines)
    assert hook["id"] == "django-upgrade-report"
    assert hook["entry"].split()[0] == "django-upgrade-report"
    assert {"--prefer-cache", "--errors-as-warnings", "--quiet"} <= set(hook["entry"].split())
    files = re.compile(hook["files"].strip("'"))
    # The hook reads the project at the repository root, so only files there count.
    for path in ("uv.lock", "poetry.lock", "requirements/base.txt", "requirements-dev.in"):
        assert files.search(path), path
    for path in ("app/poetry.lock", "docs/requirements.md", "uv.lock.bak", "requirements/a/b.txt"):
        assert not files.search(path), path
