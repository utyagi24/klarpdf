"""M0 scaffold smoke test: the package skeleton imports cleanly.

Expanded by the real model tests in M1 (test_virtual_document.py, test_materialize.py).
"""


import importlib

import pytest


@pytest.mark.parametrize("package", ["klarpdf.model", "klarpdf.util", "organize", "store", "viewer"])
def test_packages_importable(package):
    importlib.import_module(package)
