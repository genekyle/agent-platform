"""The OS layer — browser chrome, the surface CDP cannot reach.

Pinned here: the identity rule. Everything else about this layer is a live-desktop behaviour, but
"which process do we type into" is pure, and it is the part that goes badly wrong — a keystroke
addressed by app name landed in a DIFFERENT Chrome showing the operator's own browsing.
"""

from __future__ import annotations

from app.main_server import _DIALOG_WINDOW_HINT, _pid_from_ps

PS = """\
  501 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
20449 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9322 --user-data-dir=/tmp/agent-platform-training-chrome/persistent/indeed
31968 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9327 --user-data-dir=/tmp/agent-platform-training-chrome/persistent/linkedin
  777 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome Helper (Renderer)
"""


def test_the_pid_comes_from_the_debug_port_not_from_the_app_name():
    """Three Chromes were running when this mattered. The port is the only identity we own."""
    assert _pid_from_ps(PS, 9322) == 20449
    assert _pid_from_ps(PS, 9327) == 31968


def test_an_unowned_port_resolves_to_nothing_rather_than_a_guess():
    """No match must mean NO PID. Falling back to 'the first Chrome' is how you type into the
    operator's personal window."""
    assert _pid_from_ps(PS, 9999) is None
    assert _pid_from_ps("", 9322) is None


def test_a_chrome_with_no_debug_port_is_never_selected():
    """The plain Chrome on line 1 is the operator's. It has no port and must stay invisible here."""
    assert _pid_from_ps(PS, 501) is None


def test_the_dialog_window_hint_matches_chromes_title_shape():
    """Chrome titles a JS dialog window '<host> says' — the only readable thing about it, since
    its contents return empty for AXRole/AXTitle/AXDescription."""
    assert _DIALOG_WINDOW_HINT in "jobs.teradyne.com says"
    assert _DIALOG_WINDOW_HINT not in (
        "Pricing / Marketing Operations Analyst (Teradyne, N. Reading MA) - Google Chrome")
