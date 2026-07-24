"""Browser provisioning — starting and stopping a session's Chrome, honestly.

WHY THIS EXISTS (found live 2026-07-23, debugging the first control-panel drive). Three defects
compounded into a session record that was simply false: it claimed `active` on port 9322 with pid
10514, and neither existed.

  1. **Stop killed a pid, not a browser.** `_stop_training_chrome` SIGTERMed the recorded pid and
     returned. That pid was a launcher that had already exited, so the real Chrome survived while
     the row said `stopped`.
  2. **The profile-conflict guard trusted the row, not the lock.** It only considered siblings with
     status in (active, starting). A row marked `stopped` whose browser was still alive slipped
     through — but a DB row does not unlock a directory, and Chrome locks a `--user-data-dir` to
     one live browser.
  3. **Launch recorded a port it never confirmed.** Chrome saw the profile already in use, handed
     the launch off to the running instance and exited. `Popen` returning is not a debuggable
     browser at that port.

The through-line is one rule, and it is the same rule the checkpoint ledger enforces on the other
side of the system: **never record as true something you have not verified.** `session_checkpoints`
marks a rung only on proof; provisioning must do the same for a port. The corresponding lesson is
already written down for `/execute` — `ok` there means "the mechanism completed", not "it worked".

Pure-ish and testable: process discovery and CDP probing go through small seams (`_ps_lines`,
`cdp_reachable`) so the whole thing is exercised without launching Chrome.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

# How long to wait for a launched browser to answer, and for a stopped one to go dark.
LAUNCH_TIMEOUT_S = 20.0
STOP_TIMEOUT_S = 8.0
_POLL_S = 0.4


def _ps_lines() -> list[str]:
    """Every running process as 'pid command…'. A seam so tests never shell out."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True,
                             timeout=5.0)
        return out.stdout.splitlines()
    except Exception:  # noqa: BLE001 — process discovery is best-effort, never fatal
        return []


def _flag_value(line: str, flag: str) -> Optional[str]:
    """Read `--flag=value` out of a command line, tolerating quoting and trailing args."""
    m = re.search(rf"{re.escape(flag)}=([^\s]+)", line)
    return m.group(1) if m else None


@dataclass(frozen=True)
class ChromeProcess:
    pid: int
    user_data_dir: str
    debug_port: Optional[int]


def find_chromes(*, user_data_dir: str = "", debug_port: Optional[int] = None,
                 ps_lines: Optional[Callable[[], list[str]]] = None) -> list[ChromeProcess]:
    """Live Chrome processes, optionally filtered to a profile dir and/or a debug port.

    This is the answer to "is anything actually holding this profile", which is the question the
    conflict guard should have been asking all along. A DB row cannot answer it.
    """
    lines = (ps_lines or _ps_lines)()
    want_dir = (user_data_dir or "").rstrip("/")
    found: list[ChromeProcess] = []
    for line in lines:
        if "--user-data-dir=" not in line:
            continue
        line_s = line.strip()
        pid_str, _, rest = line_s.partition(" ")
        if not pid_str.isdigit():
            continue
        udd = (_flag_value(rest, "--user-data-dir") or "").rstrip("/")
        if want_dir and udd != want_dir:
            continue
        port_s = _flag_value(rest, "--remote-debugging-port")
        port = int(port_s) if (port_s or "").isdigit() else None
        if debug_port is not None and port != debug_port:
            continue
        found.append(ChromeProcess(pid=int(pid_str), user_data_dir=udd, debug_port=port))
    return found


def _terminate(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


@dataclass
class StopResult:
    stopped: bool          # did the browser actually go dark?
    killed_pids: list[int]
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"stopped": self.stopped, "killed_pids": list(self.killed_pids),
                "detail": self.detail}


def stop_browser(*, port: Optional[int], recorded_pid: Optional[int], user_data_dir: str,
                 cdp_reachable: Callable[..., bool],
                 ps_lines: Optional[Callable[[], list[str]]] = None,
                 timeout_s: float = STOP_TIMEOUT_S,
                 sleep: Callable[[float], None] = time.sleep) -> StopResult:
    """Stop this session's browser and CONFIRM it is gone.

    Kills the recorded pid *and* any Chrome actually holding this profile/port — because the
    recorded pid is frequently a launcher that has already exited while the real browser lives on
    (the 2026-07-23 case). Then it **verifies the CDP port went dark** and reports honestly if it
    did not, so a caller can never mark a session `stopped` on a browser that is still serving.
    """
    holders = [p for p in find_chromes(user_data_dir=user_data_dir, ps_lines=ps_lines)
               if port is None or p.debug_port in (None, port)]
    if not holders and not recorded_pid and not (port and cdp_reachable(port)):
        return StopResult(True, [], "no browser was running")

    killed: list[int] = []
    if recorded_pid:
        _terminate(recorded_pid)
        killed.append(recorded_pid)

    # The real browser, found by what it is holding rather than by what we wrote down.
    for proc in holders:
        if proc.pid not in killed:
            _terminate(proc.pid)
            killed.append(proc.pid)

    if not port:
        return StopResult(True, killed, f"terminated {len(killed)} process(es); no port to verify")

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not cdp_reachable(port):
            return StopResult(True, killed, f"port {port} went dark")
        sleep(_POLL_S)
    return StopResult(False, killed,
                      f"port {port} is STILL answering after {timeout_s:.0f}s — the browser did "
                      f"not stop, so this session must not be recorded as stopped")


def profile_conflict(*, user_data_dir: str, exclude_port: Optional[int] = None,
                     ps_lines: Optional[Callable[[], list[str]]] = None
                     ) -> Optional[ChromeProcess]:
    """A live Chrome already holding this profile dir, if any — the real lock check.

    Chrome backs one `--user-data-dir` with exactly one browser; launching a second onto the same
    dir does not fail loudly, it silently hands off to the running instance and exits. That is why
    this must be checked *before* launching, and why checking DB rows is not enough.

    **The lock is held by a live PROCESS, not by a responsive debug port.** An earlier draft of
    this required `cdp_reachable` before calling something a conflict, which is wrong twice over:
    `ps` only lists processes that are alive, so anything it reports here is holding the directory;
    and a Chrome whose DevTools endpoint has died still owns the profile until the process exits.
    Gating on CDP would have waved through exactly the zombie that breaks the next launch.
    """
    for proc in find_chromes(user_data_dir=user_data_dir, ps_lines=ps_lines):
        if exclude_port is not None and proc.debug_port == exclude_port:
            continue
        return proc
    return None


def await_debuggable(port: int, *, cdp_reachable: Callable[..., bool],
                     timeout_s: float = LAUNCH_TIMEOUT_S,
                     sleep: Callable[[float], None] = time.sleep) -> bool:
    """Poll until the launched browser actually answers CDP. Returns False on timeout — and the
    caller MUST act on False. The previous code ran this exact loop and then returned the session
    regardless, which is how a session came to be `active` on a port that never existed."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cdp_reachable(port):
            return True
        sleep(_POLL_S)
    return cdp_reachable(port)
