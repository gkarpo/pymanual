from __future__ import annotations

import json as _json
import os
import sysconfig

import pytest

from pymanual._probe import classify


def test_stdlib_path_classified_as_stdlib():
    assert classify(_json.__file__) == "stdlib"


def test_third_party_site_packages():
    fake = os.path.join(os.sep, "tmp", "venv", "lib", "python3.12", "site-packages", "foo", "__init__.py")
    assert classify(fake) == "third_party"


def test_third_party_dist_packages():
    fake = os.path.join(os.sep, "usr", "lib", "python3", "dist-packages", "foo.py")
    assert classify(fake) == "third_party"


def test_local_path(tmp_path):
    f = tmp_path / "mymod.py"
    f.write_text("x = 1\n")
    assert classify(str(f)) == "local"


def test_venv_marker_classified_as_third_party():
    fake = os.path.join(os.sep, "home", "u", "proj", ".venv", "somewhere", "foo.py")
    assert classify(fake) == "third_party"


def test_pytest_classified_as_third_party():
    pytest_file = pytest.__file__
    assert classify(pytest_file) == "third_party"


def test_stdlib_root_present():
    # sanity: sysconfig returns something usable
    assert os.path.isdir(sysconfig.get_paths()["stdlib"])
