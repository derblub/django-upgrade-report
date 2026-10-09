"""Write src/django_upgrade_report/data/django_removals.json from Django's release notes.

    python3 scripts/django_removals.py              # fetch docs/releases/X.Y.txt from GitHub
    python3 scripts/django_removals.py NOTES_DIR    # or read them from a checkout of Django

Run it once per Django feature release, and commit the result. Each entry is one bullet of
"Features removed in X.Y": its first sentence, the names it mentions, and a link.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src" / "django_upgrade_report" / "data"
RAW = "https://raw.githubusercontent.com/django/django/main/docs/releases/{}.txt"
VERSIONS = ["3.0", "3.1", "3.2", "4.0", "4.1", "4.2", "5.0", "5.1", "5.2", "6.0", "6.1", "6.2"]
# What django-upgrade (https://github.com/adamchainz/django-upgrade) rewrites, by a name the
# entry mentions. Kept by hand from its list of fixers, only those known for sure: a wrong
# "django-upgrade fixes this" would send people to a tool that does nothing for them.
FIXERS = {
    "USE_L10N",
    "index_together",
    "assertFormError",
    "assertFormsetError",
    "NullBooleanField",
    "django.utils.timezone.utc",
    "ugettext",
    "ugettext_lazy",
    "ungettext",
    "force_text",
    "smart_text",
    "url",
    "django.conf.urls.url",
    "QuerySetPaginator",
    "FixedOffset",
}
_ROLE = re.compile(r":[\w:]+:`~?([^`<]+?)(?:\s*<([^>]+)>)?`")
_LITERAL = re.compile(r"``([^`]+)``")
_NAME = re.compile(r"^[A-Za-z_][\w.]*$")


def removals(text: str, version: str) -> list[dict]:
    """The bullets of "Features removed in ``version``", nested bullets folded in."""
    lines = text.splitlines()
    try:
        start = next(
            i for i, line in enumerate(lines) if line.strip() == f"Features removed in {version}"
        )
    except StopIteration:
        return []
    items: list[list[str]] = []
    for line in lines[start + 2 :]:
        if re.match(r"^[=\-~^*]{4,}\s*$", line) and items:  # the next heading's underline
            items[-1].pop()  # its title went into the last item
            break
        if line.startswith("* "):
            items.append([line[2:]])
        elif items and (line.startswith("  ") or not line.strip()):
            items[-1].append(line.strip())
        elif line.strip() and items:
            items.append([])  # a paragraph between bullets ends the last one
    anchor = f"features-removed-in-{version.replace('.', '-')}"
    url = f"https://docs.djangoproject.com/en/{version}/releases/{version}/#{anchor}"
    found = []
    for item in items:
        body = " ".join(part for part in item if part).strip()
        if not body:
            continue
        symbols = [m.strip("()") for m in _LITERAL.findall(body)]
        symbols += [(m[1] or m[0]).strip("()~.") for m in _ROLE.findall(body)]
        symbols = [s.removesuffix("()") for s in symbols]
        # Names only, not values such as False, 'http' or errors=None.
        symbols = list(
            dict.fromkeys(
                s for s in symbols if _NAME.match(s) and s not in ("True", "False", "None", "str")
            )
        )
        # "django.utils.http.urlquote(), urlquote_plus()": the siblings live in the same module.
        module = ""
        named = []
        for symbol in symbols:
            if symbol.startswith("django.") and "." in symbol:
                module = symbol.rsplit(".", 1)[0]
            elif module and "." not in symbol and symbol[:1].isalpha():
                symbol = f"{module}.{symbol}"
            named.append(symbol)
        symbols = list(dict.fromkeys(named))
        plain = _ROLE.sub(lambda m: m.group(1).lstrip("."), _LITERAL.sub(r"\1", body))
        plain = plain.split(" * ")[0]  # a nested list follows "removed from:"
        sentence = re.split(r"(?<=[a-z)\]])\.\s", plain + " ", maxsplit=1)[0].strip()
        sentence = sentence.removesuffix(".").replace(" :", ":")
        found.append(
            {
                "version": version,
                "text": sentence,
                "symbols": symbols,
                "url": url,
                "fixer": any(s in FIXERS or s.split(".")[-1] in FIXERS for s in symbols),
            }
        )
    return found


def main(argv: list[str]) -> int:
    entries = []
    for version in VERSIONS:
        if argv:
            path = Path(argv[0]) / f"{version}.txt"
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
        else:
            try:
                with urllib.request.urlopen(RAW.format(version), timeout=30) as response:
                    text = response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                if exc.code != 404:  # a release that has no notes yet
                    raise
                text = ""
        entries += removals(text, version)
    OUT.mkdir(parents=True, exist_ok=True)
    data = {
        "source": "docs/releases/*.txt of https://github.com/django/django",
        "removals": entries,
    }
    (OUT / "django_removals.json").write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print(f"{len(entries)} removals from {len(VERSIONS)} releases")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
