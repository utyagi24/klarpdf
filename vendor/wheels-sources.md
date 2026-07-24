# Vendored wheel sources (offline Windows ship build)

Exact `win_amd64` wheels for the pinned ship lock (`requirements-win.txt`). The wheels themselves
are **not committed** (binary bloat; GitHub's 100 MB/file limit) — this file is the auditable
record so the set can be re-fetched and verified offline, and the M8 installer bundles them so
the target machine needs no Python and no network. Regenerate with `vendor/gen-sources.py`
(see its header) after any `requirements-win.txt` change.

Every `sha256` below also appears in `requirements-win.txt`; `pip install --require-hashes` refuses
any wheel whose hash does not match.

## PyMuPDF 1.27.2.3
- wheel: `pymupdf-1.27.2.3-cp310-abi3-win_amd64.whl`
- sha256: `d20f68ef15195e073071dbc4ae7455257c7889af7584e39df490c0a92728526e`
- source: https://files.pythonhosted.org/packages/44/47/5fb10fe73f96b31253a41647c362ea9e0380920bddf16028414a051247fc/pymupdf-1.27.2.3-cp310-abi3-win_amd64.whl

## pypdf 6.14.2
- wheel: `pypdf-6.14.2-py3-none-any.whl`
- sha256: `3f07891af76dc002657e04993ab9b4de81de29f9013b9761d0b7968bff12e946`
- source: https://files.pythonhosted.org/packages/49/e6/136aa8993a2ae7214e0b0ef2edaa0d2e08d1d4e4982635b08a835ff31ec8/pypdf-6.14.2-py3-none-any.whl

## PySide6_Essentials 6.11.1
- wheel: `pyside6_essentials-6.11.1-cp310-abi3-win_amd64.whl`
- sha256: `63311bd48e32c584599ab04b9ef7c324082374cd2c9fa533f978fb893bb47e40`
- source: https://files.pythonhosted.org/packages/64/0e/b663ecc96ca57b5c91b83b6615d6b174380b0faf30338125c26e053d6aa7/pyside6_essentials-6.11.1-cp310-abi3-win_amd64.whl

## shiboken6 6.11.1
- wheel: `shiboken6-6.11.1-cp310-abi3-win_amd64.whl`
- sha256: `c2c6863aa80ec18c0f82cea3417837b279cdc60024ac17123461dc9042577df7`
- source: https://files.pythonhosted.org/packages/52/b5/3f6fb2ee65b534193fb4ef713dd619dc31dadff5d12c16979a7699ad58be/shiboken6-6.11.1-cp310-abi3-win_amd64.whl
