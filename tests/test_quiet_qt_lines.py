"""Qt's console lines known to be harmless on Wayland are dropped, and no others (M153).

On WSL, clicking an open menu's title again printed "This plugin supports grabbing the mouse only
for popup windows" twice. ``platform_integration.quiet_harmless_qt_lines`` drops that exact line
on Wayland, and prints every other Qt message as before. See ``PLAN.md`` §M153.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import qInstallMessageHandler, qWarning

import platform_integration
from app import PdfApp

MENU_BAR_LINE = "This plugin supports grabbing the mouse only for popup windows"


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def clean_slate(qapp, monkeypatch):
    """Start from Qt's own printer, and put back whatever was there before."""
    before = qInstallMessageHandler(None)
    monkeypatch.setattr(platform_integration, "_passed_on", None)
    yield
    qInstallMessageHandler(before)


def test_the_menu_bar_line_is_dropped_and_other_lines_still_print(clean_slate, capsys):
    platform_integration._install_line_filter()

    qWarning(MENU_BAR_LINE)
    qWarning("some other warning")

    err = capsys.readouterr().err
    assert MENU_BAR_LINE not in err
    assert "some other warning" in err


def test_only_the_whole_line_is_dropped(clean_slate, capsys):
    platform_integration._install_line_filter()

    qWarning(MENU_BAR_LINE + " (and more)")

    assert MENU_BAR_LINE + " (and more)" in capsys.readouterr().err


def test_other_lines_reach_a_filter_that_was_there_before(clean_slate):
    seen = []
    qInstallMessageHandler(lambda msg_type, context, message: seen.append(message))
    platform_integration._install_line_filter()

    qWarning(MENU_BAR_LINE)
    qWarning("some other warning")

    assert seen == ["some other warning"]


def test_a_second_install_still_prints_a_line_once(clean_slate, capsys):
    platform_integration._install_line_filter()
    platform_integration._install_line_filter()

    qWarning("some other warning")

    assert capsys.readouterr().err.count("some other warning") == 1


@pytest.mark.parametrize("platform, on", [
    ("wayland", True), ("offscreen", False), ("windows", False), ("xcb", False),
])
def test_the_filter_is_on_only_under_wayland(clean_slate, platform, on):
    app = SimpleNamespace(platformName=lambda: platform)

    assert platform_integration.quiet_harmless_qt_lines(app) is on

    installed = qInstallMessageHandler(None)
    assert (installed is platform_integration._filter_qt_line) is on
