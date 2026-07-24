"""Tests for browser provisioning — the three defects that made a session record lie.

Every one of these is a regression test for something found live on 2026-07-23 while trying to
start a session for the first control-panel drive. The shared theme: **a record must never assert
something it has not verified.** Chrome is never launched here; process discovery and CDP probing
are injected.
"""

import browser_provisioning as bp

PROFILE = "/tmp/agent-platform-training-chrome/persistent/indeed"


def _ps(*entries):
    """Fake `ps -axo pid=,command=` output. entries are (pid, user_data_dir, port|None)."""
    lines = []
    for pid, udd, port in entries:
        portflag = f" --remote-debugging-port={port}" if port else ""
        lines.append(f"{pid} /Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                     f"{portflag} --user-data-dir={udd} --no-first-run")
    lines.append("999 /usr/bin/some-unrelated-process --user-data-dir-is-not-a-flag")
    return lambda: lines


def _reachable(*ports):
    live = set(ports)
    return lambda port, timeout=1.5: port in live


# --- discovery -----------------------------------------------------------------------------
def test_find_chromes_reads_pid_dir_and_port():
    procs = bp.find_chromes(ps_lines=_ps((81915, PROFILE, 9328), (42, "/tmp/other", 9200)))
    assert {p.pid for p in procs} == {81915, 42}
    me = next(p for p in procs if p.pid == 81915)
    assert me.user_data_dir == PROFILE and me.debug_port == 9328


def test_find_chromes_filters_by_profile():
    procs = bp.find_chromes(user_data_dir=PROFILE,
                            ps_lines=_ps((81915, PROFILE, 9328), (42, "/tmp/other", 9200)))
    assert [p.pid for p in procs] == [81915]


def test_find_chromes_tolerates_a_trailing_slash():
    procs = bp.find_chromes(user_data_dir=PROFILE + "/",
                            ps_lines=_ps((81915, PROFILE, 9328)))
    assert [p.pid for p in procs] == [81915]


def test_find_chromes_handles_a_browser_with_no_debug_port():
    procs = bp.find_chromes(user_data_dir=PROFILE, ps_lines=_ps((7, PROFILE, None)))
    assert procs[0].debug_port is None


# --- defect 2: the guard must ask the machine, not the DB row -------------------------------
def test_profile_conflict_sees_a_live_browser_the_db_calls_stopped():
    """THE bug. Session 16's row said `stopped`; its Chrome was still serving on 9328 and still
    holding the profile lock, so the next launch silently attached to it."""
    holder = bp.profile_conflict(user_data_dir=PROFILE, ps_lines=_ps((81915, PROFILE, 9328)))
    assert holder is not None and holder.pid == 81915 and holder.debug_port == 9328


def test_a_chrome_with_a_dead_debug_port_still_holds_the_lock():
    """The lock belongs to the PROCESS, not the DevTools endpoint. An earlier draft required CDP
    to answer before calling something a conflict, which would wave through exactly the zombie
    that breaks the next launch — `ps` only lists live processes."""
    holder = bp.profile_conflict(user_data_dir=PROFILE, ps_lines=_ps((81915, PROFILE, None)))
    assert holder is not None and holder.pid == 81915


def test_profile_conflict_ignores_our_own_port():
    assert bp.profile_conflict(user_data_dir=PROFILE, exclude_port=9328,
                               ps_lines=_ps((81915, PROFILE, 9328))) is None


def test_profile_conflict_is_clear_when_nothing_holds_the_profile():
    assert bp.profile_conflict(user_data_dir=PROFILE,
                               ps_lines=_ps((42, "/tmp/other", 9200))) is None


# --- defect 1: stop must stop the BROWSER, and verify --------------------------------------
def test_stop_kills_the_real_browser_when_the_recorded_pid_is_a_dead_launcher(monkeypatch):
    """Session 16 recorded pid 58032 (long gone). The live browser was 81915. SIGTERMing only the
    recorded pid left the browser running and the profile locked."""
    killed = []
    dark = {"yet": False}

    def reachable(port, timeout=1.5):
        return port == 9328 and not dark["yet"]

    def fake_terminate(pid):
        killed.append(pid)
        if pid == 81915:
            dark["yet"] = True   # killing the REAL one is what frees the port and the profile

    monkeypatch.setattr(bp, "_terminate", fake_terminate)
    res = bp.stop_browser(port=9328, recorded_pid=58032, user_data_dir=PROFILE,
                          cdp_reachable=reachable, timeout_s=2.0, sleep=lambda _s: None,
                          ps_lines=lambda: [] if dark["yet"] else _ps((81915, PROFILE, 9328))())
    assert res.stopped is True
    assert 58032 in res.killed_pids and 81915 in res.killed_pids


def test_stop_verifies_the_profile_is_released_not_just_the_recorded_port(monkeypatch):
    """The second-order version of the same mistake. Session 18's recorded port 9322 was never
    alive, so a port-only check found it instantly 'dark' and reported a clean stop — while the
    browser genuinely holding the profile kept serving on 9328. Verify what the next launch
    actually needs: that the profile has been released."""
    monkeypatch.setattr(bp, "_terminate", lambda _pid: None)   # nothing actually dies
    res = bp.stop_browser(port=9322, recorded_pid=10514, user_data_dir=PROFILE,
                          cdp_reachable=_reachable(),          # 9322 is dark, as it always was
                          ps_lines=_ps((81915, PROFILE, 9328)),  # but 81915 still holds the dir
                          adopt_orphans=True, timeout_s=0.5, sleep=lambda _s: None)
    assert res.stopped is False
    assert "81915" in res.detail and "still holds" in res.detail


def test_stop_succeeds_once_the_profile_is_actually_free(monkeypatch):
    gone = {"yet": False}
    monkeypatch.setattr(bp, "_terminate", lambda _pid: gone.__setitem__("yet", True))
    res = bp.stop_browser(port=9322, recorded_pid=None, user_data_dir=PROFILE,
                          cdp_reachable=_reachable(), adopt_orphans=True,
                          ps_lines=lambda: [] if gone["yet"] else _ps((81915, PROFILE, 9328))(),
                          timeout_s=2.0, sleep=lambda _s: None)
    assert res.stopped is True and "profile released" in res.detail


def test_stop_reports_failure_when_the_browser_will_not_die(monkeypatch):
    """The honest-failure path. If the browser will not die we must NOT let the caller write
    `stopped` — that lie is what locked the profile in the first place."""
    monkeypatch.setattr(bp, "_terminate", lambda _pid: None)
    res = bp.stop_browser(port=9328, recorded_pid=58032, user_data_dir=PROFILE,
                          cdp_reachable=_reachable(9328), ps_lines=_ps((81915, PROFILE, 9328)),
                          timeout_s=0.5, sleep=lambda _s: None)
    assert res.stopped is False
    assert "did not stop" in res.detail and "keeps the profile locked" in res.detail


def test_stop_is_a_noop_when_nothing_was_running():
    res = bp.stop_browser(port=9328, recorded_pid=None, user_data_dir=PROFILE,
                          cdp_reachable=_reachable(), ps_lines=_ps(), sleep=lambda _s: None)
    assert res.stopped is True and res.killed_pids == []


def test_stop_leaves_another_sessions_browser_on_the_same_profile_alone(monkeypatch):
    """Two ports on one profile shouldn't happen, but if it does we only kill ours — unless the
    caller says we own the profile outright (see the orphan test below)."""
    killed = []
    monkeypatch.setattr(bp, "_terminate", lambda pid: killed.append(pid))
    bp.stop_browser(port=9328, recorded_pid=None, user_data_dir=PROFILE,
                    cdp_reachable=_reachable(), sleep=lambda _s: None, timeout_s=2.0,
                    ps_lines=lambda: ([] if 81915 in killed else _ps((81915, PROFILE, 9328))())
                                     + _ps((555, PROFILE, 9400))())
    assert 81915 in killed and 555 not in killed


def test_stop_adopts_an_orphan_holding_our_profile_on_an_unrecorded_port(monkeypatch):
    """The second live find. Session 18's row said port 9322; the browser actually holding its
    profile answered on 9328. A port-matched stop skipped the only process that mattered and then
    reported success — a clean stop over a browser that never died. When nothing else is live on
    the profile, the profile is the identity, not the port we wrote down."""
    killed = []
    monkeypatch.setattr(bp, "_terminate", lambda pid: killed.append(pid))
    res = bp.stop_browser(port=9322, recorded_pid=10514, user_data_dir=PROFILE,
                          cdp_reachable=_reachable(), sleep=lambda _s: None, timeout_s=2.0,
                          adopt_orphans=True,
                          ps_lines=lambda: [] if 81915 in killed else _ps((81915, PROFILE, 9328))())
    assert 81915 in killed and res.stopped is True


def test_without_adoption_the_orphan_is_left_alone(monkeypatch):
    """The guard that makes adoption safe: another session live on this profile keeps its browser."""
    killed = []
    monkeypatch.setattr(bp, "_terminate", lambda pid: killed.append(pid))
    bp.stop_browser(port=9322, recorded_pid=None, user_data_dir=PROFILE,
                    cdp_reachable=_reachable(), sleep=lambda _s: None,
                    adopt_orphans=False, ps_lines=_ps((81915, PROFILE, 9328)))
    assert killed == []


# --- defect 3: never record a port we have not probed ---------------------------------------
def test_await_debuggable_is_true_once_the_port_answers():
    calls = {"n": 0}

    def reachable(port, timeout=1.5):
        calls["n"] += 1
        return calls["n"] >= 3   # answers on the third poll

    assert bp.await_debuggable(9322, cdp_reachable=reachable, timeout_s=5.0,
                               sleep=lambda _s: None) is True


def test_await_debuggable_is_false_when_the_port_never_answers():
    """The 2026-07-23 case: Chrome handed off to the running instance and exited, so port 9322
    never existed. The old code ran this loop and returned the session anyway."""
    assert bp.await_debuggable(9322, cdp_reachable=_reachable(), timeout_s=0.5,
                               sleep=lambda _s: None) is False
