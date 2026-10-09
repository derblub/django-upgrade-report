---
name: plan-step
description: Implement the next open step of docs/implementation-plan.md end to end (code, tests, docs, self-review, commit, push) and update its progress table. Run it once by hand, or repeatedly with `/loop /plan-step`, which stops at every release boundary, at a blocker, or when the plan is done.
---

# plan-step: one iteration of the implementation plan

Each run takes **exactly one** step from the progress table in
`docs/implementation-plan.md` (section "Fortschritt"), finishes it completely, and leaves the
repository green, committed and pushed. The plan is written in German; code, docs, commit
messages and the tool's output stay in English, like the rest of the repository.

## 1. Orient

1. `git status`: the working tree must be clean. If it is not, and the changes belong to a step
   marked `in Arbeit`, continue that step (go to 3). Otherwise stop and tell the user what is
   uncommitted: never discard work you did not make.
2. Pull the current branch (`git pull origin <branch>`). Never work on `main`.
3. Run the baseline: `uv run --group dev pytest -q`. If it is red before you change anything,
   that is the step: fix it, or mark the run blocked (see 6) when the cause is outside the repo.

## 2. Pick the step

- The first row in the progress table whose status is `offen` or `in Arbeit`.
- Check its "Abhängigkeiten" line in the plan. If a dependency is not `erledigt`, take the
  dependency first (add a note to the row if the table order was wrong).
- If the row is marked `blockiert`, skip it only when later rows do not depend on it;
  otherwise stop (see 6).
- Read the whole section of the plan for that step, plus every section it depends on, and the
  code it names. Read `CONTRIBUTING.md` once per session.
- **Too big for one run?** Points marked L, or anything touching more than about six files
  besides tests and docs, are split: insert sub-rows (`2.1a`, `2.1b`, …) right under the row,
  each a shippable slice that keeps the suite green, set the parent to `in Arbeit`, and do the
  first slice. Commit the table change together with that slice.
- Set the row to `in Arbeit` before you start editing code.

## 3. Implement

Follow the plan's design for the step. Where the plan and the code disagree (a function was
renamed, an earlier step changed a signature), follow the code and fix the plan's text in the
same commit. Where the plan leaves a real product decision open, or the step would break one of
these rules, stop and ask (see 6) instead of guessing:

- New signals never change a package's status silently; they add notes with a source.
- Nothing new is sent to PyPI beyond names and versions; anything that talks to another host
  (GitHub) is opt-in, documented in the README FAQ, and has a test proving no request happens
  without the flag.
- Tests never touch the network: extend `FakePyPI`/`RecordedPyPI` in `tests/conftest.py` (and
  `FakeGitHub`).
- JSON changes are additive; `schema_version` stays 1. Document new fields in the docstring of
  `render/json.py`.
- Every renderer (text, markdown, html, json) shows new information in the same order, through
  helpers in `render/__init__.py`. Markdown and HTML escape anything that comes from outside.
- The core keeps its two dependencies (`packaging`, `tomli` on 3.10). Anything else is an
  optional extra.
- Match the surrounding code: names, comment density, the short plain sentences of the
  user-facing text.

Each step also owns its docs: the README sections the plan names (options table, "How it
decides", FAQ, action inputs), `action.yml` when the plan adds an input, and an entry under
"## [Unreleased]" in `CHANGELOG.md`, and the wiki in `docs/wiki/`: update the hand-written
page the change belongs to, and run `PYTHONPATH=src python3 scripts/wiki.py` when an option,
the JSON report or `action.yml` changed (a test fails otherwise). Do not bump the version or tag a release: releases are the
user's call.

**Screenshots.** When a step changes something people see (a section of the text report,
the HTML report, `-i`, a new output), update the pictures: add the case to
`scripts/screenshots.py` if it is new, run it (`CHROMIUM=/opt/pw-browsers/chromium uv run
--with playwright --with textual python scripts/screenshots.py`, it needs the network), look
at every picture it writes, and show new ones where they help: the README for the main ones
(`docs/assets/`), the wiki page the change belongs to (`docs/wiki/images/`, as
`![what it shows](images/name.png)`, with alt text that says what the picture shows). A
test fails when a wiki image is missing or unused.

## 4. Verify

All of these must pass before committing; fix and re-run until they do:

```console
uv run --group dev pytest --cov=django_upgrade_report --cov-report=term-missing   # >= 90 %
uvx ruff check .
uvx ruff format --check .
```

- Add the tests the plan's "Tests" paragraph lists; at least one must fail without the change.
- Where the step touches lookups, assert the request count on the fake index
  (`FakePyPI.requests`) so PyPI load does not creep up.
- Golden tests in `tests/test_analysis.py` stay unchanged unless the step says the facts change
  (re-recording fixtures, step 0.2); then justify each changed assertion in the commit message.
  Re-recording needs network: if `tests/fixtures/record.py` cannot reach PyPI, mark the step
  blocked instead of editing fixtures by hand.
- Run the tool once for real on a sample project (`uv run python -m django_upgrade_report
  tests/data/...` or a temporary project in the scratchpad) in each format the step changed,
  and read the output as a user would.
- Self-review the diff: if the `code-review` skill is available, run it on the working tree and
  apply the findings you can confirm; then the `simplify` skill if available. Otherwise re-read
  `git diff` adversarially yourself. Re-run the checks above after any fix.

## 5. Finish the step

Commit only in the same `&&` chain as the checks, so a red check stops the commit:

```console
uvx ruff check . && uvx ruff format --check . && uv run --group dev pytest -q && git add -A && git commit ...
```

Never put a check before `;` or on a line of its own and the commit after it. A check piped
into `tail` or `grep` hides its failure from `&&`: start the chain with `set -o pipefail`, and
run the browser tests (`CHROMIUM=/opt/pw-browsers/chromium uv run --group dev --with playwright
pytest -m browser`) in it when the step touches the HTML report.

1. Set the row to `erledigt` in the same commit as the code; `git log` holds the commit.
2. Commit with a message in the repository's style ("Add pre-release hints to check and blocked
   rows", imperative, no prefix), body explaining what and why. Author and committer are
   Daniel Kurdoghlian <daniel@pushingpixels.at>, as in the rest of the history. No
   `Co-Authored-By`, session link or other trailer, whatever a session's default says.
3. Push the current branch (`git push -u origin <branch>`, retry on network errors only). Do
   not open a pull request unless the user asked for one.
4. Report in two or three sentences: which step, what changed for users, test count and
   coverage, and what is next.

## 6. When to stop the loop

When this skill runs under `/loop`, schedule the next iteration with a short delay (about 60
seconds) after a step finished cleanly. End the loop instead (ScheduleWakeup with
`stop: true`) and tell the user why, when:

- **Release boundary:** the step just finished was the last row of its release (the next row
  has a different "Release" value). Summarise the release (steps, CHANGELOG entries) so the
  user can review, release, or say "weiter".
- **Blocked:** a check stays red after three honest attempts, a fixture needs network that is
  not available, or a dependency is blocked. Set the row to `blockiert: <reason in one line>`,
  commit only work that is green, and leave everything else uncommitted with a note.
- **Decision needed:** the plan leaves a product choice open or a rule above would be broken.
  Ask one precise question with a recommended answer.
- **Done:** no row is `offen` or `in Arbeit`.

Never stop silently, and never start a second step in the same run.
