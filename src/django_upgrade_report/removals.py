"""What Django removed on the way to the target, and which of it the project's code still uses.

The list comes from the "Features removed" sections of Django's release notes, kept in
``data/django_removals.json`` by ``scripts/django_removals.py``. A removal counts as used only
when the code names the very thing that is gone; anything less certain is listed, not matched.
Rewriting the code is django-upgrade's job, not this tool's.
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass, field
from importlib import resources

from packaging.version import InvalidVersion, Version

from django_upgrade_report.usage import Scan

DJANGO_UPGRADE = "https://github.com/adamchainz/django-upgrade"


@dataclass
class Removal:
    version: str
    text: str
    symbols: list[str]
    url: str
    fixer: bool
    """django-upgrade rewrites code that uses it."""
    used_in: list[str] = field(default_factory=list)
    """Where the code uses it, at most three places; empty when it does not or cannot tell."""


@functools.cache
def _data() -> tuple[dict, ...]:
    text = resources.files(__package__).joinpath("data/django_removals.json").read_text("utf-8")
    return tuple(json.loads(text)["removals"])


def between(current: str | None, target: str) -> list[Removal]:
    """The removals of every release after ``current`` (X.Y or a full version) up to ``target``."""
    try:
        have = Version(current) if current else None
        goal = Version(target)
    except InvalidVersion:
        return []
    if have is None:
        return []
    have = Version(f"{have.major}.{have.minor}")
    return [
        Removal(e["version"], e["text"], list(e["symbols"]), e["url"], bool(e["fixer"]))
        for e in _data()
        if have < Version(e["version"]) <= goal
    ]


# An entry about an argument, a default, a format or a behaviour names things that are not
# gone themselves: "Support for passing raw column aliases to QuerySet.order_by() is removed".
_PARTIAL = re.compile(
    r"^(support for|the ability|passing|the undocumented ability|the default\b)|argument|"
    r"\bmessage\b|is required|no longer|signature",
    re.IGNORECASE,
)
_REMOVED = re.compile(r"\b(is|are) removed\b", re.IGNORECASE)
_SETTING = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")


def match(removal: Removal, found: Scan) -> list[str]:
    """Where the code uses what ``removal`` takes away, at most three places."""
    if not _REMOVED.search(removal.text) or _PARTIAL.search(removal.text):
        return []
    places: list[str] = []
    for symbol in removal.symbols:
        where = _find(symbol, removal.text, found)
        if where and where not in places:
            places.append(where)
    return places[:3]


def _find(symbol: str, text: str, found: Scan) -> str | None:
    if symbol.startswith("django.") and symbol.count(".") >= 2:
        if symbol in found.imports:
            return found.imports[symbol]
        module, name = symbol.rsplit(".", 1)
        short = f"{module.rsplit('.', 1)[-1]}.{name}"  # timezone.utc after importing timezone
        if module in found.imports or any(m.endswith(f".{module}") for m in found.imports):
            return found.attributes.get(short)
        return None
    if _SETTING.match(symbol) and "setting" in text:
        return found.settings.get(symbol)
    if symbol.startswith("Meta."):
        return found.meta.get(symbol.removeprefix("Meta."))
    if "template filter" in text and re.fullmatch(r"[a-z_]+", symbol):
        return found.filters.get(symbol)
    head, _, method = symbol.rpartition(".")
    # request.is_ajax(), self.assertFormError(); not a word as common as "iterator".
    method_like = "_" in method or len(method) >= 10
    if head[:1].isupper() and "." not in head and "method" in text and method_like:
        return found.attributes.get(method)
    if re.fullmatch(r"[A-Z]\w+", symbol) and "field" in text:
        # "The NullBooleanField model field is removed, except for support in historical
        # migrations": models.NullBooleanField outside migrations.
        for name, where in (*found.attributes.items(), *found.imports.items()):
            if name.endswith(f".{symbol}") and "/migrations/" not in f"/{where}":
                return where
    return None


def check(current: str | None, target: str, found: Scan | None) -> list[Removal]:
    """The removals on the way, with where the code uses them when it was read."""
    removals = between(current, target)
    if found is not None and found.complete and found.python:
        for removal in removals:
            removal.used_in = match(removal, found)
    return removals
