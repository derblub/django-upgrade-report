"""Questions in the terminal for what the project leaves out."""

from __future__ import annotations

import io

import pytest
from conftest import DJANGO, FakePyPI, release

from django_upgrade_report import cli, prompts
from django_upgrade_report.prompts import ask_missing, terminal_ask
from django_upgrade_report.sources import Dependency, DependencySet


class Script:
    """Answers questions in order, and remembers them."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked: list[tuple[str, list[str]]] = []

    def __call__(self, question, options):
        self.asked.append((question, options))
        return self.answers.pop(0)


def project(django=None, spec="", python=None):
    deps = {"django": Dependency("django", django, spec=spec)}
    return DependencySet("test", deps, python=python)


def test_asks_for_the_django_you_run(index):
    ask = Script(0, None)
    answers = ask_missing(project(spec=">=4.2,<5.0"), index, "auto", None, ask)
    assert ask.asked[0] == (
        "Django is not pinned (Django>=4.2,<5.0). Which version do you run?",
        ["4.2.7"],
    )
    assert answers.current == "4.2.7"
    assert answers.tip() == "Tip: next time pass --from 4.2.7"


def test_shows_the_newest_series_the_requirement_allows(index):
    ask = Script(None, None)
    ask_missing(project(spec=">=4.2"), index, "auto", None, ask)
    assert ask.asked[0][1] == ["4.2.7", "5.0", "5.1", "5.2.3", "6.0"]


def test_many_series_keep_the_lts_ones():
    versions = ["4.0", "4.1", "4.2", "4.2.20", "5.0", "5.1", "5.2", "6.0", "6.1"]
    index = FakePyPI({"django": [release("Django", v) for v in versions]})
    ask = Script(None, None)
    ask_missing(project(spec=">=4.0"), index, "auto", None, ask)
    assert ask.asked[0][1] == ["4.2.20", "5.2", "6.0", "6.1"]


def test_nothing_to_ask(index):
    ask = Script()
    answers = ask_missing(project("4.2.7", python="3.12"), index, "5.2", None, ask)
    assert ask.asked == [] and answers.tip() is None


def test_asks_for_a_smaller_step():
    old = [release("Django", v, uploaded="2021-04-06") for v in ("3.2", "3.2.25")]
    index = FakePyPI({"django": old + DJANGO})
    ask = Script(0)
    answers = ask_missing(project("3.2.25", python="3.12"), index, "auto", None, ask)
    assert ask.asked == [
        (
            "Django 5.2 skips the 4.2 LTS. Which target?",
            ["4.2 (one LTS at a time, easier)", "5.2"],
        )
    ]
    assert answers.target == "4.2"


def test_asks_for_the_python(index):
    django = index.packages["django"] = list(index.packages["django"])  # DJANGO is shared
    django[-3] = django[-3] | {"requires_python": ">=3.12"}  # 5.2.0
    ask = Script(2)
    answers = ask_missing(project("4.2.7"), index, "5.2", None, ask)
    question, options = ask.asked[0]
    assert question == "Django 5.2 needs Python 3.12 or newer. Which Python does your project run?"
    assert options == ["3.10", "3.11", "3.12", "3.13", "3.14"]  # 3.8, 3.9 are older still
    assert answers.python == "3.12"
    assert answers.tip() == "Tip: next time add a .python-version file with 3.12"


@pytest.mark.parametrize(
    ("typed", "chosen"),
    [
        ("2\n", 1),
        ("\n", None),
        ("3\n", None),
        ("skip\n", None),
        ("x\n9\n²\n1\n", 0),
        ("", None),
    ],
)
def test_terminal_ask(monkeypatch, capsys, typed, chosen):
    monkeypatch.setattr(prompts.sys, "stdin", io.StringIO(typed))
    assert terminal_ask("Which?", ["a", "b"]) == chosen
    err = capsys.readouterr().err
    assert err.startswith("Which?\n1) a   2) b   3) skip\n> ")
    if "9" in typed:
        assert "Type a number from 1 to 3." in err


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(cli, "_at_terminal", lambda: True)


@pytest.fixture
def unpinned(tmp_path, index, monkeypatch):
    (tmp_path / "requirements.txt").write_text("Django>=4.2,<5.0\ndjango-before==1.0\n")
    (tmp_path / ".python-version").write_text("3.12\n")
    monkeypatch.setattr(cli, "PyPI", lambda *args, **kwargs: index)
    return tmp_path


def test_the_cli_asks_at_a_terminal(unpinned, tty, monkeypatch, capsys):
    monkeypatch.setattr(cli, "terminal_ask", Script(0))
    assert cli.main([str(unpinned)]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("Django 4.2.7 → 5.2\n")
    assert "Django is not pinned" not in captured.out  # the answer replaced the warning
    assert "Tip: next time pass --from 4.2.7" in captured.err


@pytest.mark.parametrize(
    "extra",
    [["--no-input"], ["-f", "json"], ["--explain", "django-before"], ["-o", "report.txt"]],
)
def test_the_cli_never_asks_when_nobody_reads(unpinned, tty, monkeypatch, extra):
    def boom(*args):
        raise AssertionError("asked")

    monkeypatch.setattr(cli, "terminal_ask", boom)
    monkeypatch.chdir(unpinned)
    assert cli.main([str(unpinned), *extra]) == 0


def test_the_cli_never_asks_in_ci(unpinned, tty, monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(cli, "terminal_ask", Script())
    assert cli.main([str(unpinned)]) == 0


def test_python_options_reach_below_what_the_target_needs(index):
    ask = Script(0)
    answers = ask_missing(project("4.2.7"), index, "5.2", None, ask)
    assert ask.asked[0][1] == ["3.8", "3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]
    assert answers.python == "3.8"


def test_a_skipped_question_assumes_what_the_report_assumes():
    """Django<5.0 unanswered means 4.2.x for the report, so 5.2 skips no LTS from there."""
    old = [release("Django", v, uploaded="2021-04-06") for v in ("3.2", "3.2.25")]
    index = FakePyPI({"django": old + DJANGO})
    ask = Script(None, None)
    ask_missing(project(spec=">=3.2,<5.0", python="3.12"), index, "auto", None, ask)
    assert [q for q, _ in ask.asked] == [
        "Django is not pinned (Django>=3.2,<5.0). Which version do you run?"
    ]


def test_a_series_the_spec_allows_in_part():
    ask = Script(None)
    ask_missing(
        project(spec=">=4.2,<4.2.5", python="3.12"),
        FakePyPI(
            {
                "django": [
                    release("Django", "4.2"),
                    release("Django", "4.2.3"),
                    release("Django", "4.2.7"),
                ]
            }
        ),
        "4.2",
        None,
        ask,
    )
    assert ask.asked[0][1] == ["4.2.3"]


def test_the_answered_python_reaches_the_markers(unpinned, tty, monkeypatch, capsys):
    (unpinned / ".python-version").unlink()
    (unpinned / "requirements.txt").write_text(
        "Django==4.2.7\n"
        'django-before==1.0; python_version < "3.12"\n'
        'django-blocked==1.0; python_version >= "3.12"\n'
    )
    monkeypatch.setattr(cli, "terminal_ask", Script(4))  # 3.12 of 3.8..3.14
    cli.main([str(unpinned)])
    out = capsys.readouterr().out
    assert "django-blocked" in out and "django-before" not in out
    assert "Python 3.12" in out
