"""M0 scaffold smoke test: the package skeleton imports cleanly.

Expanded by the real model tests in M1 (test_virtual_document.py, test_materialize.py).
"""


def test_packages_importable():
    import klarpdf.model  # noqa: F401
    import organize  # noqa: F401
    import store  # noqa: F401
    import klarpdf.util  # noqa: F401
    import viewer  # noqa: F401
