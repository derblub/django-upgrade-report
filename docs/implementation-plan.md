# Implementierungsplan: django-upgrade-report nach 0.4.0

Dieser Plan setzt alle 19 Ideen aus dem Brainstorming um. Er baut auf dem Stand von 0.4.0 auf
(`analysis.py`, `sources.py`, `pypi.py`, `render/*`, `action.yml`) und hält die Grundsätze des
Projekts ein:

- **Ehrlich statt optimistisch.** Neue Signale ändern nie stillschweigend einen Status. Ein
  schwacher Beleg macht aus „Check manually“ kein „Ready“, sondern eine begründete Notiz.
- **Dein Code bleibt, wo er ist.** Standardmäßig gehen nur Namen und Versionen von PyPI-Paketen
  an den Index. Alles, was andere Hosts fragt (GitHub), ist opt-in und steht im README.
- **Ohne Netz testbar.** Jede Funktion bekommt Tests gegen den Fake-Index aus
  `tests/conftest.py` oder gegen aufgezeichnete Fixtures, nie gegen das echte Netz.
- **JSON bleibt stabil.** Neue Felder lassen `schema_version` bei 1. Nur Umbenennen, Entfernen
  oder Umtypisieren erhöht die Version.
- **Ausgabe auf Englisch**, im Stil des bestehenden Reports: kurze Sätze, kein Jargon.

Jede Funktion hat dieselbe Gliederung: Ziel, Verhalten für Nutzer, Design, Randfälle, Ausgabe
(Text, Markdown, HTML, JSON), Tests, Doku, Aufwand und Abhängigkeiten. Am Ende stehen
Release-Reihenfolge, Risiken und eine Checkliste pro Pull Request.

---

## Fortschritt

Die Schleife `/loop /plan-step` (Skill in `.claude/skills/plan-step/`) arbeitet diese Tabelle
von oben nach unten ab und pflegt die Spalte „Status“: `offen`, `in Arbeit`,
`erledigt` oder `blockiert: <Grund>`. Ein zu großer Punkt wird in Unterschritte
(`2.1a`, `2.1b`, …) geteilt, die direkt unter ihm eingefügt werden.

| Schritt | Release | Punkt | Status |
| --- | --- | --- | --- |
| 0.1 | 0.5 | HTTP-Client herauslösen | erledigt |
| 0.2 | 0.5 | Cache-Format v2 und neue Metadaten | erledigt |
| 0.3 | 0.5 | Erweiterungen am Report-Modell | offen |
| 0.6 | 0.5 | Testinfrastruktur | offen |
| 1.1 | 0.5 | Pre-Releases | offen |
| 2.3 | 0.5 | Risiko pro Schritt und Changelog-Links | offen |
| 4.3 | 0.5 | pre-commit-Hook und `--offline` | offen |
| 0.5 | 0.6 | Verallgemeinerte Release-Suche | offen |
| 5.1 | 0.6 | `--explain` | offen |
| 7.3 | 0.6 | Fehlende Angaben nachfragen | offen |
| 2.1 | 0.7 | Python-Readiness | offen |
| 4.1 | 0.8 | Baseline-Diff | offen |
| 4.2 | 0.8 | Sticky PR-Kommentar | offen |
| 3.3 | 0.8 | Checkliste im HTML-Report und Tracking-Issue | offen |
| 7.1 | 0.8 | Interaktiver HTML-Report | offen |
| 0.4 | 0.9 | Direkte und transitive Abhängigkeiten | offen |
| 3.1 | 0.9 | Befehle ausgeben (`--emit`) | offen |
| 3.2 | 0.9 | Renovate- und Dependabot-Konfiguration | offen |
| 2.2 | 0.9 | Mehrstufiger Pfad (`--via`) | offen |
| 1.2 | 0.10 | Schwächere Belege | offen |
| 1.3 | 0.10 | Upstream-Issues und -PRs | offen |
| 7.2 | 0.10 | Terminal-Oberfläche (`--interactive`) | offen |
| 2.4 | 0.11 | Ungenutzte Pakete | offen |
| 2.5 | 0.11 | Was Django entfernt hat | offen |
| 6.1 | 1.0 | Wagtail und django CMS als Ziel | offen |
| 6.2 | 1.0 | Mehrere Projekte | offen |
| 6.3 | separat | Öffentliche Readiness-Daten | offen |

---

## Inhalt

- [Fortschritt](#fortschritt)
- [Phase 0: Fundament](#phase-0-fundament)
  - [0.1 HTTP-Client herauslösen](#01-http-client-herauslösen)
  - [0.2 Cache-Format v2 und neue Metadaten](#02-cache-format-v2-und-neue-metadaten)
  - [0.3 Erweiterungen am Report-Modell](#03-erweiterungen-am-report-modell)
  - [0.4 Direkte und transitive Abhängigkeiten](#04-direkte-und-transitive-abhängigkeiten)
  - [0.5 Verallgemeinerte Release-Suche](#05-verallgemeinerte-release-suche)
  - [0.6 Testinfrastruktur](#06-testinfrastruktur)
- [1. Weniger „Check manually“](#1-weniger-check-manually)
  - [1.1 Pre-Releases](#11-pre-releases)
  - [1.2 Schwächere Belege (README, Testmatrix, Changelog)](#12-schwächere-belege-readme-testmatrix-changelog)
  - [1.3 Upstream-Issues und -PRs](#13-upstream-issues-und--prs)
- [2. Bessere Planung](#2-bessere-planung)
  - [2.1 Python-Readiness](#21-python-readiness)
  - [2.2 Mehrstufiger Pfad (`--via`)](#22-mehrstufiger-pfad---via)
  - [2.3 Risiko pro Schritt und Changelog-Links](#23-risiko-pro-schritt-und-changelog-links)
  - [2.4 Ungenutzte Pakete](#24-ungenutzte-pakete)
  - [2.5 Was Django entfernt hat](#25-was-django-entfernt-hat)
- [3. Vom Report zur Umsetzung](#3-vom-report-zur-umsetzung)
  - [3.1 Befehle ausgeben (`--emit`)](#31-befehle-ausgeben---emit)
  - [3.2 Renovate- und Dependabot-Konfiguration](#32-renovate--und-dependabot-konfiguration)
  - [3.3 Checkliste im HTML-Report und Tracking-Issue](#33-checkliste-im-html-report-und-tracking-issue)
- [4. CI über längere Zeit](#4-ci-über-längere-zeit)
  - [4.1 Baseline-Diff](#41-baseline-diff)
  - [4.2 Sticky PR-Kommentar](#42-sticky-pr-kommentar)
  - [4.3 pre-commit-Hook und `--offline`](#43-pre-commit-hook-und---offline)
- [5. Vertrauen: `--explain`](#5-vertrauen---explain)
- [6. Größere Würfe](#6-größere-würfe)
  - [6.1 Wagtail und django CMS als Ziel](#61-wagtail-und-django-cms-als-ziel)
  - [6.2 Mehrere Projekte](#62-mehrere-projekte)
  - [6.3 Öffentliche Readiness-Daten](#63-öffentliche-readiness-daten)
- [7. Interaktiver Output](#7-interaktiver-output)
  - [7.1 Interaktiver HTML-Report](#71-interaktiver-html-report)
  - [7.2 Terminal-Oberfläche (`--interactive`)](#72-terminal-oberfläche---interactive)
  - [7.3 Fehlende Angaben nachfragen](#73-fehlende-angaben-nachfragen)
- [Release-Reihenfolge](#release-reihenfolge)
- [Risiken](#risiken)
- [Checkliste pro Pull Request](#checkliste-pro-pull-request)

---

## Phase 0: Fundament

Diese Schritte haben allein keinen sichtbaren Nutzen, mehrere spätere Funktionen brauchen sie
aber. Jeder Schritt ist ein eigener PR und ändert das Verhalten nicht. Die Golden-Tests in
`tests/test_analysis.py` müssen danach unverändert grün sein.

### 0.1 HTTP-Client herauslösen

**Wofür:** 1.2, 1.3, 3.3, 4.2 und 6.3 sprechen mit GitHub und brauchen dieselben Retries,
denselben Cache und dieselbe Schwärzung von Zugangsdaten wie `PyPI`.

**Design** (umgesetzt)

- Neues Modul `src/django_upgrade_report/client.py` (nicht `http.py`, um keine Verwechslung mit
  dem Standardmodul `http` zu riskieren) mit `class JsonClient`. Dorthin sind aus `pypi.py`
  gewandert: `_get`, `_fetch`, `_fetch_once`, `_request`, `_retry_after`, `_describe`,
  `_split_credentials`, `redact`, die Cache-Lese- und -Schreibfunktionen, `USER_AGENT`,
  `ATTEMPTS`, `MAX_RETRY_AFTER` und das Semaphor `MAX_CONNECTIONS` (eines pro Client, also pro
  Host).
- Konstruktor: `base_url`, `cache_dir`, `timeout`, `headers` (z. B.
  `Authorization: Bearer $GITHUB_TOKEN`) und `connections`. Was ein Client anders macht,
  überschreibt er als Methode: `_validate(data)` (lehnt Antworten ab, heute die PyPI-Prüfung),
  `_ttl(url)` (Sekunden oder `None` = für immer) und `_slim(data)` (was in den Cache kommt),
  dazu das Klassenattribut `hint` für Fehlermeldungen.
- `PyPI` erbt von `JsonClient`. So überschreiben `FakePyPI` und `RecordedPyPI` weiterhin
  `_fetch`, und die öffentliche API (`project()`, `release()`, `redact()`, `index_url`) bleibt
  gleich. Die TTL-Regel „Release-Metadaten für immer, Projektindex 24 h“ ist `PyPI._ttl`.
- HTTP 403 wegen eines Rate-Limits (GitHub: `X-RateLimit-Remaining: 0` oder, beim sekundären
  Limit, `Retry-After`) gilt wie 429: warten bis `Retry-After` bzw. `X-RateLimit-Reset`. Liegt
  das weiter als `MAX_RETRY_AFTER` entfernt, bricht die Anfrage sofort mit „rate limit exceeded“
  ab, statt viermal vergeblich zu warten. Ein Reset in der Vergangenheit fällt auf das normale
  Backoff zurück. Andere 403 werden wie bisher nicht wiederholt.
- Antworten aus dem Disk-Cache laufen durch `_validate`; ein unpassender Eintrag gilt als
  Cache-Fehlschlag. `null` ist nie eine gültige Antwort, weil `None` „nicht gefunden“ heißt.
- Bekannte Grenze für 1.2: Der Cache-Schlüssel ist nur die URL, nicht der
  `Authorization`-Header. Mit `GITHUB_TOKEN` nur Antworten zu öffentlichen Repositories cachen.
- `FetchError` ersetzt `PyPIError`; `pypi.PyPIError` bleibt als Alias für bestehende Aufrufer.
- Einen `TextClient` für Rohdateien (`tox.ini`) bringt erst Schritt 1.2, wenn er gebraucht wird.

**Tests:** `tests/test_pypi.py` bleibt grün. Neu `tests/test_client.py`: Retry auf 503, Abbruch
auf 404, Rate-Limit-Header, Schwärzung in Fehlermeldungen, TTL `None` vs. Sekunden.

**Aufwand:** S–M.

### 0.2 Cache-Format v2 und neue Metadaten

**Wofür:** 1.1 (Pre-Releases mit Upload-Datum, schon vorhanden), 1.2 (README-Erwähnungen),
2.1 (Wheel-Tags und `requires_python` pro Release), 2.3 und 1.3 (`project_urls`).

Heute behält `_slim()` nur `name`, `version`, `classifiers`, `requires_dist` und
`requires_python`, pro Release nur Upload-Zeit und Yanked. Release-Metadaten werden für immer
gecacht. Ein erweitertes `_slim` würde daher bei alten Cache-Einträgen stillschweigend Felder
vermissen.

**Design** (umgesetzt)

- `JsonClient.cache_format` ist Teil jedes Cache-Schlüssels (`sha256(f"{cache_format} {url}")`),
  `PyPI.cache_format = "v2"`. Alte Einträge werden ignoriert und verwaisen; das README sagt,
  dass man das Cache-Verzeichnis ab und zu löschen kann.
- `_slim` läuft jetzt einmal pro geholter Antwort, **vor** dem Speicher- und Disk-Cache. So
  liefert ein kalter Cache dieselben Daten wie ein warmer, und die README-Beschreibung landet
  nie im Speicher.
- `_slim_project` behält zusätzlich:
  - `info.project_urls` und `info.home_page`; beim Lesen gelten nur `http(s)`-Adressen, das
    `UNKNOWN` alter setuptools-Versionen fällt weg,
  - `info.django_mentions`: Django-Feature-Versionen aus `info.description`, die als
    unterstützt genannt werden („Django 5.2“, „Django >= 4.2“, „Django~=5.0“, „Django: 5.1“,
    „Django version 4.2“), nicht aber „Django<5.0“, „Django!=4.1“ oder „python-django 3.2“.
    Nur Major 1–9, sortiert und eindeutig. Die Beschreibung selbst wird nicht gespeichert.
  - aus `urls` (Dateien dieses bzw. des neuesten Releases): `info.wheel_tags` und
    `info.has_sdist`. Fehlt `urls` (manche Mirrors), ist `has_sdist` `None`, also unbekannt,
    nicht „kein Sdist“.
  - pro Release im Stellvertreter-Eintrag zusätzlich `requires_python`, `wheel_tags` und
    `has_sdist`, jeweils nur, wenn nicht leer (botocore hat tausende Releases). So kann 2.1
    „nur Sdist“ von „nur Wheels für andere Plattformen“ unterscheiden.
- `wheel_tags` enthält nur Tags, die unter Linux installierbar sind (Plattform `any` oder
  `*linux*`), weil die Analyse für Linux urteilt. numpy hat sonst rund 50 Tags pro Release.
  Gemessen: numpy 14 → 102 KB, botocore 215 → 353 KB im Cache; das Verschlanken der
  numpy-Antwort (4 232 Dateien) dauert rund 20 ms. Der Filter steckt bewusst im
  Speicherformat: Wer später Wheels anderer Plattformen braucht, erhöht `cache_format`.
- `ReleaseInfo` hat neu `project_urls: tuple[tuple[str, str], ...]`, `home_page`,
  `django_mentions`, `wheel_tags`, `has_sdist: bool | None`; `Release` hat neu
  `requires_python`, `wheel_tags` und `has_sdist`. `PyPI.project()` liest den
  Stellvertreter-Eintrag direkt, weil `_slim` immer vorher läuft. Alle mit Default, `sources._metadata` und die Tests bleiben unverändert.
- Wheel-Dateinamen parst `packaging.utils.parse_wheel_filename`; ungültige werden übersprungen.
- **Fixtures nicht neu aufgezeichnet:** Kein bestehender Golden-Test liest die neuen Felder,
  eine Neuaufzeichnung würde nur die Fakten der Tests verschieben. `tests/fixtures/record.py`
  nimmt die neuen Felder erst in dem Schritt auf, der sie in Golden-Tests braucht (2.1 für
  Wheel-Tags, 2.3 für `project_urls`), und zeichnet dann nur die neu benötigten Pakete auf.

**Tests:** Cache-Roundtrip mit allen neuen Feldern; alter Cache-Eintrag (ohne Präfix) wird
nicht gelesen; `django_mentions` aus einer Beispielbeschreibung; Wheel-Tags aus gemischten
Dateilisten; kaputte Werte (keine Strings) werden ignoriert, wie heute in `_strings`.

**Aufwand:** M (wegen Neuaufzeichnung).

### 0.3 Erweiterungen am Report-Modell

**Wofür:** Fast alle Funktionen hängen strukturierte Daten an Pakete. Heute gibt es nur
`notes: list[str]`, was für Diff, `--emit` und `--explain` nicht reicht.

**Design**

- In `analysis.py`:
  ```python
  SEVERITY = {Status.READY: 0, Status.CHECK: 1, Status.UPGRADE: 2, Status.BLOCKED: 3}
  ```
  Die Reihenfolge in `analyse()` (`order = {...}`) und `_FAIL_ON` in `cli.py` werden daraus
  abgeleitet, damit 4.1 „besser/schlechter“ konsistent bestimmt.
- `PackageReport` bekommt optionale Felder (alle mit Default `None` oder leerer Liste), die
  einzelne Funktionen füllen:
  ```python
  prerelease: PreRelease | None = None  # 1.1
  evidence: list[Evidence] = field(...)  # 1.2
  upstream: list[UpstreamItem] = field(...)  # 1.3
  majors_crossed: int | None = None  # 2.3
  changelog_url: str | None = None  # 2.3
  repository_url: str | None = None  # 2.3, 1.2, 1.3
  usage: Usage | None = None  # 2.4
  direct: bool | None = None  # 0.4
  ```
- `Report` bekommt:
  ```python
  kind: str = "report"  # "report"; Pfad und Multi siehe 2.2, 6.2
  framework: str = "django"  # 6.1
  python_plan: PythonPlan | None = None  # 2.1
  removals: list[Removal] = field(...)  # 2.5
  changes: list[Change] | None = None  # 4.1
  ```
- `render/json.py` gibt jedes neue Feld aus, auch wenn es leer ist (`null` oder `[]`), damit
  Skripte nicht raten müssen. Die Feldliste im Docstring wird ergänzt.
- Alle Renderer lesen neue Felder nur über Helfer in `render/__init__.py`, damit die vier
  Formate dieselbe Reihenfolge behalten (das ist heute schon die Regel: „Every renderer walks
  the same sections in the same order“).

**Tests:** `tests/test_cli.py` prüft, dass JSON alle neuen Schlüssel enthält und
`schema_version` 1 bleibt.

**Aufwand:** S.

### 0.4 Direkte und transitive Abhängigkeiten

**Wofür:** 2.4 (nur direkte Abhängigkeiten können „ungenutzt“ sein), 3.1 (für direkte
Abhängigkeiten `uv add`, für transitive `uv lock --upgrade-package`), 3.2.

**Design:** `Dependency.direct: bool | None = None` (`None` = unbekannt). Ermittlung pro Quelle:

| Quelle | Wie |
| --- | --- |
| `uv.lock` | Paket mit `source = { virtual = "." }` oder `editable = "."` ist das Projekt; dessen `dependencies` und `dev-dependencies` sind direkt. Workspaces: alle Workspace-Mitglieder als Wurzeln. |
| `poetry.lock` | Kein Wurzeleintrag: `pyproject.toml` im selben Verzeichnis lesen (`[tool.poetry.dependencies]`, Gruppen, `[project.dependencies]`). |
| `pdm.lock` | `pyproject.toml` lesen (`[project]`, `[tool.pdm.dev-dependencies]`, `[dependency-groups]`). |
| `Pipfile.lock` | `Pipfile` daneben lesen (`[packages]`, `[dev-packages]`); fehlt es, `None`. |
| `requirements*.txt` | Alle Zeilen sind direkt. Über `-c` eingebundene Constraints sind nicht direkt. |
| `pyproject.toml` | Alle direkt. |
| `--python` | `importlib.metadata` kennt keine Wurzeln: `None`. Mit `uv.lock` oder `pyproject.toml` im Projekt diese zur Markierung heranziehen (Abgleich nur über den Namen). |

`_combine()` übernimmt `direct=True`, sobald eine Quelle das Paket direkt nennt.

**Tests:** je eine Fixture pro Lockfile-Format mit einer direkten und einer transitiven
Abhängigkeit; Workspace-Fall für uv.

**Aufwand:** M.

### 0.5 Verallgemeinerte Release-Suche

**Wofür:** 2.1 (Suche nach dem ersten Release, das Python X.Y unterstützt) und 6.1 (Suche
gegen Wagtail statt Django).

Heute sind `_Checker.find`, `lowest_yes` und `search` fest an `supports(info, self.target, …)`
und `excluded_side(info, self.target)` gebunden.

**Design**

- Ein Protokoll `Rule` in `analysis.py`:
  ```python
  class Rule(Protocol):
      def judge(self, info: ReleaseInfo, uploaded: datetime | None) -> Support: ...
      def side(self, info: ReleaseInfo) -> int: ...  # wie excluded_side
  ```
- `DjangoRule(target)` kapselt die heutige Logik (`supports`, `excluded_side`). `find`,
  `lowest_yes` und `search` bekommen `rule: Rule` als Parameter, Default
  `DjangoRule(self.target)`.
- Rein mechanischer Umbau ohne Verhaltensänderung. Die Golden-Tests und
  `FakePyPI.requests` (Anzahl der Anfragen) müssen identisch bleiben; ein Test vergleicht die
  Anfrageliste vor und nach dem Umbau für die Saleor-artigen Fixtures.

**Aufwand:** S–M.

### 0.6 Testinfrastruktur

- `conftest.release()` bekommt optionale Parameter `files=[...]` (Wheel-Dateinamen),
  `project_urls={...}`, `description="..."`, `requires_python=...`. `FakePyPI._fetch` gibt sie
  in derselben Form zurück wie PyPI (`releases[v][i].filename`, `urls`).
- `FakeGitHub(JsonClient)` mit einem dict von URL zu Antwort für 1.2, 1.3, 3.3, 4.2, und eine
  Liste der gestellten Anfragen, damit Tests sicherstellen, dass ohne Opt-in **keine**
  GitHub-Anfrage passiert.
- Ein pytest-Marker `network`, der in CI nie läuft, für manuelles Aufzeichnen.

---

## 1. Weniger „Check manually“

### 1.1 Pre-Releases

**Ziel:** Wenn kein stabiles Release das Ziel deklariert, ein Pre-Release es aber tut, soll der
Report das sagen. Der Status ändert sich nicht.

**Verhalten**

```
Check manually (3)
  ? django-lagging   1.0   declares Django up to 4.2
                           2.0rc1 declares Django 5.2 (pre-release, 2026-03-02)
```

**Design**

- In `_Package` neue Methode `_prerelease()`, aufgerufen am Ende von `judge()`, nur wenn
  `report.status in (CHECK, BLOCKED)` und `report.successor is None`.
- Kandidaten: `project.releases` mit `version.is_prerelease`, `has_files`, nicht yanked, und
  `version > max(self.stable)`. Ältere Pre-Releases sind überholt.
- Nur den **neuesten** Kandidaten holen (`pypi.release(name, str(v))`), also höchstens eine
  zusätzliche Anfrage pro Paket in CHECK oder BLOCKED.
- `supports(info, self.target, uploaded)` nach denselben Regeln wie für stabile Releases
  (Obergrenzen zählen nur nach dem GA-Datum; bei unveröffentlichtem Ziel nur Classifier).
- Ergebnis:
  - `YES` → `report.prerelease = PreRelease(version, reason, uploaded)` und Notiz
    `"{v} {reason} (pre-release)"`.
  - Bei BLOCKED auch `LIKELY` (nicht `NO`) → Notiz `"{v} no longer excludes Django X.Y
    (pre-release)"`, weil das für Blocker die wichtigste Nachricht ist.
  - `NO` oder `UNKNOWN` → nichts.
- Das gilt nicht für lokal beurteilte Pakete (`_judge_local`), weil es dort keine Releases gibt.

**Ausgabe:** In allen Formaten als Notiz. JSON zusätzlich
`"prerelease": {"version": "2.0rc1", "reason": "...", "uploaded": "…"}` oder `null`.

**Randfälle:** Pre-Release ohne Dateien; Pre-Release, das neuer ist als ein yanked Stable; nur
`.dev`-Releases (zählen als Pre-Release, `packaging` behandelt sie so); Pre-Release mit anderem
Django-Requirement auf anderem Python (Marker-Auswertung wie gehabt).

**Tests:** fünf Fälle im Fake-Index: rc deklariert das Ziel (CHECK + Notiz), rc für ein
blockiertes Paket hebt die Grenze, alter Beta-Release vor dem neuesten Stable wird ignoriert,
rc ohne Dateien wird ignoriert, Anfragezähler beweist genau einen zusätzlichen Request.

**Doku:** README, Abschnitt „How it decides“, neuer Absatz „Pre-releases“. CHANGELOG.

**Aufwand:** S. **Abhängigkeiten:** keine.

### 1.2 Schwächere Belege (README, Testmatrix, Changelog)

**Ziel:** „Check manually“ in zwei Gruppen teilen: mit Belegen („likely fine“) und ohne.
Status und `--fail-on` bleiben gleich.

**Verhalten**

```
Check manually (5)
  The metadata does not say either way. Read the changelog or run the test suite.
  ? django-lagging   1.0   declares Django up to 4.2
                           README mentions Django 5.2 · main branch tests Django 5.2 (tox.ini)
```

Im HTML und Markdown erscheinen Belege als eigene Chips oder Spalte mit Link zur Quelle.

**Drei Belegquellen, gestuft nach Kosten:**

1. **README auf PyPI (immer an, kostet nichts):** `ReleaseInfo.django_mentions` aus 0.2. Nur
   das Release, das der Report vorschlägt (`target_version`), sonst das neueste. Beleg, wenn
   die Zielversion darin vorkommt. Formulierung: `"README of 2.1 mentions Django 5.2"`.
2. **Testmatrix im Repository (opt-in `--evidence`):** Rohdateien vom Standard-Branch.
3. **Changelog im Repository (opt-in `--evidence`):** Rohdatei vom Standard-Branch.

**Repository finden** (gemeinsam mit 1.3 und 2.3, Funktion `repository_url(info)` in neuem
Modul `projects.py`):

- `project_urls` mit Schlüsseln (case-insensitiv, normalisiert ohne Leer- und Sonderzeichen)
  `source`, `sourcecode`, `repository`, `code`, `github`, `homepage`, dann `home_page`.
- Akzeptiert werden nur `https://github.com/{owner}/{repo}` (GitLab und Codeberg als spätere
  Erweiterung, gleiche Schnittstelle). `.git`, Unterpfade (`/tree/main`) und `www.` werden
  entfernt.
- Kein Repository → keine Online-Belege, kein Fehler.

**Testmatrix lesen** (`evidence.py`):

- Abruf über `https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{path}` (kein API-Limit,
  `HEAD` löst den Standard-Branch auf). Feste Pfadliste, höchstens acht Anfragen pro Paket:
  `tox.ini`, `noxfile.py`, `pyproject.toml` (für `[tool.tox]`), `.github/workflows/test.yml`,
  `tests.yml`, `ci.yml`, `main.yml`, `python-package.yml`. 404 ist normal und wird gecacht.
- Mit `GITHUB_TOKEN` zusätzlich die API `GET /repos/{o}/{r}/contents/.github/workflows`, um alle
  Workflow-Dateien zu finden statt zu raten.
- Parser, jeweils konservativ (lieber nichts finden als falsch):
  - tox `envlist`: Klammern expandieren (`py{310,312}-django{42,52}` → Produkte), dann
    `dj(?:ango)?[-_]?(\d)(\d+)` → `X.Y`. Auch `[gh-actions:env]` und `DJANGO=`-Faktoren.
  - GitHub-Workflow: Werte unter `matrix:` mit Schlüsseln, die `django` enthalten
    (`django-version`, `django`). Listen in Fluss- (`["4.2", "5.2"]`) und Blockform. Ohne
    YAML-Abhängigkeit: zeilenbasierter Parser für genau diese zwei Formen. Alles andere wird
    ignoriert.
  - nox: `@nox.parametrize("django", [...])` per AST und Strings `"X.Y"` darin.
  - `main` oder `stable/X.Y` gelten nicht als Version.
- Beleg: `"main branch tests Django 5.2 (tox.ini)"` mit URL. Der Hinweis „main branch“ ist
  wichtig: Das ist eine Aussage über unveröffentlichten Code, nicht über das Release.

**Changelog lesen:**

- Pfade `CHANGELOG.md`, `CHANGES.rst`, `CHANGES.md`, `HISTORY.rst`, `HISTORY.md`,
  `docs/changelog.rst`, `docs/changes.rst`, `NEWS.rst`, plus `project_urls["Changelog"]`, wenn
  diese auf `github.com/{o}/{r}/blob/...` zeigt (wird in Raw-URL umgeschrieben).
- Suche nach Zeilen, die `Django X.Y` und eines von `support`, `compatib`, `add`, `test`
  enthalten. Treffer nur, wenn die Zeile im Abschnitt einer Version **nach** der installierten
  steht (Abschnittsköpfe per Regex für Versionsnummern erkennen). Ohne Abschnittserkennung kein
  Beleg.
- Beleg: `"changelog of 2.2 mentions Django 5.2 support"`.

**Datenmodell**

```python
@dataclass(frozen=True)
class Evidence:
    kind: Literal["readme", "test-matrix", "changelog"]
    text: str
    url: str | None
```

**Sortierung:** Innerhalb von „Check manually“ kommen Pakete **ohne** Beleg zuerst, weil dort
die meiste Arbeit liegt. Der Section-Hint wird ergänzt: `"N of them have signs of support,
see their notes."`

**Datenschutz:** `--evidence` sendet Owner und Repo öffentlicher PyPI-Pakete an GitHub, nie
Pakete mit `source` (Forks, privat) oder aus einem privaten Index. README-FAQ „Does it send my
code anywhere?“ ergänzen. In der Action Input `evidence: false` als Default.

**Kosten:** Nur für Pakete in CHECK (meist 5–30). Cache 24 h. Parallel über den eigenen
GitHub-Client mit höchstens vier gleichzeitigen Anfragen.

**Tests:** Parser-Unit-Tests mit echten Ausschnitten (tox von django-filter, Workflow von
django-allauth, nox von wagtail) als Textfixtures in `tests/data/evidence/`. Integrationstest
mit `FakeGitHub`: Ohne `--evidence` null GitHub-Anfragen, mit `--evidence` erwartete URLs.
Repo-URL-Normalisierung (zehn Varianten).

**Doku:** README: neuer Abschnitt „Signs of support“, Option `--evidence`, FAQ. CHANGELOG.

**Aufwand:** M–L. **Abhängigkeiten:** 0.1, 0.2, 0.3, 0.6.

### 1.3 Upstream-Issues und -PRs

**Ziel:** Bei BLOCKED und CHECK sehen, ob upstream schon jemand an der Unterstützung
arbeitet.

**Verhalten**

```
Blocked (1)
  ✗ django-taggit   4.0.0   latest 5.0.1 requires Django<5.2
                            open PR: Add Django 5.2 support (#912)
```

**Design**

- Nur mit `--evidence` und nur für Pakete mit GitHub-Repository (1.2).
- Anfrage: `GET https://api.github.com/search/issues?q=repo:{o}/{r}+"Django {X.Y}"+in:title&sort=updated&per_page=5`.
- Die Such-API ist streng limitiert (10/min ohne Token, 30/min mit). Deshalb:
  - Nur BLOCKED und CHECK ohne Beleg aus 1.2, sortiert nach BLOCKED zuerst.
  - Höchstens 10 Suchen pro Lauf ohne Token, 30 mit; danach ein Hinweis in `notices`:
    `"Searched upstream issues for 10 of 14 packages: set GITHUB_TOKEN for more"`.
  - Cache 6 h.
- Auswahl: offene vor geschlossenen, PR vor Issue, neuestes zuerst; höchstens zwei Einträge.
  Geschlossen-und-gemergte PRs heißen `"merged PR: …"`, das ist ein starker Hinweis, dass das
  nächste Release kommt.
- Titel werden auf 80 Zeichen gekürzt und in jedem Format escaped (Markdown: `escape()`, HTML:
  `html.escape`), weil sie Fremdinhalt sind.

**Datenmodell:**
`UpstreamItem(kind: "issue" | "pr", state: "open" | "closed" | "merged", title, url, number, updated)`.

**Tests:** `FakeGitHub` mit Suchantwort; Limit und Hinweis; Escaping eines Titels mit
`<script>` und `|`.

**Aufwand:** S–M. **Abhängigkeiten:** 1.2 (Repository-Ermittlung, Opt-in-Flag), 0.1.

---

## 2. Bessere Planung

### 2.1 Python-Readiness

**Ziel:** Wenn das Ziel-Django ein neueres Python braucht als das Projekt, zeigen, welche
Abhängigkeiten (alle, nicht nur Django-bezogene) auf dem neuen Python laufen, welche ein
Upgrade brauchen und welche blockieren.

**Verhalten**

```
Django 4.2.7 → 6.1
from uv.lock · 38 Django-related packages · Python 3.10
! Django 6.1 needs Python >=3.12, your project uses 3.10 (from .python-version)

Python 3.12 first (4)
  Switch Python before Django. These need a newer release on Python 3.12.
  ↑ psycopg2-binary   2.9.3 → 2.9.9   no wheel for Python 3.12 in 2.9.3
  ↑ numpy             1.22.4 → 1.26.0 requires Python <3.12 in 1.22.4
  ✗ some-old-lib      0.4             every release requires Python <3.11
  ? pycrypto          2.6.1           no wheel for Python 3.12, pip builds it from source
  31 more packages declare Python 3.12 or are pure Python.
Django 4.2.7 runs on Python 3.12 from 4.2.8: update Django 4.2 first.
```

**Python-Ziel bestimmen:** Neue Option `--python-target {auto,none,X.Y}`.

- `auto` (Default): `_min_python(goal.requires_python)`, wenn das höher ist als die
  Projekt-Python. Sonst keine Python-Analyse.
- `X.Y`: immer, auch ohne Django-Wechsel (Health-Check plus Python-Upgrade, z. B.
  `--target 5.2 --python-target 3.13`).
- `none`: aus.
- Ist die Projekt-Python unbekannt, gilt `auto` als `none` mit Hinweis in `notices`.

**Urteil pro Release für Python P** (neue `PythonRule` aus 0.5, Funktion
`python_supports(info_or_release, P)`):

1. `requires_python` schließt P aus → `NO`, `"requires Python <3.12"`.
2. Classifier `Programming Language :: Python :: P` → `YES`.
3. Ein Wheel, das auf CPython P unter Linux x86_64 installierbar ist (Interpreter `cpPP`, oder
   `abi3` mit `cpNN ≤ P`, Plattform `manylinux*`/`musllinux*`/`any`) → `YES`, wenn es ein
   `cpPP`-Wheel ist, sonst `LIKELY` (`abi3` oder pure).
4. Nur plattformspezifische Wheels für andere Pythons, kein passendes, Sdist vorhanden →
   `LIKELY` mit Grund `"no wheel for Python P, pip builds it from source"`. Das ist der
   häufigste echte Schmerz (C-Erweiterungen), deshalb immer sichtbar.
5. Nur reines Python (`py3-none-any`) und `requires_python` lässt P zu → `LIKELY`,
   `"pure Python"`.
6. Sonst `UNKNOWN`.

**Status pro Paket** (wie bei Django):

- READY bei `YES` oder pure-`LIKELY` (Fall 5). Begründung im Text: Pure-Python-Pakete brechen
  fast nie an einer neuen Python-Version, sie werden aber nur als Summenzeile gezählt, nicht als
  „declared“ ausgegeben.
- UPGRADE, wenn das installierte Release nicht READY ist und ein neueres `YES` ist (oder
  `LIKELY` Fall 3). Suche mit `lowest_yes` über die `Release`-Objekte des Projekt-JSON. Wegen 0.2
  steckt `requires_python` und `wheel_tags` darin, also **keine** zusätzlichen Release-Anfragen.
  Classifier stehen nur im Release-JSON, also für Kandidaten aus Fall 2 holen.
- CHECK bei Fall 4 oder `UNKNOWN`.
- BLOCKED, wenn jedes neuere Release `NO` ist.

**Last auf PyPI:** Heute holt das Tool für nicht Django-bezogene Pakete nur das Release-JSON
(siehe 0.2.3 im CHANGELOG). Das bleibt so: Das Release-JSON enthält `urls`, Classifier und
`requires_python`. Das Projekt-JSON wird nur für Pakete geholt, deren installiertes Release
nicht READY ist (typisch 2–10).

**Phase gegenüber der Python-Umstellung:** Das vorgeschlagene Release muss auch auf der
aktuellen Python laufen (`python_supports(…, project_python)` nicht `NO`). Sonst Notiz
`"goes together with the switch to Python 3.12"`.

**Django selbst:** Prüfen, ob die installierte Django-Patchversion P deklariert (Classifier im
Release-JSON von Django). Wenn nicht, die erste Patchversion der aktuellen Serie suchen, die es
tut: `"Django 4.2.7 does not declare Python 3.12, 4.2.8 does: update Django 4.2 first"`. Das ist
für 4.2 → 3.12 wirklich so.

**Zusammenspiel mit dem Django-Plan:** Ein vorgeschlagenes Django-Upgrade (`target_version`),
das P nicht unterstützt, bekommt die Notiz `"2.0 does not declare Python 3.12"`.

**Datenmodell**

```python
@dataclass
class PythonPlan:
    target: str  # "3.12"
    current: str | None  # "3.10"
    packages: list[PackageReport]  # nur nicht-READY, gleiche Klasse, eigene Statusbedeutung
    ready: int  # Summenzeile
    pure: int
    django_note: str | None
```

**Ausgabe:** Neuer Abschnitt ganz oben nach den Warnungen in allen Formaten. JSON
`"python": {"target", "current", "packages": [...], "ready", "pure", "django_note"}` oder
`null`. `--fail-on` zählt Python-Blocker mit, wenn eine neue Option `--fail-on-python`
gesetzt ist. Default aus, damit sich bestehende CI-Läufe nicht ändern.

**Action:** Input `python-target` (Default `auto`), Output `python-blocked`.

**Randfälle:** Wheels nur für macOS und Windows (Linux zählt); PyPy-Wheels ignorieren;
`requires_python` mit ungültiger Syntax → wie fehlend; abi3 mit `cp36`; Projekt-Python schon ≥
Ziel → keine Analyse; Pakete aus Git (`source`) → nur Metadaten, keine Wheels, Fall 6 oder 1.

**Tests:** Unit-Tests für `python_supports` (jeder der sechs Fälle), Wheel-Tag-Matrix,
Integration mit Fake-Index (psycopg2-Muster, numpy-Muster, pure-Paket, blockiertes Paket),
Anfragezähler (keine Projekt-JSON für READY-Pakete), Golden-Test mit aufgezeichneten
Fixtures für `psycopg2-binary` und `numpy` (in `record.py` aufnehmen).

**Doku:** README: neuer Abschnitt „Upgrading Python too“, Optionstabelle, „How it decides“.
CHANGELOG.

**Aufwand:** L. **Abhängigkeiten:** 0.2, 0.3, 0.5.

### 2.2 Mehrstufiger Pfad (`--via`)

**Ziel:** Statt nur zu warnen, dass ein LTS übersprungen wird, einen Plan pro Schritt zeigen.

**Verhalten**

```console
django-upgrade-report --target 6.1 --via lts
```

```
Django 3.2.25 → 4.2 → 5.2 → 6.1  (3 steps)

Step 1: Django 3.2.25 → 4.2
  …voller Report…
Step 2: Django 4.2 → 5.2  (after step 1)
  …
```

**Optionen:** `--via {lts,each}`. `lts` nimmt alle x.2-Serien zwischen aktuell und Ziel als
Zwischenstationen, `each` jede Feature-Version. Ohne `--via` bleibt alles wie heute.

**Design**

- Neue Funktion `analyse_path(deps, pypi, target, via, …) -> PathReport` in `analysis.py`.
- Stationen: `resolve_target()` für das Ziel, dann `_series(django)` filtern.
- Schritt n:
  1. `analyse(deps_n, …, target=station_n, current=str(neueste Patchversion von station_{n-1}))`.
  2. `deps_{n+1}` = `deps_n` mit ersetzten Versionen: Für jedes Paket mit `status=UPGRADE` die
     `target_version`, für Django die neueste Patchversion von `station_n`. BLOCKED und CHECK
     bleiben unverändert.
  3. Ein Paket, das in einem früheren Schritt BLOCKED war, bekommt in späteren Schritten die
     Notiz `"blocked since step 1"`, damit klar ist, dass der Plan dort hängt.
- Der gemeinsame `PyPI`-Client (In-Memory-Cache) sorgt dafür, dass Schritt 2 und 3 kaum neue
  Anfragen kosten.
- Die Python-Analyse (2.1) läuft pro Schritt mit der jeweiligen Mindestversion.
- `--fail-on` gilt für alle Schritte (schlimmster Status).

**Datenmodell:** `PathReport(steps: list[Report], blocked_at: int | None)`.

**Ausgabe**

- Text und Markdown: Kopfzeile mit Pfad, dann jeder Schritt als eigener Block. In Markdown
  jeder Schritt als `<details>`, der erste offen.
- HTML: Tabs oder Anker pro Schritt, oben eine Übersichtstabelle (Schritt × Zählungen).
- JSON: `{"schema_version": 1, "kind": "path", "tool", "generated", "steps": [<report>…]}`. Jeder
  Schritt ist ein vollständiges Report-Objekt mit `"kind": "report"`. Das Feld `kind` wird in
  0.3 auch dem normalen Report hinzugefügt, damit Leser unterscheiden können.

**Action:** Input `via` (leer = aus). Die Zähl-Outputs beziehen sich auf alle Schritte
zusammen, `report` zeigt auf das Pfad-JSON.

**Randfälle:** Ziel ist selbst die nächste Station (ein Schritt, dann wie ohne `--via`);
Django unbekannt (`--from` nötig, sonst Fehler mit Hinweis); `--via` mit Health-Check
(Fehler); Ziel unveröffentlicht (letzter Schritt nur Classifier, wie heute).

**Tests:** Pfad 4.2 → 5.2 → 6.0 im Fake-Index; ein Paket, das in Schritt 1 ein Upgrade
bekommt und in Schritt 2 READY ist; ein BLOCKED-Paket über zwei Schritte; JSON-Form.

**Doku:** README „Choosing the target“ ergänzen; Warnung „This skips … LTS“ verweist auf
`--via lts`.

**Aufwand:** M. **Abhängigkeiten:** 0.3.

### 2.3 Risiko pro Schritt und Changelog-Links

**Ziel:** Bei jedem Upgrade zeigen, wie groß der Sprung ist, und direkt zum Changelog verlinken.

**Verhalten**

```
Upgrade first (4)
  ↑ django-allauth   0.54.0 → 65.0.0   crosses 2 major versions · changelog
```

**Design**

- `majors_crossed(current, target) -> int | None` in `analysis.py`:
  - Calendar Versioning erkennen (Major ≥ 2000 oder Major ≥ 20 bei Jahreszahl-Mustern wie
    `24.1`): `None` und Notiz `"calendar versions, read the changelog"` nur, wenn sich das Jahr
    ändert.
  - `0.x`: Minor-Sprünge zählen als Major (SemVer-Regel für 0.x).
  - Sonst `target.major - current.major`.
- Notiz ab ≥ 1: `"crosses N major versions"` (bzw. `"a major version"`).
- `changelog_url(info)` in `projects.py`: `project_urls`-Schlüssel normalisiert
  `changelog`, `changes`, `releasenotes`, `history`, `whatsnew`, `news`; sonst
  `https://github.com/{o}/{r}/releases` bei GitHub-Repo; sonst `None`.
- Daten kommen aus dem Projekt-JSON (0.2), also keine zusätzlichen Anfragen.

**Ausgabe:** Text: Notiz, der Link nur mit `-v` (URLs machen die Zeile zu lang). Markdown und
HTML: `changelog` als Link. JSON: `majors_crossed`, `changelog_url`, `repository_url`.

**Tests:** Tabelle mit 15 Versionspaaren (SemVer, 0.x, CalVer, Post-Releases, Epochs),
URL-Auswahl.

**Aufwand:** S. **Abhängigkeiten:** 0.2, 0.3.

### 2.4 Ungenutzte Pakete

**Ziel:** Direkte Abhängigkeiten finden, die der Code nicht nutzt, und vorschlagen, sie zu
entfernen statt zu aktualisieren. Das spart oft die meiste Arbeit.

**Verhalten**

```
  ↑ django-extensions   3.1.0 → 3.2.3   not imported or configured in your code: remove it instead?
```

**Design:** Neues Modul `usage.py`, nur lokal, ohne Projektimport.

1. **Dateien:** alle `*.py` unter dem Projektverzeichnis, ohne `.git`, `.venv`, `venv`, `env`,
   `node_modules`, `.tox`, `.nox`, `build`, `dist`, `site-packages`, `__pycache__` und
   Verzeichnisse mit `pyvenv.cfg`. Obergrenze 20 000 Dateien und 50 MB, danach Abbruch mit
   Hinweis (dann keine Aussage).
2. **Gesammelte Namen** per `ast`:
   - `import x.y`, `from x.y import z` → Top-Level `x`.
   - Alle String-Konstanten, die wie gepunktete Pfade aussehen (`^[a-z_][\w]*(\.[\w]+)+$`) oder
     ein einzelner Bezeichner in einer Liste namens `INSTALLED_APPS` sind. Damit sind
     `INSTALLED_APPS`, `MIDDLEWARE`, `AUTHENTICATION_BACKENDS`, `TEMPLATES`, `STORAGES`,
     `DATABASES["ENGINE"]`, `REST_FRAMEWORK` und Celery-Konfigurationen abgedeckt, ohne sie
     einzeln zu kennen.
   - Zusätzlich `*.html`/`*.txt` unter `templates/` auf `{% load x %}` → Template-Tag-Libraries
     (z. B. `crispy_forms_tags`), abgebildet über eine kleine Tabelle auf Pakete.
   - `manage.py`-Befehle in `Makefile`, `Procfile`, `*.sh`, `Dockerfile`,
     `.github/workflows/*.yml` (`manage.py (\w+)`), zugeordnet über bekannte Befehle
     (z. B. `runserver_plus` → django-extensions).
3. **Paket → Modulnamen:**
   - Mit `--python`: exakt über `importlib.metadata.packages_distributions()` im
     Ziel-Interpreter (Skript wie `_LIST_DISTRIBUTIONS` in `sources.py`).
   - Sonst: eingebaute Tabelle `usage_names.py` für bekannte Abweichungen
     (`djangorestframework` → `rest_framework`, `django-filter` → `django_filters`,
     `django-crispy-forms` → `crispy_forms`, `pillow` → `PIL`, `psycopg2-binary` → `psycopg2`,
     `django-environ` → `environ`, `python-dateutil` → `dateutil`, … rund 80 Einträge, Quelle:
     `top_level.txt` der Wheels, per Skript `scripts/top_level_names.py` aus den Top-500
     Django-Paketen erzeugt).
   - Fallback: Name normalisiert mit `_` statt `-`, ohne `django-`-Präfix, ohne
     `python-`-Präfix.
4. **Urteil:** „ungenutzt“ nur, wenn
   - `Dependency.direct is True` (0.4),
   - kein Kandidaten-Modulname vorkommt,
   - das Paket kein reines Werkzeug ist, das man nicht importiert (Liste: `gunicorn`, `uwsgi`,
     `whitenoise` wird importiert, aber `gunicorn` nicht, `psycopg`/`mysqlclient` erscheinen
     nur als ENGINE-String, sind also abgedeckt; Dev-Gruppen wie `pytest-django`, `ruff`,
     `coverage`, `django-debug-toolbar` in Dev-Gruppen werden übersprungen),
   - und der Scan vollständig war.
5. **Nur Notiz**, Status bleibt. Zusätzlich gilt die Notiz für **alle** direkten Pakete, auch
   nicht Django-bezogene, in einem eigenen Abschnitt `"Possibly unused (N)"` am Ende des
   Reports.

**Schalter:** Standardmäßig an, wenn `PROJECT` ein Verzeichnis ist. `--no-scan-code` schaltet ab.
Eine Datei als `PROJECT` scannt nur, wenn `--scan-code DIR` gesetzt ist. Der Scan sendet
nichts, daher kein Opt-in nötig, aber im README dokumentiert („reads your code locally to …,
never sends it“).

**Datenmodell:** `Usage(found: bool, where: list[str])` mit bis zu drei Fundstellen
`"settings.py:42"`, nützlich für `--explain`.

**Randfälle:** Monorepo mit mehreren Django-Projekten (Wurzel = PROJECT); Plugins, die nur per
Entry Point laden (pytest-Plugins, Sphinx): Dev-Gruppe überspringen; `__import__`/`importlib`
mit Strings (sind String-Konstanten, also abgedeckt); Syntaxfehler in einer Datei (überspringen,
zählen, Hinweis).

**Tests:** Beispielprojekt in `tests/data/usage/` mit settings, Templates, einem ungenutzten
und einem nur in `INSTALLED_APPS` genutzten Paket; Grenzwerte; Dev-Gruppen; `--no-scan-code`.

**Aufwand:** L. **Abhängigkeiten:** 0.4, 0.3.

### 2.5 Was Django entfernt hat

**Ziel:** Zwischen aktueller und Ziel-Version die entfernten Funktionen auflisten und, mit dem
Code-Scan aus 2.4, markieren, welche der eigene Code noch nutzt. Kein Umschreiben: dafür gibt
es django-upgrade, auf das verwiesen wird.

**Verhalten**

```
Removed in Django 5.0 and 5.1 (3 used in your code)
  DEFAULT_FILE_STORAGE setting       settings.py:88      use STORAGES  · django-upgrade fixes this
  Meta.index_together                shop/models.py:41   use Meta.indexes
  …and 27 more removals you do not use: docs.djangoproject.com/en/5.0/releases/5.0/#features-removed-in-5-0
```

**Daten**

- Paketdatei `src/django_upgrade_report/data/django_removals.json`, erzeugt von
  `scripts/django_removals.py` aus `docs/releases/X.Y.txt` des Django-Repositories (Abschnitt
  „Features removed in X.Y“). Pro Eintrag: `version`, `text` (erster Satz), `symbols`
  (Bezeichner in Backticks, z. B. `DEFAULT_FILE_STORAGE`, `index_together`,
  `django.utils.timezone.utc`), `url` (Anker der Release-Notes), `fixer` (bool, aus einer
  gepflegten Liste der django-upgrade-Fixer).
- Das Skript läuft einmal pro Django-Feature-Release (in RELEASING-Notizen aufnehmen). Ergebnis
  wird eingecheckt und in `pyproject.toml` als Paketdaten aufgenommen
  (`[tool.hatch.build.targets.wheel]` schließt `data/` ein).

**Abgleich mit dem Code:** Pro Symbol eine Suchstrategie:

- Gepunkteter Pfad (`django.utils.timezone.utc`) → Import- oder Attributzugriff im AST.
- Großgeschriebenes Setting → Zuweisung an Modulebene in Dateien, die `settings` im Namen oder
  Pfad haben, oder `settings.X`-Zugriff.
- Kleingeschriebene Bezeichner (`index_together`) → Zuweisung in einer `class Meta`.
- Unbekannte Form → nur Liste, kein Abgleich.

**Ausgabe:** Eigener Abschnitt nach den Paketen, standardmäßig nur genutzte Einträge plus
Summenzeile mit Link. `-v` zeigt alle. JSON: `removals: [{version, text, url, fixer, used_in:
[...]}]`.

**Tests:** Generator-Skript gegen eine kleine Beispiel-Release-Note; Abgleich gegen ein
Beispielprojekt; Paketdaten werden ins Wheel aufgenommen (`tests/test_packaging.py`).

**Aufwand:** M–L. **Abhängigkeiten:** 2.4 (Scanner), 0.3.

---

## 3. Vom Report zur Umsetzung

### 3.1 Befehle ausgeben (`--emit`)

**Ziel:** Fertige Befehle pro Phase statt manuellem Abtippen.

**Verhalten**

```console
django-upgrade-report --emit uv
```

```sh
# Django 4.2.7 → 5.2, generated by django-upgrade-report 0.7.0
# 1. Upgrade first: one at a time, run your tests after each
uv add "django-filter>=23.2"
uv lock --upgrade-package "asgiref==3.8.1"          # transitive
# 2. Together with Django: one change
uv add "django>=5.2,<5.3" "django-with>=3.0"
# Blocked or to check, not included: django-taggit (blocked), django-lagging (check)
```

**Formate:** `uv`, `poetry`, `pdm`, `pip` (Requirements), `pipenv`. `auto` wählt nach der Quelle
(`report.source`).

| Werkzeug | Direkt | Transitiv | Django-Schritt |
| --- | --- | --- | --- |
| uv | `uv add "p>=v"` | `uv lock --upgrade-package "p==v"` | `uv add "django>=X.Y,<X.Y+1"` |
| poetry | `poetry add "p@>=v"` | `poetry update p` (Hinweis: Version via Constraint) | `poetry add "django@~X.Y"` |
| pdm | `pdm add "p>=v"` | `pdm update p` | `pdm add "django~=X.Y.0"` |
| pip | Zeile `p==v` als Ersatz in `requirements*.txt` (Liste der Dateien und alten Zeilen) | Constraints-Datei-Vorschlag | `django==<neueste Patchversion>` |
| pipenv | `pipenv install "p>=v"` | `pipenv update p` | `pipenv install "django~=X.Y.0"` |

- Für `pip` gibt es keine Befehle, die Dateien sicher ändern. Stattdessen eine Liste
  `requirements/base.txt:12  django-filter==2.4.0  →  django-filter==23.2`. Dafür merkt sich
  `sources.parse_requirements` Datei und Zeilennummer (`Dependency.origin`, neues optionales
  Feld).
- Die Untergrenze ist `target_version` (das kleinste Release, das das Ziel deklariert). Die
  Phase-Reihenfolge folgt dem `rank` aus `_upgrade_rank`, also genau der Reihenfolge im Report.
- Pakete mit Notiz „upgrade together with …“ (Zyklen) kommen in denselben Befehl.
- Python-Schritt aus 2.1 als Kommentar vorne: `# 0. Switch to Python 3.12 (…)` und die
  Python-Upgrades.
- Shell-Quoting über `shlex.quote`.

**Ausgabe:** `--emit` ersetzt die normale Ausgabe auf stdout (oder `-o`). Mit `--format json`
landen die Befehle zusätzlich in `"commands": {"tool": "uv", "steps": [{"phase": "before",
"commands": [...]}]}`, damit Skripte sie nutzen können.

**Tests:** Pro Werkzeug ein Snapshot-Test auf Basis des Fake-Index; Quoting; Zyklus-Gruppe;
`pip` mit Datei und Zeile.

**Aufwand:** M. **Abhängigkeiten:** 0.4.

### 3.2 Renovate- und Dependabot-Konfiguration

**Ziel:** Die PR-Bots so einstellen, dass sie zum Plan passen: Django und die
„together“-Pakete als Gruppe, Django bis zum Ende der Vorarbeiten zurückhalten.

**Verhalten:** `--emit renovate` gibt JSON aus, `--emit dependabot` YAML.

Renovate:

```json
{
  "packageRules": [
    {
      "description": "django-upgrade-report: hold Django at 4.2 until the 'upgrade first' list is done",
      "matchPackageNames": ["django"],
      "allowedVersions": "<5.0"
    },
    {
      "description": "django-upgrade-report: these go together with Django 5.2",
      "matchPackageNames": ["django", "django-with"],
      "groupName": "Django 5.2"
    },
    {
      "description": "django-upgrade-report: upgrade first, one at a time",
      "matchPackageNames": ["django-filter"],
      "minimumReleaseAge": "0 days"
    }
  ]
}
```

Dependabot (Ausschnitt für `.github/dependabot.yml`):

```yaml
    groups:
      django-5-2:
        patterns: ["django", "django-with"]
    ignore:
      - dependency-name: "django"
        versions: [">=5.0"]
```

**Design:** Reine Textausgabe aus dem Report, kein YAML-Paket. Renovate-JSON über `json.dumps`.
Die Halteregel entfällt, wenn „Upgrade first“ leer ist. Kommentarzeile, wann man sie wieder
entfernt.

**Tests:** Snapshot für beide Formate; leere Listen; Gruppe mit Zyklus.

**Doku:** README FAQ „How is this different from Dependabot or Renovate?“ um das Rezept
ergänzen.

**Aufwand:** S. **Abhängigkeiten:** 3.1 (gemeinsame Phasenlogik).

### 3.3 Checkliste im HTML-Report und Tracking-Issue

**Ziel:** Den Plan abarbeiten und den Fortschritt sehen.

**A. HTML-Checkliste**

- Jede Zeile in BLOCKED, BEFORE, WITH, UPGRADE und CHECK bekommt eine Checkbox.
- Zustand in `localStorage` unter einem Schlüssel aus Ziel, Quelle und
  `sha256(Paketnamen+Versionen)`, damit ein neuer Report nicht alte Haken übernimmt. Alle
  Zugriffe in `try/catch`, der Report funktioniert auch ohne Speicher (z. B. `file://` in
  Safari).
- Fortschrittsanzeige in den Kacheln: `3 / 8 done`.
- Druck-CSS: Checkboxen als Kästchen, Hintergrundfarben aus, Tabellen nicht umbrechen.
- Kein externes Skript, alles inline (die Datei bleibt eigenständig).

**B. Tracking-Issue (nur in der Action)**

- Input `issue: true` (Default `false`), braucht `permissions: issues: write`.
- Neues Modul `ci.py` mit `python -m django_upgrade_report.ci issue --report report.json`:
  - Sucht ein offenes Issue mit Label `django-upgrade-report` und dem Ziel im Titel
    (`Django 5.2 upgrade plan`). Fehlt es, wird es angelegt.
  - Body: Markdown-Report mit Aufgabenliste (`- [ ] django-filter 2.4 → 23.2`). Bei
    Aktualisierung bleiben abgehakte Punkte abgehakt (Paketname als Schlüssel), Pakete, die
    READY geworden sind, werden automatisch abgehakt und mit `(ready since 2026-11-02)`
    markiert.
  - Wenn alles READY ist: Kommentar `"Everything is ready for Django 5.2"`, Issue bleibt offen
    (Schließen ist Entscheidung der Menschen).
  - Token: `GITHUB_TOKEN` aus der Umgebung, API über den Client aus 0.1.
- Ein Issue pro Ziel, nicht pro Paket, um Lärm zu vermeiden. Option `issue: per-package`
  explizit dokumentiert als späterer Ausbau, nicht in diesem Schritt.

**Tests:** HTML: Checkbox-Markup vorhanden, Schlüsselbildung deterministisch, Seite rendert ohne
JS-Fehler (Playwright-Test optional, Chromium ist in CI verfügbar). Issue: `FakeGitHub` für
Anlegen, Aktualisieren mit erhaltenen Haken, Auto-Haken.

**Aufwand:** M. **Abhängigkeiten:** 0.1, 4.1 (für „ready since“ ist der Vergleich nötig).

---

## 4. CI über längere Zeit

### 4.1 Baseline-Diff

**Ziel:** Nur zeigen, was sich seit dem letzten Lauf geändert hat, z. B. „django-taggit
unterstützt jetzt 5.2“. Damit wird ein wöchentlicher Lauf zum Wächter für Blocker.

**Verhalten**

```console
django-upgrade-report --baseline last.json --format json -o now.json
```

```
Changes since 2026-10-02 (2)
  ✓ django-taggit   blocked → upgrade first   5.0.2 declares Django 5.2
  + django-new      new dependency, ready
```

**Design**

- `--baseline PATH` liest ein früheres JSON (schema_version 1, `kind` `report`). Andere
  Versionen oder Formen → Fehler mit Exit 2.
- Vergleich in neuem Modul `diff.py`, Schlüssel ist `name` (kanonisiert):
  - `status` geändert (mit Richtung über `SEVERITY`: `better`/`worse`),
  - `upgrade_to` geändert bei gleichem Status (`"now 23.3 instead of 23.2"`),
  - `phase` geändert,
  - Paket neu oder entfernt,
  - neue oder weggefallene Warnungen,
  - neue Belege (1.2) oder Upstream-PRs (1.3), nur als Info.
- Anderes Ziel als die Baseline → Hinweis `"The baseline was for Django 5.1, comparing anyway"`.
- Neue Optionen:
  - `--only-changes`: Ausgabe nur des Änderungsabschnitts (für Benachrichtigungen). Ohne
    Änderungen leere Ausgabe und Exit 0.
  - `--fail-on-change {any,worse}`: Exit 1 bei Änderung.
- Ausgabe in allen Formaten als erster Abschnitt. JSON `"changes": [{"name", "kind",
  "from", "to", "direction", "text"}]` oder `null` ohne Baseline.

**Action-Rezept (README und `examples/weekly.yml`):**

```yaml
on:
  schedule: [{ cron: "17 6 * * 1" }]
jobs:
  watch:
    runs-on: ubuntu-latest
    permissions: { contents: read, issues: write }
    steps:
      - uses: actions/checkout@v7
      - uses: actions/cache/restore@v6
        with: { path: .dur-baseline.json, key: dur-baseline-${{ github.run_id }}, restore-keys: dur-baseline- }
      - uses: derblub/django-upgrade-report@v0
        id: django
        with: { baseline: .dur-baseline.json, issue: true }
      - run: cp "${{ steps.django.outputs.report }}" .dur-baseline.json
      - uses: actions/cache/save@v6
        with: { path: .dur-baseline.json, key: dur-baseline-${{ github.run_id }} }
```

Action-Inputs `baseline` (Pfad, leer = aus) und Output `changes` (Anzahl).

**Tests:** Diff-Tabelle mit allen Änderungsarten; Baseline aus einer älteren Tool-Version
(fehlende neue Felder werden toleriert); kaputte Datei; `--only-changes` ohne Änderungen.

**Aufwand:** M. **Abhängigkeiten:** 0.3.

### 4.2 Sticky PR-Kommentar

**Ziel:** Bei PRs, die Abhängigkeiten ändern, den Report als einen Kommentar zeigen, der bei
jedem Push aktualisiert wird.

**Design**

- Action-Input `comment: {false,true,on-change}` (Default `false`), braucht
  `permissions: pull-requests: write`.
- Läuft nur bei `pull_request`- und `pull_request_target`-Events, sonst Hinweis im Log.
- `python -m django_upgrade_report.ci comment --report report.json --markdown report.md`:
  - Marker `<!-- django-upgrade-report:{path}:{target} -->`, damit mehrere Projekte im selben
    Repo eigene Kommentare bekommen.
  - Kommentar mit Marker finden (paginiert über `GET /repos/{o}/{r}/issues/{n}/comments`),
    aktualisieren oder anlegen.
  - `on-change`: Im Marker steht ein kompakter Fingerabdruck
    (`sha256` über `name:status:upgrade_to` aller Pakete). Unverändert → nichts tun.
    Zusätzlich kann der Kommentar mit Baseline (4.1) den Vergleich zum Basis-Branch zeigen,
    wenn der Workflow den Basis-Report erzeugt (Rezept in der Doku: zweiter Checkout von
    `github.base_ref`).
  - Markdown über 65 000 Zeichen (GitHub-Grenze): READY-Liste und Notizen kürzen, Hinweis auf
    das Job-Summary.
- Fork-PRs haben ein Read-only-Token: 403 wird erkannt, Hinweis im Log, Schritt schlägt nicht
  fehl.

**Tests:** `FakeGitHub`: anlegen, aktualisieren, `on-change` ohne Änderung, 403 bei Fork, Kürzung.

**Aufwand:** M. **Abhängigkeiten:** 0.1, optional 4.1.

### 4.3 pre-commit-Hook und `--offline`

**Ziel:** Blocker sehen, bevor ein Lockfile-Commit landet, ohne dass ein Netzproblem den
Commit verhindert.

**A. `--offline`**

- `PyPI` bekommt `offline: bool`. Im Offline-Modus liest `_get` nur den Cache, **ohne TTL**
  (alte Projekt-Indizes sind besser als nichts), und fragt nie das Netz.
- Fehlende Einträge → das Paket landet in `failed` mit dem Problem `"not in the cache"`. Fehlt
  Django selbst, Exit 2 mit `"run once online first"`.
- Hinweis in `notices`: `"Offline: answers from the cache, the newest is from 2026-10-01"`.
- Option `--prefer-cache`: Cache ohne TTL nutzen, Netz nur für fehlende Einträge. Das ist der
  Default im Hook.

**B. Hook**

`.pre-commit-hooks.yaml` im Repo-Wurzelverzeichnis:

```yaml
- id: django-upgrade-report
  name: django-upgrade-report
  description: Fail when a dependency blocks the next Django upgrade.
  entry: django-upgrade-report --fail-on blocked --prefer-cache
  language: python
  pass_filenames: false
  files: '(^|/)(uv\.lock|poetry\.lock|pdm\.lock|Pipfile\.lock|requirements.*\.(txt|in)|pyproject\.toml)$'
```

- Bei Exit 2 (Netz, Cache) soll der Commit nicht hängen: Neue Option
  `--errors-as-warnings` gibt Exit 0 bei Exit-2-Situationen und druckt die Fehlermeldung als
  Warnung. Der Hook nutzt sie, CI nicht.
- Text-Ausgabe im Hook knapp: neue Option `--quiet` zeigt nur Überschrift, Warnungen, BLOCKED
  und die Zählzeile.

**Tests:** Offline mit leerem Cache, mit vollem Cache, `--prefer-cache` holt nur Fehlendes,
`--errors-as-warnings`, `--quiet`. `tests/test_packaging.py` prüft, dass
`.pre-commit-hooks.yaml` gültiges YAML-artiges Format hat (einfacher Zeilenparser genügt).

**Doku:** README „In CI“ → neuer Unterabschnitt „pre-commit“.

**Aufwand:** S–M. **Abhängigkeiten:** 0.1.

---

## 5. Vertrauen: `--explain`

**Ziel:** Für ein Paket die vollständige Begründung zeigen. Das hilft Nutzern, dem Urteil zu
trauen, und Maintainern bei „wrong verdict“-Issues.

**Verhalten**

```console
django-upgrade-report --explain wagtail
```

```
wagtail 6.3 → Django 5.2: upgrade together with Django to 6.3.4

Inputs
  installed  6.3 (uv.lock), direct dependency
  Python     3.12 for markers (target Django 5.2 needs >=3.10, your project uses 3.12)
  current    Django 4.2.7

Your release 6.3 (uploaded 2024-11-01)
  1. requirement Django>=4.2,<5.2 → excludes 5.2? no, allows 5.2.x? no … → NO
     …
Searching newer releases (bisection over 9 stable releases, 3 fetched)
  6.4   (2025-02-03)  declares Django 5.2          → YES
  6.3.4 (2025-04-03)  allows Django<5.3, after 5.2 → YES   ← oldest that declares it
  6.3.2 (2025-01-10)  allows Django<5.2            → NO
Phase
  6.3.4 on Django 4.2.7: requires Django>=4.2 → runs → before
  conflicts: none
Result: upgrade first, 6.3.4
```

**Design**

- `--explain NAME` (wiederholbar). Nur Textausgabe auf stdout; mit `--format json` zusätzlich
  `"explain": {name: [step…]}` im JSON. Der normale Report wird bei `--explain` im Textformat
  **nicht** ausgegeben (nur die Erklärung), damit die Ausgabe kurz bleibt.
- `Trace`-Objekt: `list[TraceStep(section, text, verdict | None)]`, pro erklärtes Paket ein
  eigenes, abgelegt in `_Checker.traces: dict[str, Trace]`. `_Checker`, `_Package` und
  `_plan_order` rufen `self._trace(name, …)` auf; das ist ein No-op, wenn das Paket nicht
  erklärt wird. So entsteht im Normalbetrieb kein Overhead.
- **Regelkette für ein Release:** neue Funktion `explain_support(info, target, uploaded) ->
  list[RuleStep]`, die dieselben Schritte wie `supports()` geht und jeden Schritt festhält
  (Requirement, Classifier, Obergrenze mit Datum, Rest). Damit beide nicht auseinanderlaufen:
  `supports()` wird intern zu `explain_support(…)[-1]` umgebaut, aber hinter einem schnellen
  Pfad, der ohne Listenaufbau auskommt. Ein Test vergleicht über **alle** Releases aller
  aufgezeichneten Fixtures und Ziele 4.2–6.1, dass beide dasselbe Urteil liefern.
- Erklärt werden auch Pakete, die sonst gar nicht im Report stehen:
  - „skipped: no Django requirement and no Framework :: Django classifier in 2.31.0“,
  - „not from PyPI: git github.com/org/fork, judged by its own metadata“,
  - „not on the index“,
  - Paket nicht in den Abhängigkeiten → Exit 2 mit Liste ähnlicher Namen (`difflib`).
- Marker-Auswertung wird gezeigt: welche `Requires-Dist`-Zeilen auf welcher Python galten.
- Die Erklärung enthält die Tool-Version und das Ziel, sodass sie direkt in ein Issue kopiert
  werden kann. Das Issue-Template `wrong-verdict.yml` fordert sie an.

**Tests:** Erklärung für je ein Paket jeder Statusart im Fake-Index (Snapshot); Gleichheit
`supports` vs. `explain_support` über alle Fixtures; unbekanntes Paket; übersprungenes Paket;
Fork.

**Doku:** README Optionstabelle und FAQ „Why does it say …?“; CONTRIBUTING „Reporting a wrong
verdict“ und Issue-Template auf `--explain` umstellen.

**Aufwand:** M. **Abhängigkeiten:** 0.5 (Suchfunktionen nehmen einen Tracer).

---

## 6. Größere Würfe

### 6.1 Wagtail und django CMS als Ziel

**Ziel:** Dieselbe Planung für Wagtail- und django-CMS-Upgrades, nicht nur der Hinweis „also
check it against your Wagtail version“.

**Verhalten**

```console
django-upgrade-report --framework wagtail --target 7.0
```

```
Wagtail 6.3 → 7.0
! Wagtail 7.0 requires Django>=4.2,<6.1: your Django 4.2.7 is fine
…dieselben Abschnitte…
```

**Design**

- Neues Modul `frameworks.py`:
  ```python
  @dataclass(frozen=True)
  class Framework:
      key: str  # "django", "wagtail", "django-cms"
      display: str  # "Django", "Wagtail", "django CMS"
      package: str  # PyPI-Name
      classifier: str  # "Framework :: Django", "Framework :: Wagtail", "Framework :: Django CMS"
      lts: Callable[[Version], bool] | frozenset[Version]
      next_feature: Callable[[Version], Version]
      versions: Literal["minor", "major"]  # Wagtail-Classifier sind Major (":: 6"), Django X.Y
      successors: bool  # nur Django
  ```
  - Django: wie heute (`x.2` LTS, `_next_feature`).
  - Wagtail: LTS als gepflegte Menge (`2.16, 4.1, 5.2, 6.3, 7.0 …`, Quelle: Wagtail-Release-
    Schedule in der Doku, Kommentar mit URL), `next_feature` = nächste bekannte Minor + 1,
    Classifier `Framework :: Wagtail :: 6` (Major). Deshalb zählt bei Wagtail ein
    Major-Classifier als `YES` für alle Minor-Versionen dieser Major-Version, wenn das Release
    nach dem GA der Ziel-Minor kam; sonst `LIKELY`. Diese Abweichung wird in „How it decides“
    dokumentiert.
  - django CMS: Classifier `Framework :: Django CMS :: 4.1`, kein LTS-Konzept (`lts` leer, `auto`
    = neueste).
- `analysis.py`: Jede feste Stelle mit `"django"` wird zum Parameter: `django_requirement` →
  `framework_requirement(info, framework, python)`, `_CLASSIFIER` je Framework,
  `is_django_related` → `is_related(info, framework)`, `_framework_note` entfällt im
  Wagtail-Modus für Wagtail. Texte in `render/*` nutzen `report.framework_display`.
- Der Umbau geschieht über `DjangoRule` → `FrameworkRule(framework, target)` aus 0.5.
- **Zusätzlich im Wagtail-Modus:** Prüfen, ob das Ziel-Wagtail das installierte Django
  zulässt. Wenn nicht: Warnung mit der kleinsten Django-Version, die es braucht, und Vorschlag
  `"run django-upgrade-report --target 5.2 first"`. Ziel-Wagtail selbst kommt als Zeile
  „Wagtail“ in den Plan (Phase `with`).
- CLI: `--framework {django,wagtail,django-cms}` (Default `django`). `--from` und `--target`
  beziehen sich auf das gewählte Framework. Action-Input `framework`.
- JSON: `framework` (`"django"`), `target` bleibt die Version. Die Feldnamen
  `current_django` und `django_requires_python` bleiben für Django bestehen; neu kommen
  `current_framework` und `framework_requires_python` dazu (additiv).

**Randfälle:** Wagtail-Pakete, die nur `wagtail>=x` ohne Classifier haben (häufig) → wie bei
Django `LIKELY`; Pakete, die Wagtail und Django direkt einschränken (Django-Prüfung läuft im
Django-Modus, hier nicht); django-CMS-Pakete mit `djangocms-`-Präfix ohne Classifier.

**Tests:** Golden-Tests mit den vorhandenen Fixtures `wagtail.json` und `wagtail-grapple.json`
plus neu aufgezeichnet `wagtail-modeladmin`, `wagtail-localize`, `djangocms-text`; alle
bestehenden Django-Tests unverändert.

**Doku:** README neuer Abschnitt „Wagtail and django CMS“. CHANGELOG.

**Aufwand:** L. **Abhängigkeiten:** 0.5.

### 6.2 Mehrere Projekte

**Ziel:** Ein Bericht für ein Monorepo oder mehrere Dienste, mit Übersicht, welches Paket wie
viele Projekte blockiert.

**Verhalten**

```console
django-upgrade-report services/*        # mehrere PROJECT-Argumente
django-upgrade-report --recursive .     # findet Projekte selbst
```

```
3 projects
  services/api       Django 4.2.7 → 5.2   2 blocked · 5 to upgrade · 1 to check · 30 ready
  services/admin     Django 5.2.3 → 6.1   0 blocked · 1 to upgrade · 3 to check · 22 ready
  services/worker    Django 4.2.7 → 5.2   1 blocked · 3 to upgrade · 0 to check · 12 ready

Blocking more than one project
  django-taggit   api, worker
```

**Design**

- `project` in `cli.py` wird `nargs="*"`, Default `["."]`.
- `--recursive`: alle Verzeichnisse unter den Argumenten, die eine der Quellen aus
  `sources.LOCKFILES`, `requirements*.txt` oder ein `pyproject.toml` mit Abhängigkeiten
  enthalten. Ausschlussliste wie in 2.4 (venvs, `node_modules`, `.git`). Unterverzeichnisse
  eines gefundenen Projekts werden nicht als eigene Projekte gezählt, außer sie haben ein
  eigenes Lockfile.
- Ein gemeinsamer `PyPI`-Client für alle Projekte (In-Memory-Cache), Projekte nacheinander,
  Pakete parallel wie heute.
- Ziel pro Projekt `auto`, außer `--target` ist gesetzt.
- `MultiReport(projects: list[tuple[str, Report]])` mit Aggregaten:
  `blocking: dict[name, list[project]]`, `shared_upgrades` (gleiches Paket, gleiche
  Zielversion in mehreren Projekten).
- Fehler in einem Projekt (z. B. keine Abhängigkeiten) brechen nicht alles ab: Projekt mit
  Fehlertext in der Übersicht, Exit 2 am Ende, wenn ein Projekt fehlschlug.
- Ausgabe: Text und Markdown mit Übersicht und je Projekt ein eingeklappter Report (Markdown
  `<details>`). HTML mit Übersichtstabelle und Ankern. JSON
  `{"schema_version": 1, "kind": "multi", "projects": [{"path", "report" | "error"}], "blocking": {...}}`.
- `--fail-on` über alle Projekte. Die Action bekommt `path` als mehrzeiligen Input.

**Tests:** Monorepo-Fixture mit drei Projekten (uv, poetry, requirements); ein kaputtes
Projekt; `--recursive`-Ausschlüsse; Aggregation.

**Aufwand:** M–L. **Abhängigkeiten:** 0.3; profitiert von 4.1 und 4.2 (Marker pro Pfad ist
schon vorgesehen).

### 6.3 Öffentliche Readiness-Daten

**Ziel:** Regelmäßig veröffentlichen, wie bereit das Ökosystem für jede Django-Version ist
(wie im r/django-Post), als Reichweite für das Projekt und als Grundlage für spätere Belege.

**Design**

- Getrennt vom CLI, damit das Paket schlank bleibt: Verzeichnis `ecosystem/` im Repo (nicht im
  Wheel, in `pyproject.toml` von sdist ausgeschlossen) mit `ecosystem/build.py`.
- **Paketliste:** Top-N Django-bezogene Pakete nach Downloads. Quelle: der monatliche Datensatz
  „top-pypi-packages“ (öffentliche JSON-Datei von hugovk), gefiltert über
  `is_django_related(latest)`. N = 300. Liste mit Datum versioniert eingecheckt
  (`ecosystem/packages.json`), damit Läufe vergleichbar sind.
- **Lauf:** Für jede unterstützte Django-Version (4.2 bis neueste plus nächste) ein synthetisches
  `DependencySet` mit allen Paketen in ihrer neuesten Version und `analyse()` mit `--from` der
  Vorgängerversion. Damit beantwortet jede Zeile: „Deklariert die neueste Version dieses Pakets
  Django X.Y?“. Ergebnis: Status pro Paket und Version plus Datum, an dem es zum ersten Mal
  READY war (aus dem Upload-Datum des ersten deklarierenden Release).
- **Ausgabe:** `ecosystem/site/index.html` (statisch, gleiche CSS-Tokens wie der HTML-Report),
  `data.json`, und pro Django-Version eine Kurve „Anteil READY über Tage seit GA“.
- **Workflow** `.github/workflows/ecosystem.yml`: wöchentlich, Cache des PyPI-Clients zwischen
  Läufen (`actions/cache`), Veröffentlichung über GitHub Pages
  (`actions/upload-pages-artifact`, `actions/deploy-pages`). `permissions: pages: write,
  id-token: write`.
- **Last auf PyPI:** 300 Pakete × wenige Anfragen dank Cache; die Releases ändern sich nie,
  wöchentlich sind es nur neue. Gleiches Limit von acht parallelen Anfragen.
- **Später:** Die Seite verlinkt aus dem README und aus dem Report-Footer („how the ecosystem
  looks“). Optional nutzt 1.2 die Daten nicht: Der Report bleibt unabhängig von einem Server.

**Tests:** `build.py` gegen den Fake-Index mit fünf Paketen (Snapshot der `data.json`).

**Aufwand:** M. **Abhängigkeiten:** keine harten (nutzt `analyse()`), profitiert von 0.2.

---

## 7. Interaktiver Output

Der Report ist heute eine fertige Seite: Man liest ihn von oben nach unten. Bei 40 und mehr
Django-Paketen will man aber filtern, ein Paket aufklappen, die Begründung sehen und den Befehl
kopieren. Drei Stufen, von „ohne neue Abhängigkeit“ bis „optionales Extra“.

### 7.1 Interaktiver HTML-Report

**Ziel:** Die HTML-Datei bleibt eigenständig (eine Datei, kein CDN), wird aber bedienbar.

**Verhalten**

- Werkzeugleiste über den Abschnitten: Suchfeld (Paketname, Notiz, Begründung), Filter-Chips pro
  Status und Phase, Schalter „only direct dependencies“ (0.4) und „only with notes“.
- Die Kacheln oben sind Filter: Klick auf „3 blocked“ zeigt nur Blocker.
- Spalten sortierbar (Name, Status, Major-Sprünge aus 2.3, letztes Release).
- Jede Zeile klappt auf (`<details>` pro Zeile, funktioniert auch ohne JavaScript) und zeigt:
  die Erklärung aus 5.1, Belege und Upstream-PRs (1.2, 1.3), den Befehl aus 3.1 mit
  Kopier-Knopf, Links zu PyPI, Changelog und Repository.
- Checkboxen und Fortschritt aus 3.3.
- Zustand von Suche und Filtern im URL-Fragment (`#status=blocked,check&q=allauth`), damit man
  eine gefilterte Ansicht weitergeben kann. Kein `localStorage` dafür nötig.
- Tastatur: `/` fokussiert die Suche, `Esc` setzt Filter zurück, `j`/`k` springen zwischen Zeilen,
  `Enter` klappt auf. Alles mit sichtbarem Fokus und `aria-expanded`, `aria-pressed`.

**Design**

- Daten: Der Renderer bettet das JSON aus `render/json.py` (plus `explain` und `commands`) als
  `<script type="application/json" id="report-data">` ein. Das Markup bleibt serverseitig
  gerendert und vollständig, das Skript **filtert nur vorhandene Zeilen** (Attribute
  `data-status`, `data-phase`, `data-direct`, `data-search`). Ohne JavaScript sieht die Datei aus
  wie heute.
- Inline-Skript, unter 8 KB, kein Framework, kein Build-Schritt. Liegt als
  `src/django_upgrade_report/render/html_report.js` im Paket und wird über
  `importlib.resources` eingelesen; ein Test stellt sicher, dass die Datei im Wheel ist.
- Kopieren über `navigator.clipboard.writeText`, mit Fallback auf Markieren des Textes, weil
  `file://` in manchen Browsern keine Zwischenablage erlaubt.
- Druckansicht zeigt immer alle Zeilen, unabhängig vom Filter.
- Option `--format html --static` schaltet das Skript ab (für Umgebungen, die Skripte in
  Anhängen blockieren).

**Tests:** Python-Seite: Datenattribute und eingebettetes JSON vorhanden und gültig, Escaping
von `</script>` im JSON. Browser-Seite: ein Playwright-Test (Chromium liegt in CI bereit, Marker
`browser`, eigener CI-Job) für Suche, Filter-Chip, Aufklappen, URL-Fragment, Kopier-Knopf.
Snapshot von `docs/example-report.html` neu erzeugen.

**Aufwand:** M. **Abhängigkeiten:** 3.3 (Checkliste), nutzt 5.1, 3.1, 2.3, 1.2 wenn vorhanden;
jede fehlende Information blendet ihren Teil einfach aus.

### 7.2 Terminal-Oberfläche (`--interactive`)

**Ziel:** Im Terminal durch den Plan navigieren, statt eine lange Ausgabe zu scrollen.

**Verhalten**

```console
django-upgrade-report -i
```

```
┌ Django 4.2.7 → 5.2 ─────────────── 30 ready · 5 to upgrade · 3 to check · 2 blocked ┐
│ Blocked (2)                      │ django-taggit 4.0.0                                 │
│ > django-taggit      4.0.0       │ latest 5.0.1 requires Django<5.2                    │
│   old-lib            0.3         │ open PR: Add Django 5.2 support (#912)              │
│ Upgrade first (4)                │                                                     │
│   django-filter  2.4 → 23.2      │ Why                                                 │
│   …                              │   1. requirement Django>=4.2,<5.2 → excludes 5.2    │
│ Upgrade together with Django (1) │   …                                                 │
│ Check manually (3)               │ Links  PyPI · changelog · repository                │
└──────────────────────────────────┴─────────────────────────────────────────────────────┘
 / search  f filter  t target  e command  o open link  space done  ? help  q quit
```

- Linke Liste nach Abschnitten, rechts die Details des gewählten Pakets (Begründung, Notizen,
  Erklärung aus 5.1, Belege, Befehl).
- `t` wechselt das Ziel (`5.2`, `6.0`, `6.1`, …) und rechnet neu. Dank Cache dauert das Sekunden.
- `e` kopiert den Befehl aus 3.1 (OSC-52-Escape-Sequenz, funktioniert auch über SSH; sonst
  anzeigen).
- `o` öffnet PyPI, Changelog oder Repository mit `webbrowser`.
- `space` hakt ab. Gespeichert in `.django-upgrade-report/state.json` im Projekt (nicht im
  Cache, weil projektbezogen), Format: Paket, Zielversion, Zeitpunkt. Hinweis im README, die
  Datei in `.gitignore` aufzunehmen oder bewusst einzuchecken.
- `w` schreibt den aktuellen Report in einem wählbaren Format (`-o`-Logik).

**Design**

- Optionales Extra `django-upgrade-report[tui]` mit `textual` als einziger Abhängigkeit, damit
  der Kern bei `packaging` (und `tomli`) bleibt. Modul `tui.py` wird nur bei `-i` importiert.
  Fehlt das Extra: Exit 2 mit `"pip install 'django-upgrade-report[tui]'"` bzw.
  `"uvx --with textual django-upgrade-report -i"`.
- Die Oberfläche bekommt den fertigen `Report` (bzw. `PathReport`, `MultiReport`) und rendert
  dieselben `sections()` aus `render/__init__.py`. Keine eigene Logik, nur Darstellung.
- Neu rechnen läuft in einem Worker-Thread (Textual `run_worker`), die bestehende
  Fortschrittsanzeige wird zu einem Callback in die Statuszeile.
- `-i` ohne Terminal (`not sys.stdout.isatty()`) oder mit gesetzter `CI`-Variable → Exit 2
  mit klarer Meldung. `-i` zusammen mit `--format`, `-o`, `--fail-on`, `--emit` → Fehler bei der
  Argumentprüfung.
- Farben respektieren `NO_COLOR`; Textual-Theme hell und dunkel aus denselben Statusfarben wie
  HTML.

**Tests:** Textual `App.run_test()` (Pilot) mit einem Report aus dem Fake-Index: Navigation,
Suche, Filter, Zielwechsel (zweites `analyse()` gegen denselben Fake-Index), Abhaken schreibt
die Zustandsdatei, fehlendes Extra ergibt Exit 2. CI installiert das Extra in einem Job; die
anderen Jobs prüfen, dass der Kern ohne Textual läuft.

**Aufwand:** L. **Abhängigkeiten:** 5.1 und 3.1 für den Detailbereich (sonst ausgeblendet);
3.3 teilt das Abhaken-Konzept.

### 7.3 Fehlende Angaben nachfragen

**Ziel:** Statt einer Warnung („Django is not pinned: pass --from …“) im Terminal direkt
nachfragen.

**Verhalten**

```
Django is not pinned (Django>=4.2). Which version do you run?
  1) 4.2.16 (newest 4.2)   2) 5.0.14   3) 5.1.8   4) skip
> 1
Tip: next time pass --from 4.2.16
```

**Design**

- Nur wenn stdin und stderr ein Terminal sind, `CI` nicht gesetzt ist und keine
  Ausgabedatei/kein Maschinenformat verlangt wird (`--format text`, kein `-o`). Sonst Verhalten
  wie heute. `--no-input` schaltet Fragen ab.
- Gefragt wird nur, was das Ergebnis wirklich ändert:
  - die eigene Django-Version, wenn nicht gepinnt (Auswahl aus den Serien, die das Requirement
    erlaubt, über `spec_sets` und `_series`),
  - das Ziel, wenn `auto` ein LTS überspringt (Auswahl: kleinerer Schritt, wie vorgeschlagen,
    oder `--via lts` aus 2.2),
  - die Projekt-Python, wenn unbekannt und das Ziel eine Mindestversion hat.
- Umgesetzt mit `input()` auf stderr, ohne Abhängigkeit; in der TUI (7.2) als Dialog.
- Nach der Antwort wird die passende Option als Tipp ausgegeben, damit der nächste Lauf
  reproduzierbar ist.

**Tests:** `monkeypatch` für `isatty` und `input`; jede Frage einmal; `CI=1` und `--no-input`
fragen nie; ungültige Eingabe wird wiederholt, `EOF` bricht ohne Frage ab (Verhalten wie heute).

**Aufwand:** S. **Abhängigkeiten:** keine; 2.2 für die `--via`-Auswahl.

---

## Release-Reihenfolge

Jede Zeile ist ein Minor-Release mit eigenem CHANGELOG-Abschnitt. Innerhalb eines Releases ein
PR pro Punkt.

| Release | Inhalt | Warum diese Reihenfolge |
| --- | --- | --- |
| **0.5** | 0.1, 0.2, 0.3, 0.6, **1.1** Pre-Releases, **2.3** Risiko und Changelog, **4.3** pre-commit und `--offline` | Fundament plus drei kleine, sofort sichtbare Verbesserungen ohne neue Netzwerkziele. |
| **0.6** | 0.5, **5.1** `--explain`, **7.3** fehlende Angaben nachfragen | Der Such-Umbau wird durch `--explain` sofort genutzt und abgesichert, bevor größere Regeln dazukommen. |
| **0.7** | **2.1** Python-Readiness | Größter inhaltlicher Mehrwert; braucht 0.2 und 0.5. |
| **0.8** | **4.1** Baseline, **4.2** PR-Kommentar, **3.3** Checkliste und Tracking-Issue, **7.1** interaktiver HTML-Report | Alles rund um wiederholte Läufe, gemeinsam mit dem GitHub-Client aus 0.1. |
| **0.9** | 0.4, **3.1** `--emit`, **3.2** Renovate/Dependabot, **2.2** `--via` | Umsetzungshilfen; `--via` nutzt dieselbe Ersetzungslogik der Versionen. |
| **0.10** | **1.2** Belege, **1.3** Upstream-Issues, **7.2** Terminal-Oberfläche | Erstes Opt-in für einen weiteren Host; klare Doku zum Datenschutz. |
| **0.11** | **2.4** ungenutzte Pakete, **2.5** Django-Entfernungen | Lokaler Code-Scan, beide teilen den Scanner. |
| **1.0** | **6.1** Frameworks, **6.2** Mehrere Projekte | Die größten Umbauten zuletzt, wenn alles andere stabil ist; danach API- und JSON-Stabilität als 1.0 zusagen. |
| separat | **6.3** Ökosystem-Seite | Unabhängig vom Paket-Release, kann jederzeit nach 0.5 starten. |

Die Action folgt jedem Release mit neuen Inputs; der Tag `v0` wird wie bisher nachgezogen.

## Risiken

| Risiko | Gegenmaßnahme |
| --- | --- |
| Mehr Last auf PyPI durch Pre-Releases und Python-Analyse | Höchstens eine zusätzliche Release-Anfrage pro CHECK/BLOCKED-Paket; Python nutzt die Release-Daten aus dem Projekt-JSON; Anfragezähler-Tests in jedem PR. |
| Cache-Formatwechsel verliert alte Einträge | Einmaliger Mehrabruf; README-Hinweis; Cache-Schlüssel mit Version, damit kein gemischter Zustand entsteht. |
| Heuristiken (Testmatrix, Changelog, ungenutzte Pakete) liegen falsch | Nie Status ändern, nur Notizen mit Quelle und Link; konservative Parser; im Zweifel nichts sagen. |
| GitHub-Rate-Limits | Opt-in, Cache, harte Obergrenzen pro Lauf, Hinweis auf `GITHUB_TOKEN`. |
| Datenschutzversprechen im README | Jede neue Netzwerkquelle ist opt-in, in der FAQ beschrieben und hat einen Test „ohne Flag keine Anfrage“. |
| `supports()` und `--explain` laufen auseinander | Gleichheitstest über alle Fixtures. |
| Framework-Verallgemeinerung bricht Django-Verhalten | 0.5 zuerst als reiner Umbau mit identischen Golden-Tests und Anfragelisten; 6.1 erst zu 1.0. |
| JSON-Verbraucher | Nur additive Felder; `kind` unterscheidet neue Dokumentformen; `schema_version` bleibt 1. |
| Interaktivität bricht Skripte und CI | Fragen und `-i` nur mit Terminal und ohne `CI`; `--no-input`; die TUI ist ein optionales Extra, der Kern bekommt keine neue Abhängigkeit; HTML funktioniert ohne JavaScript. |
| Laufzeit bei großen Repos (Code-Scan, `--recursive`) | Grenzwerte für Dateien und Größe, Ausschlussliste, Abbruch mit Hinweis statt halber Aussage. |

## Checkliste pro Pull Request

- [ ] Ein Thema pro PR, mit Test, der ohne die Änderung fehlschlägt (CONTRIBUTING.md).
- [ ] Keine Netzwerkzugriffe in Tests; neue Netzwerkziele nur über `FakePyPI` / `FakeGitHub`.
- [ ] Golden-Tests in `tests/test_analysis.py` unverändert grün, oder jede geänderte Assertion
      begründet.
- [ ] Anfragezahl auf den Fake-Indizes geprüft, wo die Funktion PyPI fragt.
- [ ] Alle vier Formate (Text, Markdown, HTML, JSON) zeigen die neue Information in derselben
      Reihenfolge; Markdown und HTML escapen Fremdinhalt.
- [ ] JSON-Felder im Docstring von `render/json.py` dokumentiert; `schema_version` unverändert.
- [ ] Neue Optionen in `cli.py`, README-Optionstabelle und, wo sinnvoll, `action.yml` mit
      Input-Tabelle im README.
- [ ] README „How it decides“ bei jeder Regeländerung angepasst.
- [ ] CHANGELOG unter „Unreleased“.
- [ ] `uv run --group dev pytest --cov`, Abdeckung ≥ 90 %; `uvx ruff check .`;
      `uvx ruff format --check .`.
- [ ] Windows-Job grün (Pfade, Encoding bei neuer Ausgabe).
