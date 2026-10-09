"""Make the screenshots of the README (docs/assets/) and the wiki (docs/wiki/images/).

    CHROMIUM=/path/to/chromium uv run --with playwright --with textual python scripts/screenshots.py

Needs the network (PyPI, and GitHub for the signs of support) and a Chromium. Each picture
comes from a small sample project built in a temporary directory, so running it again after
a change to the output gives the same pictures with the new output. Commit what it writes.
"""

from __future__ import annotations

import asyncio
import html
import os
import pty
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"
WIKI = ROOT / "docs" / "wiki" / "images"
SAMPLE_CODE = ROOT / "tests" / "data" / "usage"
CLI = [sys.executable, "-m", "django_upgrade_report"]

SHOP = """\
Django==4.2.7
django-allauth==0.57.0
django-celery-beat==2.5.0
django-ckeditor==6.7.0
django-cors-headers==4.3.0
django-crispy-forms==2.0
crispy-bootstrap5==0.7
django-debug-toolbar==4.2.0
django-environ==0.11.2
django-extensions==3.2.3
django-filter==23.3
django-import-export==3.3.1
django-polymorphic==3.1.0
django-reversion==5.0.6
djangorestframework==3.14.0
djangorestframework-simplejwt==5.3.0
django-model-utils==4.3.1
django-storages==1.14.2
django-taggit==4.0.0
"""
FORK_LOCK = [
    ("Django", "4.2.7", None, None),
    ("django-allauth", "0.58.1", None, None),
    ("django-crispy-forms", "2.1", None, None),
    ("crispy-bootstrap5", "2023.10", None, None),
    ("djangorestframework", "3.14.0", None, None),
    ("django-autocomplete-light", "3.9.7", None, None),
    ("django-jsonfield", "1.4.1", None, None),
    ("django-storages", "1.14.2", None, None),
    ("django-htmx", "1.22.0", None, None),
    (
        "django-taggit",
        "4.0.0",
        "https://github.com/acme/django-taggit.git",
        'Django = ">=3.2,<5.0"',
    ),
    ("pdfkit", "1.0.0", "https://github.com/acme/python-pdfkit.git", None),
]
OLD = (
    "Django==3.2.25\ndjango-filter==21.1\ndjango-debug-toolbar==3.2.4\n"
    "django-crispy-forms==1.14.0\n"
)
PYTHON = (
    "Django==4.2.7\ndjango-filter==23.3\nnumpy==1.22.4\npsycopg2-binary==2.9.3\nrequests==2.31.0\n"
)
WAGTAIL = (
    "Django==4.2.16\nwagtail==5.2.3\nwagtail-localize==1.7\nwagtail-modeladmin==2.0.0\n"
    "wagtail-grapple==0.24.0\nwagtailmenus==3.1.9\n"
)
REMOVALS = "Django==4.2.7\ndjango-taggit==4.0.0\ndjango-crispy-forms==2.0\nwhitenoise==6.5.0\n"

COLORS = {"31": "#ff6b6b", "32": "#3ddc97", "33": "#f5c542", "35": "#c792ea", "36": "#4cc9f0"}


def chromium():
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    return playwright.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)


# --- projects ---------------------------------------------------------------------------


def projects(base: Path) -> dict[str, Path]:
    made = {}

    def project(name: str, files: dict[str, str]) -> Path:
        path = base / name
        path.mkdir()
        for file, text in files.items():
            (path / file).write_text(text, encoding="utf-8")
        made[name] = path
        return path

    project("shop", {"requirements.txt": SHOP})
    lock = []
    for name, version, git, deps in FORK_LOCK:
        lock.append(f'[[package]]\nname = "{name}"\nversion = "{version}"\nfiles = []\n')
        if deps:
            lock.append(f"\n[package.dependencies]\n{deps}\n")
        if git:
            lock.append(f'\n[package.source]\ntype = "git"\nurl = "{git}"\nreference = "HEAD"\n')
        lock.append("\n")
    project(
        "project",
        {
            "poetry.lock": "".join(lock) + '[metadata]\nlock-version = "2.0"\n',
            "pyproject.toml": '[tool.poetry.dependencies]\npython = "^3.11"\nDjango = "4.2.7"\n',
            ".python-version": "3.11\n",
        },
    )
    project("old", {"requirements.txt": OLD})
    project("wagtail", {"requirements.txt": WAGTAIL, ".python-version": "3.12\n"})
    project("python", {"requirements.txt": PYTHON, ".python-version": "3.10\n"})
    shutil.copytree(SAMPLE_CODE, base / "code")
    (base / "code" / "requirements.txt").write_text(REMOVALS, encoding="utf-8")
    made["code"] = base / "code"
    return made


# --- the terminal -----------------------------------------------------------------------


def run_in_terminal(args: list[str], cwd: Path, columns: int = 110) -> str:
    """The output of the tool as a terminal shows it, colours included."""
    controller, terminal = pty.openpty()
    env = {**os.environ, "COLUMNS": str(columns), "TERM": "xterm-256color"}
    env.pop("NO_COLOR", None)
    env.pop("CI", None)
    process = subprocess.Popen(
        [*CLI, *args], cwd=cwd, stdin=subprocess.DEVNULL, stdout=terminal, stderr=terminal, env=env
    )
    os.close(terminal)
    chunks = []
    while True:
        try:
            chunk = os.read(controller, 65536)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    process.wait()
    os.close(controller)
    text = b"".join(chunks).decode("utf-8", errors="replace").replace("\r", "")
    return text.split("\x1b[2K")[-1].rstrip()  # after the last progress line


def only(output: str, section: str) -> str:
    """The headline, the "from ..." line, and the block that starts with ``section``."""
    lines = output.splitlines()
    plain = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in lines]
    about = [lines[i] for i, line in enumerate(plain) if line.startswith("from ")][:1]
    start = next(i for i, line in enumerate(plain) if line.startswith(section))
    end = next((i for i in range(start, len(lines)) if not plain[i].strip()), len(lines))
    return "\n".join([lines[0], *about, "", *lines[start:end]])


def ansi_to_html(text: str) -> str:
    out, style = [], set()
    for part in re.split(r"(\x1b\[[0-9;]*m)", text):
        match = re.fullmatch(r"\x1b\[([0-9;]*)m", part)
        if match:
            codes = match.group(1).split(";")
            style = set() if codes in (["0"], [""]) else style | set(codes)
            continue
        if not part:
            continue
        css = []
        if "1" in style:
            css.append("font-weight:700;color:#f2f4f3")
        if "2" in style:
            css.append("color:#8b949e")
        css += [f"color:{COLORS[c]}" for c in style if c in COLORS]
        escaped = html.escape(part)
        out.append(f'<span style="{";".join(css)}">{escaped}</span>' if css else escaped)
    return "".join(out)


def terminal_shot(browser, command: str, output: str, path: Path, title: str = "~/project"):
    page = f"""<!doctype html><meta charset=utf-8><style>
    body{{margin:0;background:#000;padding:24px;display:inline-block}}
    .win{{background:#11151b;border:1px solid #30363d;border-radius:14px;width:fit-content;
      min-width:880px;box-shadow:0 20px 60px rgba(0,0,0,.5)}}
    .bar{{height:40px;border-bottom:1px solid #30363d;position:relative;display:flex;
      align-items:center;padding-left:16px;gap:8px}}
    .bar i{{width:12px;height:12px;border-radius:50%;display:inline-block}}
    .bar span{{position:absolute;left:0;right:0;text-align:center;color:#8b949e;
      font:13px ui-monospace,'DejaVu Sans Mono',monospace}}
    pre{{margin:0;padding:24px 28px 28px;color:#c9d1d9;
      font:15px/1.6 'IBM Plex Mono','DejaVu Sans Mono',ui-monospace,monospace}}
    .p{{color:#4cc9f0}}
    </style><div class=win><div class=bar><i style="background:#ff5f57"></i>
    <i style="background:#febc2e"></i><i style="background:#28c840"></i>
    <span>{html.escape(title)}</span></div>
    <pre><span class=p>$</span> {html.escape(command)}

{ansi_to_html(output)}</pre></div>"""
    shot(browser, page, path, selector="body")


def shot(browser, page_html: str, path: Path, selector: str = "body", **options) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(page_html)
    page = browser.new_page(
        device_scale_factor=2, viewport={"width": 1000, "height": 800}, **options
    )
    page.goto(Path(f.name).as_uri())
    page.locator(selector).screenshot(path=path)
    page.close()
    os.unlink(f.name)
    print(f"wrote {path.relative_to(ROOT)}")


# --- the HTML report --------------------------------------------------------------------


def html_shots(browser, report: Path, out: dict[str, Path]) -> None:
    for scheme, path in out.items():
        page = browser.new_page(
            device_scale_factor=2, viewport={"width": 1000, "height": 900}, color_scheme=scheme
        )
        page.goto(report.as_uri())
        page.click('tr[data-name="django-allauth"] summary')
        page.check('input[data-todo="django:django-celery-beat"]')
        page.check('input[data-todo="django:django-ckeditor"]')
        page.mouse.move(0, 0)
        height = 1240
        fade(page, height)
        page.screenshot(path=path, clip={"x": 0, "y": 0, "width": 1000, "height": height})
        page.close()
        print(f"wrote {path.relative_to(ROOT)}")


def fade(page, height: int) -> None:
    """The page fades into its background where the picture ends."""
    page.evaluate(
        """h => {
        const fade = document.createElement('div');
        fade.style.cssText = `position:absolute;left:0;right:0;top:${h - 160}px;height:160px;` +
          'background:linear-gradient(to bottom, transparent, var(--bg));pointer-events:none';
        document.body.style.position = 'relative';
        document.body.appendChild(fade);
    }""",
        height,
    )


def path_shot(browser, report: Path, path: Path) -> None:
    page = browser.new_page(device_scale_factor=2, viewport={"width": 1000, "height": 900})
    page.goto(report.as_uri())
    fade(page, 900)
    page.screenshot(path=path, clip={"x": 0, "y": 0, "width": 1000, "height": 900})
    page.close()
    print(f"wrote {path.relative_to(ROOT)}")


# --- -i -----------------------------------------------------------------------------------


def tui_svg(project: Path, svg: Path) -> None:
    """-i on a project, two packages ticked off, saved as SVG by Textual. Before the browser
    starts: Playwright keeps an event loop running, and Textual needs one of its own."""
    from django_upgrade_report import cli, tui

    def run(report, recompute, state):
        async def go():
            app = tui.ReportApp(report, recompute, None)
            async with app.run_test(size=(118, 30)) as pilot:
                await pilot.pause()
                await pilot.press("space", "down", "space", "down", "down")
                await pilot.pause()
                app.save_screenshot(str(svg))

        asyncio.run(go())
        return 0

    tui.run = run
    cli._at_terminal = lambda: True
    sys.stdout.isatty = lambda: True
    os.environ.pop("CI", None)
    status = cli.main(
        [str(project), "-i", "--no-input", "--target", "6.0", "--python-target", "none"]
    )
    if status or not svg.is_file():
        raise SystemExit(f"-i did not run: status {status}")


def tui_shot(browser, svg: Path, path: Path) -> None:
    page = browser.new_page(device_scale_factor=1.5, viewport={"width": 1500, "height": 900})
    page.goto(svg.as_uri())
    page.locator("svg").screenshot(path=path)
    page.close()
    svg.unlink()
    print(f"wrote {path.relative_to(ROOT)}")


# --- all of them ----------------------------------------------------------------------------


def main() -> int:
    WIKI.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        made = projects(base)
        svg = base / "tui.svg"
        tui_svg(made["shop"], svg)
        browser = chromium()

        def term(name, args, command, path, section=None):
            output = run_in_terminal([str(made[name]), "--no-input", *args], made[name])
            if section:
                output = only(output, section)
            terminal_shot(browser, command, output, path)

        term("project", ["--target", "5.2"], "uvx django-upgrade-report", ASSETS / "terminal.png")
        shutil.copy(ASSETS / "terminal.png", WIKI / "terminal.png")
        term(
            "shop",
            ["--target", "5.2", "--emit", "uv"],
            "django-upgrade-report --emit uv",
            WIKI / "emit.png",
        )
        term(
            "python",
            ["--target", "6.0", "--no-scan-code"],
            "django-upgrade-report --target 6.0",
            WIKI / "python.png",
            section="Python 3.12 first",
        )
        term(
            "shop",
            ["--target", "6.0", "--evidence", "--no-scan-code"],
            "django-upgrade-report --target 6.0 --evidence",
            WIKI / "signs.png",
            section="Check manually",
        )
        term(
            "wagtail",
            ["--framework", "wagtail", "--target", "7.0", "--no-scan-code"],
            "django-upgrade-report --framework wagtail --target 7.0",
            WIKI / "wagtail.png",
        )
        term(
            "code",
            ["--target", "5.2"],
            "django-upgrade-report",
            WIKI / "removals.png",
            section="Removed in Django",
        )

        report = base / "report.html"
        subprocess.run(
            [
                *CLI,
                str(made["shop"]),
                "--target",
                "5.2",
                "--python-target",
                "none",
                "-f",
                "html",
                "-o",
                str(report),
                "--no-input",
            ],
            check=True,
        )
        shutil.copy(report, ROOT / "docs" / "example-report.html")
        html_shots(
            browser,
            report,
            {"light": ASSETS / "report-light.png", "dark": ASSETS / "report-dark.png"},
        )
        shutil.copy(ASSETS / "report-light.png", WIKI / "html-report.png")

        path_report = base / "path.html"
        subprocess.run(
            [
                *CLI,
                str(made["old"]),
                "--target",
                "5.2",
                "--via",
                "lts",
                "--python-target",
                "none",
                "-f",
                "html",
                "-o",
                str(path_report),
                "--no-input",
            ],
            check=True,
        )
        path_shot(browser, path_report, WIKI / "path.png")

        tui_shot(browser, svg, ASSETS / "tui.png")
        shutil.copy(ASSETS / "tui.png", WIKI / "tui.png")
    browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
