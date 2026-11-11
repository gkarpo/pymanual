from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Redirect the cache to a per-test tmp dir so tests never touch the user's real cache."""
    monkeypatch.setenv("PYMANUAL_CACHE_DIR", str(tmp_path / "cache"))
    yield


@pytest.fixture(autouse=True)
def _auto_detects_this_environment(monkeypatch):
    """Make the CLI's ``--python auto`` land on the test process itself.

    Otherwise it would follow whatever ``$VIRTUAL_ENV`` or ``python3`` the
    shell running pytest happens to have. Tests of detection override this.
    """
    monkeypatch.delenv("PYMANUAL_PYTHON", raising=False)
    monkeypatch.setenv("VIRTUAL_ENV", sys.prefix)


@dataclass(frozen=True)
class TargetEnv:
    """A real virtual environment, separate from the one running the tests."""

    root: Path
    python: Path
    site_packages: Path

    def write(self, relpath: str, source: str = "") -> Path:
        path = self.site_packages / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        return path


@pytest.fixture(scope="session")
def target_env(tmp_path_factory) -> TargetEnv:
    """A pip-less venv holding a package the test environment doesn't have.

    ``targetonlypkg`` is importable only here, and is provided by a
    distribution with a *different* name (``target-only-dist``), so both the
    import-name → distribution mapping and the distribution-name fallback
    can be exercised against real metadata.
    """
    root = tmp_path_factory.mktemp("target") / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root)], check=True)
    python = root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    site = Path(
        subprocess.run(
            [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    env = TargetEnv(root=root, python=python, site_packages=site)

    env.write("targetonlypkg/__init__.py", "VALUE = 1\n")
    env.write("targetonlypkg/sub.py", "VALUE = 2\n")
    dist_info = "target_only_dist-1.0.dist-info"
    env.write(
        f"{dist_info}/METADATA",
        "Metadata-Version: 2.1\n"
        "Name: target-only-dist\n"
        "Version: 1.0\n"
        "Project-URL: Homepage, https://target.example/\n"
        "Project-URL: Documentation, https://target.example/docs/\n",
    )
    env.write(f"{dist_info}/top_level.txt", "targetonlypkg\n")
    env.write(f"{dist_info}/RECORD", "")
    return env
