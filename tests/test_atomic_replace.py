"""M38.5 — the save path's ``os.replace`` survives a transient lock on the freshly written temp.

Two halves: the ``util/atomic.py`` retry policy on its own (pure, no Qt), and the thing that
actually mattered — a save whose first rename loses the race to an antivirus handle must still
succeed, not raise the "Save failed" modal that the conftest guard turns into a failure.

The Windows error this models is ``PermissionError: [WinError 5] Access is denied`` (or WinError
32, sharing violation) from ``os.replace`` while another process holds the temp open. It cannot be
reproduced on Linux, so both halves inject it — the bug was never in our logic, it was the absence
of a second attempt.

**M130 — why the doubles never delegate to the real ``os.replace``.** These tests began flaking at
roughly **one run in five** on a Windows box, in two different tests, with the reported one moving
between runs. The cause was not a bug in either: the doubles handed off to the genuine
``os.replace`` once their scripted failures were spent, and on a machine where a scanner can briefly
hold a freshly written file that call raised — so ``atomic_replace`` retried, correctly, and the
assertions counted an attempt they had not scripted.

The lesson generalises past this file: **a component whose whole job is tolerating a hostile
environment cannot be measured in one.** So the split here is deliberate — tests that assert on
attempt counts or backoff get a double that touches **no disk**, and the single test that exercises
the real filesystem asserts only that the bytes arrived, never how many tries it took.
"""

from __future__ import annotations

import os
import time

import pytest

from app import PdfApp
from main_window import MainWindow
from store.settings import Settings
from klarpdf.util import atomic
from klarpdf.util.atomic import atomic_replace

# Captured before `_no_real_sleeping` runs. That fixture patches `sleep` on the `time` **module**,
# which every importer shares — so a double that wants a genuine pause has to hold its own reference.
_real_sleep = time.sleep


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "view_state.json")
    qapp.page_clipboard = []
    return qapp


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    """Record the backoff instead of serving it — the policy is what is under test, not the clock.

    Without this the persistent-failure test would spend its whole ~0.75 s budget for nothing.
    """
    slept: list[float] = []
    monkeypatch.setattr(atomic.time, "sleep", slept.append)
    return slept


def _scripted_replace(failures: int, calls: list[int]):
    """A drop-in ``os.replace`` that fails ``failures`` times, then succeeds — touching no disk.

    **Hermetic on purpose (M130).** The earlier version delegated to the real ``os.replace`` once its
    scripted failures were spent, which let the *machine* join in: on a box whose antivirus briefly
    locks a freshly written temp — the exact condition M38.5 exists to absorb — that real call raised
    too, `atomic_replace` retried as designed, and the test counted one attempt more than it scripted.
    Measured at **4 failures in 20 runs** of this file. The production code was right every time; the
    assertion was measuring the environment.

    So a test about the *policy* gets no filesystem at all. Whether the bytes really move is a
    different question, asked by `test_a_real_replace_moves_the_file` below.
    """

    def replace(src, dst):
        calls.append(1)
        if len(calls) <= failures:
            raise PermissionError(5, "Access is denied")
        return None

    return replace


def _moving_replace(failures: int, calls: list[int]):
    """Scripted failures, then a real move that absorbs the machine's own lock contention.

    For the tests that need the file to actually arrive — Save and Export. It retries a genuine
    ``PermissionError`` **itself**, with real sleeps, rather than letting it reach `atomic_replace`.
    That matters more than it looks: `_no_real_sleeping` stubs the production backoff out, so its
    four retries would burn through in microseconds and a 50 ms antivirus lock would outlive the
    whole budget and surface as a failed save.
    """
    real = os.replace

    def replace(src, dst):
        calls.append(1)
        if len(calls) <= failures:
            raise PermissionError(5, "Access is denied")
        for attempt in range(40):  # ~2 s, far past any transient scanner handle
            try:
                return real(src, dst)
            except PermissionError:
                if attempt == 39:
                    raise
                _real_sleep(0.05)

    return replace


# ---- the retry policy -------------------------------------------------------


def test_replaces_on_the_first_try_when_nothing_is_locked(tmp_path, monkeypatch, _no_real_sleeping):
    """One attempt, no backoff, when the rename succeeds immediately.

    Injected rather than real (M130): asserting "no backoff was paid" against the actual filesystem
    makes the machine a participant, and on a box where a scanner can hold a just-written file this
    failed roughly one run in five — with `[0.05]` recorded, i.e. the retry doing its job.
    """
    calls: list[int] = []
    monkeypatch.setattr(atomic.os, "replace", _scripted_replace(0, calls))

    atomic_replace(tmp_path / "new", tmp_path / "target")

    assert len(calls) == 1
    assert _no_real_sleeping == []  # no backoff paid on the happy path


def test_a_real_replace_moves_the_file(tmp_path):
    """The bytes really arrive — the one test here that uses the actual filesystem.

    It deliberately asserts **nothing** about attempt counts or backoff. Those are the environment's
    to disturb; what must hold is that the destination ends up with the new content and the temp is
    gone, however many attempts that took.
    """
    src, dst = tmp_path / "new", tmp_path / "target"
    src.write_bytes(b"new")
    dst.write_bytes(b"old")

    atomic_replace(src, dst)

    assert dst.read_bytes() == b"new"
    assert not src.exists()


@pytest.mark.parametrize("failures", [1, 2, 3, 4])
def test_transient_lock_is_retried_until_it_clears(tmp_path, monkeypatch, failures):
    """The whole point: a lock that lets go within the budget must not fail the save."""
    calls: list[int] = []
    monkeypatch.setattr(atomic.os, "replace", _scripted_replace(failures, calls))

    atomic_replace(tmp_path / "new", tmp_path / "target")

    assert len(calls) == failures + 1


def test_persistent_lock_still_raises_after_a_bounded_number_of_attempts(tmp_path, monkeypatch):
    """A real permission problem must surface, and must not retry forever."""
    calls: list[int] = []
    monkeypatch.setattr(atomic.os, "replace", _scripted_replace(99, calls))

    with pytest.raises(PermissionError):
        atomic_replace(tmp_path / "new", tmp_path / "target")

    assert len(calls) == len(atomic._BACKOFF_SECONDS) + 1  # every delay, then one final attempt


def test_backoff_is_bounded_to_under_a_second(_no_real_sleeping, tmp_path, monkeypatch):
    monkeypatch.setattr(atomic.os, "replace", _scripted_replace(99, []))
    with pytest.raises(PermissionError):
        atomic_replace(tmp_path / "new", tmp_path / "target")
    assert sum(_no_real_sleeping) < 1.0


def test_other_errors_are_not_retried(tmp_path, monkeypatch, _no_real_sleeping):
    """Only lock contention is transient. A missing source is a bug, not a race — report it now."""
    calls: list[int] = []

    def missing(src, dst):
        calls.append(1)
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(atomic.os, "replace", missing)
    with pytest.raises(FileNotFoundError):
        atomic_replace(tmp_path / "new", tmp_path / "target")
    assert len(calls) == 1
    assert _no_real_sleeping == []


# ---- the user-facing case: Save and Export ----------------------------------


def test_save_succeeds_when_the_first_rename_is_locked(app, a_pdf, monkeypatch):
    """The flake as reported: full-suite runs hit a locked temp and got a "Save failed" modal.

    ``_no_real_modals`` (conftest) raises on ``QMessageBox.critical``, so if the retry regresses
    this test fails with that dialog's text rather than silently passing.
    """
    win = MainWindow(app, a_pdf, app.settings)
    win._delete_rows([1])
    monkeypatch.setattr(atomic.os, "replace", _moving_replace(2, []))

    assert win.save() is True
    assert not win.vdoc.dirty
    assert len(win.vdoc.ordered) == 2  # the deletion is on disk, not just in memory


def test_export_succeeds_when_the_first_rename_is_locked(app, a_pdf, tmp_path, monkeypatch):
    """The same rename, on the export path (``_export_pdf``) — one fix covers both call sites."""
    from PySide6.QtWidgets import QFileDialog

    out = tmp_path / "flat.pdf"
    win = MainWindow(app, a_pdf, app.settings)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    monkeypatch.setattr(atomic.os, "replace", _moving_replace(2, []))

    win._export_flattened_pdf()

    assert out.exists() and out.stat().st_size > 0
