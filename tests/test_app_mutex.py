"""The named mutex that stops Setup/uninstall running while KlarPDF is open (RELEASE.md, v0.10.1).

Two artifacts appeared when v0.10.0 was uninstalled with the app running — neither a packaging bug,
both avoidable: Windows will not delete a running ``.exe`` (and a per-user install cannot queue a
reboot-time delete), and ``[UninstallDelete]`` wiped ``%LOCALAPPDATA%\\klarpdf`` only for the live
process to write ``view_state.json`` on shutdown and recreate it.

Inno's ``AppMutex`` closes both, but only if the app actually holds a mutex under the name the
``.iss`` watches. The single-instance guard cannot serve: it is a ``QLocalServer`` named pipe, which
Inno cannot see.

These tests are mostly about *drift*. The mutex is a contract between a Python constant and an Inno
directive, with no compiler and no runtime error to catch a rename — the guard would simply stop
working, silently, and the next uninstall would leave the same mess.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

import platform_integration as pi

ISS = Path(__file__).resolve().parents[1] / "packaging" / "installer.iss"


def _iss_define(name: str) -> str:
    """Value of a `#define <name> "<value>"` in the .iss."""
    m = re.search(rf'^#define\s+{re.escape(name)}\s+"([^"]+)"', ISS.read_text(encoding="utf-8"), re.M)
    assert m, f"#define {name} not found in {ISS.name}"
    return m.group(1)


def _iss_setup_directive(name: str) -> str:
    """Value of a `Name=Value` line in the [Setup] section (comments and blanks skipped)."""
    text = ISS.read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(name)}=(.+)$", text, re.M)
    assert m, f"[Setup] directive {name} not found in {ISS.name}"
    return m.group(1).strip()


# --- the contract -------------------------------------------------------------------------------


def test_mutex_name_matches_the_installer():
    """The whole guard is this one string agreeing in two files."""
    assert _iss_define("MyAppMutex") == pi.APP_MUTEX_NAME


def test_installer_declares_appmutex_so_setup_and_uninstall_both_check_it():
    assert _iss_setup_directive("AppMutex") == "{#MyAppMutex}"


def test_installer_refuses_rather_than_force_closing_the_app():
    """KlarPDF prompts on unsaved edits; Restart Manager would close it past that prompt."""
    assert _iss_setup_directive("CloseApplications") == "no"
    assert _iss_setup_directive("RestartApplications") == "no"


def test_setup_mutex_prevents_two_installers_at_once():
    assert _iss_setup_directive("SetupMutex").startswith("{#MyAppMutex}")


def test_mutex_is_session_scoped_not_global():
    """A per-user install: one user's running app must not block another user's installer."""
    assert not pi.APP_MUTEX_NAME.startswith("Global\\")
    assert "\\" not in pi.APP_MUTEX_NAME  # a backslash would name a different kernel namespace


# --- who takes it -------------------------------------------------------------------------------


def test_only_a_launch_that_stays_alive_takes_the_mutex():
    """A forwarding launch exits in milliseconds; taking the mutex there would briefly unlock Setup.

    Reads the source rather than driving it: the ordering is what matters, and the two hand-off
    returns above the call are exactly what a careless refactor would move.
    """
    src = Path(pi.__file__).parent.joinpath("launcher.py").read_text(encoding="utf-8")
    body = src[src.index("def main("):]
    mutex_at = body.index("acquire_app_mutex()")
    # Both hand-off `return 0` paths must precede the acquire.
    handoffs = [m.start() for m in re.finditer(r"send_path_to_running_instance\(name, raw_path\)", body)]
    assert len(handoffs) == 2, "expected two hand-off paths in launcher.main"
    for pos in handoffs:
        assert pos < mutex_at, "acquire_app_mutex() must come after every forwarding return"


# --- behaviour ----------------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="the mutex is a Windows kernel object")
def test_acquire_is_idempotent_and_holds_a_handle():
    assert pi.acquire_app_mutex() is True
    handle = pi._app_mutex_handle
    assert handle
    assert pi.acquire_app_mutex() is True  # second call must not leak a second handle
    assert pi._app_mutex_handle == handle


@pytest.mark.skipif(sys.platform != "win32", reason="the mutex is a Windows kernel object")
def test_the_mutex_is_visible_to_another_opener_which_is_what_inno_does():
    """Inno tests for *existence*, so prove an unrelated OpenMutexW finds ours."""
    import ctypes
    from ctypes import wintypes

    pi.acquire_app_mutex()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    SYNCHRONIZE = 0x00100000
    found = kernel32.OpenMutexW(SYNCHRONIZE, False, pi.APP_MUTEX_NAME)
    assert found, "Inno's AppMutex check would not see our mutex"
    ctypes.WinDLL("kernel32").CloseHandle(found)

    missing = kernel32.OpenMutexW(SYNCHRONIZE, False, pi.APP_MUTEX_NAME + "-nope")
    assert not missing, "sanity: a name we never created must not resolve"


@pytest.mark.skipif(sys.platform == "win32", reason="off-Windows it must be an inert no-op")
def test_acquire_is_a_no_op_off_windows():
    assert pi.acquire_app_mutex() is False
    assert pi._app_mutex_handle is None
