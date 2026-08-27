"""SESSION 21 — session snapshots.

The load-bearing tests here are two: the one that pins the `cookie_ttl_s` scoping bug (because a
naive minimum reads RED on a healthy session, which is how this would ship broken and stay
broken), and the secret-boundary one the brief demands by name — **a snapshot id can reach a
journal row and its contents cannot**.

Storage isolation comes free from `conftest._isolate_observer_artifacts`: `_store_dir()` resolves
`settings.observer_artifacts_dir` per call, which is exactly the routing that conftest's own
docstring warns a new writer must have.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

import session_snapshot as ss

NOW = 1_756_300_000.0


def _c(name, domain, ttl_s=None, value="REDACTED-SECRET-VALUE"):
    """A cookie shaped like Chrome's. `expires` is absolute; -1 means a session cookie."""
    return {"name": name, "domain": domain, "value": value,
            "expires": -1 if ttl_s is None else NOW + ttl_s,
            "session": ttl_s is None, "httpOnly": True, "secure": True}


#: The Indeed jar as MEASURED live 2026-08-27, cut down to the cookies that matter to the bug:
#: an ad cookie expiring in 23 seconds sitting beside auth cookies ~79–365 days out.
INDEED_JAR = [
    _c("FPLC", ".indeed.com", 23),                 # Google ad cookie — the trap
    _c("__cf_bm", ".encserv.indeed.com", 6 * 60),  # Cloudflare bot-management — the second trap
    _c("_gid", ".indeed.com", 3 * 3600),
    _c("rememberMe", "secure.indeed.com", 1896 * 3600),
    _c("cf_clearance", ".indeed.com", 8757 * 3600),
    _c("PPID", ".indeed.com", 8759 * 3600),
    _c("JSESSIONID", "smartapply.indeed.com", None),  # session cookie: the apply flow's own
]

LINKEDIN_JAR = [
    _c("__cf_bm", ".linkedin.com", 24 * 60),
    _c("lidc", ".linkedin.com", 9 * 3600),
    _c("li_at", ".www.linkedin.com", 8694 * 3600),
    _c("bcookie", ".linkedin.com", 8743 * 3600),
]


# ---------------------------------------------------------------------------------------------
# The scoping bug, pinned
# ---------------------------------------------------------------------------------------------

def test_a_naive_minimum_over_the_jar_would_read_red_on_a_healthy_session():
    """This is the falsification, kept as a test so nobody re-derives `cookie_ttl_s` naively.

    `PLAN_staleness.md` §4 called landing this signal "a one-line change at the call site". It is
    not: the one-line version reads 23 SECONDS on a session `/auth_state` reports `logged_in:
    true`, against a RED threshold of 2 minutes. Always-red is worse than the inert `None` it
    replaces, because inert at least says "unmeasured" out loud.
    """
    naive = min(c["expires"] - NOW for c in INDEED_JAR if c["expires"] >= 0)
    assert naive == 23, "the measured trap: an ad cookie 23s from expiry in a healthy jar"

    scoped, found = ss.auth_ttl_s(INDEED_JAR, "indeed", now=NOW)
    assert scoped == pytest.approx(1896 * 3600), "the soonest AUTH cookie, ~79 days out"
    assert found == ["PPID", "cf_clearance", "rememberMe"]

    from perception import staleness as st
    red = st.THRESHOLDS["cookie_ttl_s"][2]
    assert naive < red < scoped, "naive trips RED; scoped does not — the whole point"


def test_the_linkedin_jar_scopes_the_same_way():
    ttl, found = ss.auth_ttl_s(LINKEDIN_JAR, "linkedin", now=NOW)
    assert ttl == pytest.approx(8694 * 3600)
    assert found == ["bcookie", "li_at"]


@pytest.mark.parametrize("platform", [None, "workday", "greenhouse", ""])
def test_an_unknown_platform_is_unmeasured_not_fresh(platform):
    """The `unprobed()` rule: a check we cannot perform has a defined, strict consequence.

    `None` flows into `staleness.Evidence.cookie_expires_at` as unmeasured, which the detector
    already renders correctly. A borrowed number would be a guess wearing a measurement's clothes.
    """
    assert ss.auth_ttl_s(INDEED_JAR, platform, now=NOW) == (None, [])


def test_a_jar_with_no_auth_cookie_is_unmeasured_rather_than_zero():
    """Logged out is not "TTL 0" — it is "no auth cookie was found". Different facts, and only
    `/auth_state` can tell us which; reporting 0 here would make the staleness signal assert a
    logout it never observed."""
    assert ss.auth_ttl_s([_c("FPLC", ".indeed.com", 23)], "indeed", now=NOW) == (None, [])


def test_a_session_only_auth_cookie_is_present_but_contributes_no_ttl():
    jar = [_c("li_at", ".www.linkedin.com", None)]
    ttl, found = ss.auth_ttl_s(jar, "linkedin", now=NOW)
    assert found == ["li_at"] and ttl is None, "present and permanent are different from absent"


def test_the_platform_is_read_off_the_jar_not_asserted_by_a_caller():
    """`upsert_observed_jobs`' rule, one layer down: the page is the authority. A caller's wrong
    guess would file a LinkedIn identity under `indeed` and restore it onto the wrong browser."""
    assert ss._platform_of_jar(INDEED_JAR) == "indeed"
    assert ss._platform_of_jar(LINKEDIN_JAR) == "linkedin"
    assert ss._platform_of_jar([_c("x", ".workday.com", 60)]) is None


# ---------------------------------------------------------------------------------------------
# A fake browser
# ---------------------------------------------------------------------------------------------

class _FakeWS:
    """Speaks just enough CDP: request/response by id, over an in-memory queue."""

    def __init__(self, responses: dict, log: list):
        self._responses, self._log, self._out = responses, log, []

    async def send(self, raw: str) -> None:
        msg = json.loads(raw)
        self._log.append(msg["method"])
        result = self._responses.get(msg["method"], {})
        if callable(result):
            result = result(msg.get("params") or {})
        self._out.append(json.dumps({"id": msg["id"], "result": result}))

    async def recv(self) -> str:
        return self._out.pop(0)

    async def close(self) -> None:
        pass


def _browser(jar, *, tabs=("https://www.indeed.com/?vjk=abc",), log=None):
    log = [] if log is None else log
    state = {"cookies": list(jar)}

    def _set(params):
        state["cookies"] = list(params.get("cookies") or [])
        return {}

    responses = {
        "Storage.getCookies": lambda _p: {"cookies": state["cookies"]},
        "Storage.clearCookies": lambda _p: state.update(cookies=[]) or {},
        "Storage.setCookies": _set,
        "Target.getTargets": {"targetInfos": [{"type": "page", "url": u} for u in tabs]},
        "Runtime.evaluate": {"result": {"value": {
            "origin": "https://www.indeed.com",
            "local": {"wizard_step": "3", "token": "REDACTED-SECRET-VALUE"},
            "session": {}}}},
    }

    async def connect(_ws_url):
        return _FakeWS(responses, log)

    def http_json(url):
        if url.endswith("/json/version"):
            return {"Browser": "Chrome/151.0.7922.172",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9322/devtools/browser/fake"}
        return [{"type": "page", "url": u,
                 "webSocketDebuggerUrl": f"ws://127.0.0.1:9322/devtools/page/{i}"}
                for i, u in enumerate(tabs)]

    return connect, http_json, state, log


# ---------------------------------------------------------------------------------------------
# The secret boundary — the test the brief asks for by name
# ---------------------------------------------------------------------------------------------

def test_the_id_can_reach_a_journal_and_the_contents_cannot():
    """A cookie jar is a bearer credential, strictly stronger than the vault password it bypasses.

    So the shape that LEAVES this module — the one a journal row, the DB, an API response and the
    cockpit all see — must carry the id and the verdict and nothing else. This asserts on the
    serialized form, because that is what actually gets written somewhere.
    """
    connect, http_json, _state, _log = _browser(INDEED_JAR)
    meta = asyncio.run(ss.capture_warm(port=9322, profile="indeed",
                                       connect=connect, http_json=http_json))

    rendered = json.dumps(meta.as_dict())
    assert "REDACTED-SECRET-VALUE" not in rendered, "a cookie value reached the public record"
    for key in ("value", "cookies", "storage", "local"):
        assert f'"{key}"' not in rendered, f"{key} must not be part of the public shape"

    # ...and the id IS there, because the journal is supposed to know a snapshot was taken.
    assert meta.id in rendered and meta.id.startswith("indeed-")
    assert meta.cookie_count == len(INDEED_JAR) and meta.auth_ttl_s is not None


def test_the_payload_on_disk_is_ciphertext():
    connect, http_json, _s, _l = _browser(INDEED_JAR)
    meta = asyncio.run(ss.capture_warm(port=9322, profile="indeed",
                                       connect=connect, http_json=http_json))
    raw = ss._blob_path(meta.id).read_text(encoding="utf-8")
    assert "REDACTED-SECRET-VALUE" not in raw and "PPID" not in raw
    assert ss._read_blob(meta.id)["cookies"], "…and it decrypts back for a restore"


def test_no_temp_file_survives_a_write():
    """The write is atomic (`os.replace`) because `apply_state_store.save`'s plain `write_text` is
    fine for a blackboard that can be rebuilt and not fine for the only copy of a login."""
    connect, http_json, _s, _l = _browser(LINKEDIN_JAR)
    meta = asyncio.run(ss.capture_warm(port=9323, profile="linkedin",
                                       connect=connect, http_json=http_json))
    leftovers = list(ss._store_dir().glob("*.tmp"))
    assert not leftovers, f"a torn write left {leftovers}"
    assert ss._blob_path(meta.id).exists()


# ---------------------------------------------------------------------------------------------
# Capture / restore
# ---------------------------------------------------------------------------------------------

def test_capture_names_what_it_did_not_take():
    """The brief's DoD: the warm tier's GAP is the finding, and the next reader should not have to
    rediscover it. A restore that silently omits a class of state is the false success this repo
    keeps paying for."""
    connect, http_json, _s, _l = _browser(INDEED_JAR)
    meta = asyncio.run(ss.capture_warm(port=9322, profile="indeed",
                                       connect=connect, http_json=http_json))
    assert meta.not_captured, "the tier must say what it misses"
    assert any("IndexedDB" in x for x in meta.not_captured)
    assert any("no open tab" in x.lower() for x in meta.not_captured)
    assert meta.origins_with_storage == ("https://www.indeed.com",)
    assert meta.session_cookie_count == 1, "the smartapply JSESSIONID"


def test_restore_puts_the_jar_back_and_reports_a_shortfall_rather_than_a_silent_ok():
    connect, http_json, state, log = _browser(INDEED_JAR)
    meta = asyncio.run(ss.capture_warm(port=9322, profile="indeed",
                                       connect=connect, http_json=http_json))
    state["cookies"] = []  # the session went stale: everything gone
    out = asyncio.run(ss.restore_warm(port=9322, snapshot_id=meta.id,
                                      connect=connect, http_json=http_json))
    assert out["ok"] and out["cookies_written"] == len(INDEED_JAR)
    assert out["shortfall"] == 0 and len(state["cookies"]) == len(INDEED_JAR)
    assert "Storage.clearCookies" in log and "Storage.setCookies" in log


def test_restore_proposes_tabs_and_does_not_open_them():
    """§3: states are reached by clicking, and a recovery is not a licence to URL-jump an
    account's origins unbidden. `PLAN_staleness.md` also forbids acting on a verdict while the
    thresholds are guesses. Offer the remedy; the operator presses."""
    connect, http_json, _s, log = _browser(INDEED_JAR, tabs=("https://www.indeed.com/?vjk=abc",))
    meta = asyncio.run(ss.capture_warm(port=9322, profile="indeed",
                                       connect=connect, http_json=http_json))
    log.clear()
    out = asyncio.run(ss.restore_warm(port=9322, snapshot_id=meta.id,
                                      connect=connect, http_json=http_json))
    assert out["tabs_proposed"] == ["https://www.indeed.com/?vjk=abc"]
    assert not any(m.startswith("Target.create") or m == "Page.navigate" for m in log)
    assert out["storage_not_restored"] == ["https://www.indeed.com"], "named, not silently dropped"


def test_restoring_a_missing_snapshot_refuses_rather_than_claiming_ok():
    connect, http_json, _s, _l = _browser(INDEED_JAR)
    out = asyncio.run(ss.restore_warm(port=9322, snapshot_id="nope-000000",
                                      connect=connect, http_json=http_json))
    assert out["ok"] is False and "no readable payload" in out["detail"]


def test_a_restore_never_grades_itself():
    connect, http_json, _s, _l = _browser(LINKEDIN_JAR)
    meta = asyncio.run(ss.capture_warm(port=9323, profile="linkedin",
                                       connect=connect, http_json=http_json))
    out = asyncio.run(ss.restore_warm(port=9323, snapshot_id=meta.id,
                                      connect=connect, http_json=http_json))
    assert out["verified"] is None, "a local restore is not evidence the SERVER honours it"


# ---------------------------------------------------------------------------------------------
# Verification verdicts
# ---------------------------------------------------------------------------------------------

def test_the_three_verdicts_are_distinct():
    assert ss.verify_verdict({"ok": True, "logged_in": True}) == ss.RESTORED_AUTHENTICATED
    assert ss.verify_verdict({"ok": True, "logged_in": False}) == ss.RESTORED_LOGGED_OUT
    # The probe covers indeed + linkedin only; every other host falls into its `except`.
    assert ss.verify_verdict({"ok": False, "detail": "no target"}) == ss.RESTORED_UNVERIFIED


def test_an_unprobed_restore_is_not_reported_as_logged_out():
    """Two ways to get this wrong, and both cost something: calling unverified "authenticated"
    is the false success; calling it "logged out" sends the operator to re-login a session that
    was fine."""
    v = ss.verify_verdict({"ok": False})
    assert v != ss.RESTORED_LOGGED_OUT and v != ss.RESTORED_AUTHENTICATED


def test_verification_is_recorded_against_the_snapshot():
    connect, http_json, _s, _l = _browser(LINKEDIN_JAR)
    meta = asyncio.run(ss.capture_warm(port=9323, profile="linkedin",
                                       connect=connect, http_json=http_json))
    assert meta.verified is None, "capture never claims a verdict it did not get"
    updated = ss.record_verification(meta.id, ss.RESTORED_AUTHENTICATED)
    assert updated.verified == ss.RESTORED_AUTHENTICATED
    assert ss.get_meta(meta.id).verified == ss.RESTORED_AUTHENTICATED


# ---------------------------------------------------------------------------------------------
# Retention — enforced in code, because a policy in prose is how 433 MB got here unnoticed
# ---------------------------------------------------------------------------------------------

def _fake_capture(profile, connect, http_json, n):
    out = []
    for _ in range(n):
        out.append(asyncio.run(ss.capture_warm(port=9322, profile=profile,
                                               connect=connect, http_json=http_json)))
        time.sleep(0.001)  # ids and ordering key off the clock
    return out


def test_retention_keeps_n_and_deletes_the_blobs():
    connect, http_json, _s, _l = _browser(INDEED_JAR)
    made = _fake_capture("retention_probe", connect, http_json, 4)
    swept = ss.enforce_retention("retention_probe", keep=2)
    assert len(swept) == 2
    left = ss.list_snapshots("retention_probe")
    assert [m.id for m in left] == [made[3].id, made[2].id], "newest kept"
    for sid in swept:
        assert not ss._blob_path(sid).exists(), "retention must remove the CREDENTIAL, not just the row"


def test_a_pinned_snapshot_is_a_fixture_and_survives_the_sweep():
    """"Stale sessions are fixtures" given a mechanism: a snapshot of a BROKEN state is a
    regression test for recovery, and a sweep that eats it destroys the experiment."""
    connect, http_json, _s, _l = _browser(INDEED_JAR)
    made = _fake_capture("pin_probe", connect, http_json, 4)
    ss.set_pinned(made[0].id, True)
    ss.enforce_retention("pin_probe", keep=1)
    ids = {m.id for m in ss.list_snapshots("pin_probe")}
    assert made[0].id in ids and made[3].id in ids
    assert ss._blob_path(made[0].id).exists()


def test_retention_is_scoped_to_one_profile():
    connect, http_json, _s, _l = _browser(INDEED_JAR)
    _fake_capture("prof_a", connect, http_json, 3)
    b = _fake_capture("prof_b", connect, http_json, 2)
    ss.enforce_retention("prof_a", keep=1)
    assert len(ss.list_snapshots("prof_b")) == 2 and {m.id for m in ss.list_snapshots("prof_b")} == {
        b[0].id, b[1].id}


# ---------------------------------------------------------------------------------------------
# The cold tier's one measured exclusion
# ---------------------------------------------------------------------------------------------

def test_the_cold_tier_excludes_service_worker_cachestorage():
    """Measured 2026-08-27: `Service Worker/CacheStorage` is 38.3 MB of Indeed's profile. Include
    it and the "~2 MB identity tier" that justified having a cheap tier at all becomes 39.6 MB of
    regenerable cache. The exclusion IS the tier."""
    joined = " ".join(ss.COLD_IDENTITY_PATHS)
    assert "CacheStorage" not in joined
    assert "Service Worker/Database" in joined, "the SW registration DB is identity; its cache is not"
    assert "Default/Cookies" in joined and "Default/Login Data" in joined
