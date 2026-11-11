# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The CLI now locates modules in **your** Python environment, not pymanual's own.
  Installed with `uv tool` or `pipx`, pymanual lives in an isolated venv that
  holds nothing but itself, so `pymanual markdown_it` returned `module_not_found`
  even when `python3 -c "import markdown_it"` worked. The CLI now runs a small
  probe under `$PYMANUAL_PYTHON`, else the active `$VIRTUAL_ENV`, else `python3`
  on `PATH`, and falls back to its own environment if that interpreter can't
  answer within 10 seconds.
- `--python PATH` picks the interpreter explicitly: an interpreter path, a
  virtual environment directory, or a command name on `PATH`.
- `-v`/`--verbose` explains on stderr which interpreter was used and why.
- `resolve_doc_url(..., python=...)` exposes the same choice to library callers.
  The default stays `None` (resolve in the calling process); pass `"auto"` for
  the CLI's detection.

### Changed

- Cache entries resolved through another interpreter are keyed by that
  interpreter's path, because the same name can be a third-party package in one
  environment and your own module in another. In-process entries keep the bare
  name, so existing cache files stay valid.
- The working directory is not on the probe's `sys.path`, matching the
  installed `pymanual` command, whose `sys.path[0]` is its own `bin` directory.

## [1.1.0] - 2026-08-04

### Added

- Modules are now **located** with `importlib.util.find_spec` rather than imported.
  Resolving a name no longer executes that module's code, so `pymanual foo` will
  not run whatever `foo.py` happens to sit in the working directory. One exception
  remains by necessity: for a dotted name, `find_spec` imports the *parent*
  packages to read their `__path__`. Leaf modules are never executed.
- Stdlib modules that aren't importable in the current environment now resolve
  from `sys.stdlib_module_names` instead of returning `module_not_found` —
  `winreg` on Linux and macOS, `tkinter` on a build without Tk.
- Modules with no usable file path now resolve by name: the frozen bootstrap
  stdlib (`os`, `io`, `abc`, `codecs`, `stat`, `zipimport`) and namespace
  packages.
- A missing submodule under a real package resolves to the package's page
  rather than falling through to PyPI. `json.nosuchthing` returns the `json`
  page instead of a project that happens to share the name on PyPI, and
  `_pytest.nosuchthing` resolves through pytest's distribution metadata.

### Fixed

- `resolve_doc_url(".foo")` and `pymanual .foo` returned a `TypeError` traceback
  instead of a status token. They now return `module_not_found`, as the output
  contract requires.

### Performance

Measured over 25 inputs, five cold runs each:

| input | 1.0.0 | 1.1.0 |
|---|---|---|
| `pytest` | 41.4 ms | 6.4 ms |
| `_pytest.config` | 21.4 ms | 6.8 ms |
| `email.mime.text` | 3.19 ms | 0.63 ms |
| `os`, `zipimport` | ~0.55 ms | ~0.02 ms |

Dotted third-party names (`pytest.nosuchthing`) are unchanged at ~40 ms, which
is the residual cost of importing the parent package.

### Known issues

- `resolve_doc_url("")` raises `ValueError` instead of returning a status token.
- Private stdlib modules (`_ast`, `_ssl`) and undocumented ones (`encodings`)
  return a `docs.python.org` URL that 404s.

## [1.0.0] - 2026-04-22

Initial release.

- Resolve a documentation URL from a module import name or a PyPI distribution
  name, or return one of three status tokens explaining why none is available.
- `pymanual` CLI with `--no-network`, `--no-cache` and `--clear-cache`; exit 0
  for a URL, 1 for a status token.
- `resolve_doc_url()` library entry point.
- On-disk LRU cache, 100 entries, at `$XDG_CACHE_HOME/pymanual/cache.json`.

[Unreleased]: https://github.com/gkarpo/pymanual/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/gkarpo/pymanual/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/gkarpo/pymanual/releases/tag/v1.0.0
