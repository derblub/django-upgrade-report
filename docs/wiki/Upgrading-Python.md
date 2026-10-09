# Upgrading Python

Django 6.0 needs Python 3.12. When the target Django needs a newer Python than your project uses, the report starts with what **all** your dependencies need on it, not only the Django-related ones. A C extension without a wheel for the new Python is the most common surprise of a Django upgrade.

```text
Python 3.12 first (2)
  These dependencies need something before they run on Python 3.12.
  ↑ numpy            1.22.4 → 1.26.0  1.22.4 no wheel for Python 3.12, pip builds it from source
  ↑ psycopg2-binary  2.9.3 → 2.9.9    2.9.3 no wheel for Python 3.12, pip builds it from source
  3 more dependencies run on Python 3.12, all pure Python.
  1 says nothing about Python versions: pycrypto.
  Django 4.2.7 does not declare Python 3.12, 4.2.8 does: update Django 4.2 first.
```

## When it runs

| `--python-target` | Checks |
| --- | --- |
| `auto` (default) | The Python the target Django needs, when your project uses an older one. Your project's Python must be known, see [Dependency sources](Dependency-Sources#which-python). |
| `3.13`, … | That Python, whatever Django needs. Useful for a Python upgrade on its own. |
| `none` | Nothing. |

## How a release is judged on a Python

In this order:

1. A `Requires-Python` that excludes it means **no**.
2. A `Programming Language :: Python :: 3.12` classifier means **yes**.
3. A wheel built for it (`cp312`) means **yes**.
4. An `abi3` wheel for an older Python, or a pure-Python wheel (`py3-none-any`), runs on it too.
5. Wheels only for other Pythons mean pip **builds it from source**: that needs a compiler and headers and often fails. Without a source distribution it cannot be installed at all, which means **no**.
6. Otherwise the release **says nothing** about Python versions.

Wheels count when they install on CPython under Linux on x86_64, where Django apps run. PyPy and free-threading (`cp313t`) wheels do not count.

## What the section shows

- **Rows** for dependencies whose installed release excludes the Python or would be built from source, with the oldest newer release that runs on it, and those no release fixes (blocked).
- A note **goes together with the switch to Python 3.12** when that release no longer runs on the Python you use today.
- One line counts the dependencies that run on it already; another names those whose release says nothing about Python at all (old sdist-only packages).
- One line says whether **your Django patch release** declares the Python, or which patch of your series first does.
- In the Django plan, an upgrade whose release excludes the new Python says so on its row.

The installed releases come from the cache the Django check already filled. A release history is only fetched for a dependency that needs an upgrade.

## In CI

```console
django-upgrade-report --fail-on-python blocked
```

fails when a dependency has no release that runs on the new Python. The GitHub Action has the inputs `python-target` and `fail-on-python`, and the output `python-blocked`. The JSON report has the section under `python`.
