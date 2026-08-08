# pymanual

Given a Python **module name** (import name) *or* a **distribution name** (the string you'd type in `pip install …`), deterministically return the best available documentation URL — or a status token explaining why one isn't available.

## Install

End users:

```bash
pipx install pymanual
# or
uv tool install pymanual
```

This puts `pymanual` on your `PATH`.

Developing on the project itself instead:

```bash
git clone https://github.com/gkarpo/pymanual && cd pymanual
uv sync
```

## CLI

```
pymanual <module> [--no-network] [--no-cache]
pymanual --clear-cache
```

Exit code is `0` when a URL is returned, `1` when a status token is returned.

```bash
$ pymanual json
https://docs.python.org/3/library/json.html

$ pymanual email.mime.text
https://docs.python.org/3/library/email.html

$ pymanual pytest
https://docs.pytest.org/en/latest/

$ pymanual pytest-cov       # distribution name with a dash
https://pytest-cov.readthedocs.io/

$ pymanual python-dateutil  # not installed; PyPI lookup
https://dateutil.readthedocs.io/en/stable/

$ pymanual winreg           # stdlib, but not importable on Linux
https://docs.python.org/3/library/winreg.html

$ pymanual definitely_no_such_xyz
module_not_found
```

If you're working inside the project source, prefix every command with `uv run` (e.g. `uv run pymanual json`).

### Flags

- `--no-network` — skip the PyPI HTTP fallback (step **4.ii** below for installed third-party modules, and the second half of step **5** for the distribution-name fallback). Resolution stays fully offline.
- `--no-cache` — bypass the local cache for that one call (no read, no write).
- `--clear-cache` — delete the cache file and exit.

## Library

```python
from pymanual import (
    resolve_doc_url,
    STATUS_NOT_FOUND,
    STATUS_UNRESOLVABLE_PATH,
    STATUS_LOCAL,
)

resolve_doc_url("json")                 # 'https://docs.python.org/3/library/json.html'
resolve_doc_url("pytest")               # URL from installed distribution metadata
resolve_doc_url("pytest-cov")           # distribution-name fallback
resolve_doc_url("python-dateutil")      # not installed → PyPI lookup
resolve_doc_url("nope_xyz")             # 'module_not_found'

# Same options as the CLI flags:
resolve_doc_url("pytest", network=False)     # offline-only
resolve_doc_url("json", use_cache=False)     # bypass the on-disk cache
```

## Resolution strategy

Strict order — the first step that yields a result wins.

**The stdlib name check.** Steps 1 and 2 both reach points where no file path is available to classify. Both then ask whether `top_level` is in `sys.stdlib_module_names`, and if so return `https://docs.python.org/3/library/<top-level>.html`. That list is generated at build time and covers every stdlib module unconditionally, including ones belonging to other platforms (`winreg` on Linux) and ones a build omitted (`tkinter` without Tk). It is deliberately consulted **only after** a path lookup has come up empty, never before: a local `statistics.py` shadowing a stdlib name is still your own code and must return `local_module_no_external_docs`.

1. **Locate the module** with `importlib.util.find_spec` — which finds it *without executing it* (see [Modules are never imported](#modules-are-never-imported) below).
   - On success → continue at step 2.
   - `None`, `ImportError` or `ValueError`, for a dotted name → retry with just the top-level name, so a missing leaf under a real package (`pytest.nosuchthing`) still answers from the package.
   - Still nothing → apply the **stdlib name check**. This is what resolves `winreg` on Linux, and what makes `json.nosuchthing` return `json`'s page rather than reaching PyPI (which carries projects named `json`, `email`, and other short stdlib names). If it doesn't apply → try the **distribution-name fallback** (step 5).
2. **Take `spec.origin`.** Builtins (`sys`, `builtins`, …) are short-circuited as stdlib before this step. If there's no usable path — `None` for namespace packages, the sentinel `'frozen'` for the frozen bootstrap stdlib (`os`, `io`, `abc`, `codecs`, `zipimport`), or a source file deleted since import — apply the **stdlib name check**; if it doesn't apply either, return `unresolvable_module_path`.
3. **Classify by file path only** (not `sys.path`):
   - inside `sysconfig.get_paths()["stdlib"]` → **stdlib**
   - inside `site-packages` / `dist-packages` / a venv directory → **third_party**
   - otherwise → **local**
4. **Resolve URL by classification:**
   - **stdlib** → `https://docs.python.org/3/library/<top-level>.html`
   - **third_party**:
     1. `importlib.metadata.metadata(...)` → prefer `Project-URL` keys matching `documentation`/`docs`/`doc`, then `homepage`, then `Home-page`
     2. PyPI JSON (`https://pypi.org/pypi/<name>/json`) — same key precedence; skipped when `network=False`
     3. Heuristic last resort: `https://pypi.org/project/<name>/`
   - **local** → `local_module_no_external_docs`
5. **Distribution-name fallback** (when `find_spec` located nothing and the stdlib name check didn't apply):
   - Try `importlib.metadata.metadata(<input>)` — catches cases like `pytest-cov`, `PyYAML`, `scikit-learn` whose import names don't match their distribution names. If installed → return URL from its metadata (falling back to `https://pypi.org/project/<input>/`).
   - Else, if `network=True`, query PyPI directly for `<input>`. If the package exists there → return its documentation URL.
   - Otherwise → `module_not_found`.

Import-name vs distribution-name mismatches (e.g. `yaml` → `PyYAML`) from step 4 are handled via `importlib.metadata.packages_distributions()`.

### Modules are never imported

Resolving a name does not run that module's code. `pymanual` locates modules with `find_spec` rather than importing them, so `pymanual foo` will not execute whatever `foo.py` happens to be sitting in your working directory.

One exception survives, by necessity: for a **dotted** name, `find_spec` imports the *parent* packages to read their `__path__`. `pymanual email.mime.text` imports `email` and `email.mime`; the leaf is never executed. Import-time errors in a parent propagate rather than being swallowed. This is why `pymanual pytest` is fast while `pymanual pytest.something` still pays for importing `pytest`.

## Local cache

Successful URL resolutions are cached at `$XDG_CACHE_HOME/pymanual/cache.json` (defaults to `~/.cache/pymanual/cache.json`). Subsequent lookups for the same input string return instantly without hitting `find_spec`, `importlib.metadata`, or PyPI.

- Status tokens (`module_not_found`, etc.) are **not** cached, so installing a previously-missing module just works on the next call.
- Cache key is the verbatim input string — `pytest-cov` and `pytest_cov` are independent entries.
- LRU-bounded at 100 entries; the oldest entry is evicted when the cap is exceeded.
- No TTL; entries live until eviction, until you run `pymanual --clear-cache`, or until you delete the file.
- Override location for tests/sandboxing via the `PYMANUAL_CACHE_DIR` env var.

## Output contract

A single string — either a URL or one of:

- `module_not_found`
- `unresolvable_module_path`
- `local_module_no_external_docs`

## Development

```bash
uv run pytest -q                  # run tests
uv run pytest --cov=pymanual      # with coverage
```
