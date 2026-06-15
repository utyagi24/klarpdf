# pdfproj

Local, offline, **native-Windows** PDF viewer + page editor (Python · PySide6 · PyMuPDF) — a
trustworthy replacement for macOS Preview's view + splice/split workflow on Windows. The source is
the unit of audit; it ships as a pinned, fully offline Windows installer.

| Doc | What |
|---|---|
| [PLAN.md](PLAN.md) | Product spec, architecture, dependencies/packaging, portability, build order, **Execution**, verification |
| [PROGRESS.md](PROGRESS.md) | Live milestone checklist (M0–M9) |
| [CLAUDE.md](CLAUDE.md) | Orientation + conventions for contributors/agents |

## Develop (WSL)

```bash
# one-time: base Ubuntu python lacks ensurepip
sudo apt install -y python3.12-venv

python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

The cross-platform core and headless tests run in WSL; the GUI iterates via WSLg. Packaging and
Windows shell-integration happen on Windows only (PLAN.md §Development environment).

**Status:** M0 — scaffold. See [PROGRESS.md](PROGRESS.md).
