"""Session snapshots — a signed-in browser identity, captured whole and restorable.

SESSION 21. The signed-in Indeed and LinkedIn profiles live in
`/tmp/agent-platform-training-chrome/persistent/<name>` (`settings.training_chrome_profiles_dir`,
never overridden) and macOS clears `/tmp` on reboot. The logins that cost a HUMAN to re-create —
2FA, checkpoints, the lot — have had no copy anywhere. LEARNINGS recorded that risk on 2026-08-18
and nothing acted on it.

But this is a LEARNING feature, not an ops convenience. Today a stale session is an *incident*:
it happens once, someone improvises, the lesson is prose. With a restore it is an *experiment* —
freeze the broken state, try a recovery, measure it, restore, try another. Three things follow
that the repo could not reach before:

  * `PLAN_staleness.md`'s **RENEW verdict gets its first remedy**. Until now its only answer was
    "a human logs in again".
  * **`cookie_ttl_s` stops being inert** — see `auth_ttl_s` below, and the measurement that
    corrected the plan's estimate of what landing it would cost.
  * The staleness research (§3 of that plan: *at what value does the next action's failure rate
    rise?*) becomes a bench experiment instead of a months-long wait for natural decay.

This is "stale sessions are fixtures" given a mechanism: **a snapshot of a BROKEN state is a
regression test for recovery.**

## The secret boundary, which is the strictest constraint here

A cookie jar is a **bearer credential, strictly more powerful than the vault password it
bypasses** — it carries 2FA and checkpoint state with it. So:

  * the payload is encrypted with the vault's own key provider and stored BESIDE the vault, never
    inside it (see `_blob_path` for why not inside), and never in the DB;
  * it never enters the transition corpus, a capture artifact, a screenshot, an intent-journal
    row, a log line, or LEARNINGS. `SnapshotMeta` is the ONLY shape that leaves this module, and
    `test_session_snapshot` proves an id can reach a journal row and the contents cannot;
  * a snapshot is journaled as *taken* / *restored*, with its id and verification verdict and
    nothing else — the `errand.login_code` precedent, where the journal knows a code was read and
    never the code.

## Why this talks CDP directly, when the mcp server owns the browser

Every other browser read in `controlplane-api` goes out over HTTP to the capture server. This one
does not, deliberately: routing a cookie jar through a second process makes it an HTTP body and a
candidate for that process's request logging, in exchange for nothing. The payload stays inside
one process and is written encrypted. That is the boundary buying the exception, and it is the
only reason for it.

## Scope is a PROFILE, not a session

`_profile_dir_for` (main.py:154) resolves `persistent_profile` → `<root>/persistent/<slug>`, and
that directory is shared by every session on that account. A snapshot names a profile and a
moment, never a session id — otherwise two sessions on one account each think they own the
restore.

## Not Chrome's own session restore

`controller/window.plan_fresh_start` exists because Chrome's automatic restore drags back
half-finished apply forms; that hazard is unchanged. This is a *deliberate, addressed, verified*
restore of an identity, which is a different thing, and the next reader should not read this
module as a reversal of that decision.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------------------------
# What carries identity, measured — and what the brief's estimate got wrong
# ---------------------------------------------------------------------------------------------

#: The WARM tier: read live over CDP from a running browser. Chrome holds Cookies in a WAL-backed
#: SQLite file, so copying that file under a running browser can read torn state — the live read is
#: complete, consistent, and needs no downtime.
#:
#: Measured 2026-08-27 on the two live profiles:
#:   indeed   771 cookies / 253 KB across 269 domains (mostly ad-tech)
#:   linkedin  79 cookies /  27 KB across  20 domains
#:
#: The COLD tier (a copy of the whole `user-data-dir`) is for a browser that is already stopped.
#: **The two tiers are NOT ordered**, which corrects the brief: Indeed carries 16 first-party
#: SESSION cookies — four `JSESSIONID`s (one on `smartapply.indeed.com`, the apply flow's own
#: session) and five CSRF tokens — and those are exactly the mid-apply state. The warm read
#: returns them; whether a cold file copy carries them is UNVERIFIED. Never describe cold-full as
#: a superset of warm until somebody measures that.
TIERS = ("identity_warm", "cold_full")

#: Identity-tier files, for the cold path. **`Service Worker/CacheStorage` is deliberately absent**
#: and that exclusion is the whole point: measured 2026-08-27, it is 38.3 MB of Indeed's profile,
#: which turns a "~2 MB identity tier" into 39.6 MB of regenerable cache. With it excluded the
#: real figures are indeed ~1.2 MB and linkedin ~2.5 MB — cheap enough for many generations, which
#: was the design argument for the tier in the first place.
COLD_IDENTITY_PATHS = (
    "Default/Cookies",
    "Default/Local Storage",
    "Default/Session Storage",
    "Default/Login Data",
    "Default/IndexedDB",
    "Default/Service Worker/Database",
    "Default/Preferences",
    "Local State",
)

# ---------------------------------------------------------------------------------------------
# The auth cookies — and the plan claim they falsify
# ---------------------------------------------------------------------------------------------

#: The cookies that actually carry the login, per platform. Measured live 2026-08-27; a world-fact
#: (§14), not a code-fact — these rot when the SITE changes, with no line of this repo moving.
#:
#: **THIS LIST IS WHY `cookie_ttl_s` WAS NOT "a one-line change at the call site"**, which is what
#: `PLAN_staleness.md` §4 has claimed since 2026-07-26. Measured on a session `/auth_state` reports
#: `logged_in: true`:
#:
#:     min TTL over the whole jar (indeed)   ->      23 s   `FPLC`, a Google ad cookie
#:     min TTL over first-party only         ->    ~6 min   `__cf_bm`, Cloudflare bot-management
#:     min TTL over the AUTH cookies below   -> ~1896 h     `rememberMe`
#:
#: `THRESHOLDS["cookie_ttl_s"]` is `(30 min, 10 min, 2 min)`. So the naive minimum — over the jar
#: OR over first-party — reads **RED, permanently, on a demonstrably healthy session**. A signal
#: that is always red is worse than one that is always None: the inert version at least says
#: "unmeasured" out loud. Scope is the whole feature.
AUTH_COOKIES: dict[str, frozenset[str]] = {
    # `PPID` + `cf_clearance` are the long-lived pair; `rememberMe` (secure.indeed.com) is the one
    # that actually survives a browser restart, and it is the shortest of the three at ~79 days.
    "indeed": frozenset({"PPID", "cf_clearance", "rememberMe",
                         "__Secure-PassportAuthProxy-RefreshToken"}),
    # `li_at` is the session; `bcookie`/`bscookie` are the browser identity LinkedIn checks
    # alongside it. `liap` marks the authenticated-app state.
    "linkedin": frozenset({"li_at", "bcookie", "bscookie", "liap"}),
}

#: Hosts that decide which platform a jar belongs to. Same two platforms `_AUTH_JS_BY_PLATFORM`
#: (main_server.py:5697) covers, and for the same reason: a restore on a third profile must report
#: `unverified` rather than borrow a verdict it never got.
_PLATFORM_HOSTS = {"indeed": ("indeed.com",), "linkedin": ("linkedin.com",)}

#: Retention. Snapshots of a live login do not expire on their own and an unbounded store of
#: bearer credentials is the worst thing to leave growing quietly. Pinned ones are recovery
#: fixtures and never swept.
KEEP_PER_PROFILE = 10


def platform_of_profile(profile: str) -> Optional[str]:
    """Which platform's auth vocabulary applies to this profile, or None if we have none.

    None is a real answer with a defined consequence (`ActuationReach.unprobed()`): a snapshot on
    an unknown profile reports `unverified`, never a borrowed verdict.
    """
    p = (profile or "").strip().lower()
    return p if p in AUTH_COOKIES else None


def auth_ttl_s(cookies: list[dict[str, Any]], platform: Optional[str],
               *, now: Optional[float] = None) -> tuple[Optional[float], list[str]]:
    """Seconds until the soonest-expiring AUTH cookie — the honest `cookie_ttl_s`.

    Returns `(None, [])` when we cannot measure it: an unknown platform, or not one named auth
    cookie present. That is the strict-consequence rule, not a shrug — `None` flows into
    `staleness.Evidence.cookie_expires_at` as "unmeasured", which the detector already renders
    correctly. Returning a number we did not earn is how a guessed threshold becomes a gate.

    A session-only auth cookie (no expiry) contributes no TTL but DOES count as present, because
    its absence and its permanence are different facts.
    """
    names = AUTH_COOKIES.get(platform or "")
    if not names:
        return None, []
    now = time.time() if now is None else now
    found: list[str] = []
    ttls: list[float] = []
    for c in cookies:
        name = c.get("name")
        if name not in names:
            continue
        found.append(name)
        exp = c.get("expires", -1)
        if exp is not None and exp >= 0:
            ttls.append(max(0.0, float(exp) - now))
    if not found:
        return None, []
    return (min(ttls) if ttls else None), sorted(set(found))


# ---------------------------------------------------------------------------------------------
# The record that leaves this module
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SnapshotMeta:
    """Everything a caller, a journal row, the DB or the cockpit may see.

    Deliberately holds no cookie, no storage value, no tab URL query string that could carry a
    token — `tabs` keeps the URL because a restore proposal needs it, truncated the way
    `close_out` already truncates `tabs_at_close`.
    """

    id: str
    profile: str
    tier: str
    taken_at: float
    platform: Optional[str] = None
    chrome: str = ""
    cookie_count: int = 0
    domain_count: int = 0
    session_cookie_count: int = 0
    auth_cookies_found: tuple[str, ...] = ()
    auth_ttl_s: Optional[float] = None
    origins_with_storage: tuple[str, ...] = ()
    tabs: tuple[str, ...] = ()
    #: What this tier DID and DID NOT take. The brief's DoD asks for the gap by name, because a
    #: restore that silently omits a class of state is the false success this repo keeps paying
    #: for. `not_captured` is a claim about the TIER, not about this run.
    captured: tuple[str, ...] = ()
    not_captured: tuple[str, ...] = ()
    pinned: bool = False
    note: str = ""
    #: Set by `verify_restore`, never by capture. `None` means nobody has checked.
    verified: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id, "profile": self.profile, "tier": self.tier,
            "taken_at": self.taken_at, "platform": self.platform, "chrome": self.chrome,
            "cookie_count": self.cookie_count, "domain_count": self.domain_count,
            "session_cookie_count": self.session_cookie_count,
            "auth_cookies_found": list(self.auth_cookies_found),
            "auth_ttl_s": self.auth_ttl_s,
            "auth_ttl_h": None if self.auth_ttl_s is None else round(self.auth_ttl_s / 3600, 1),
            "origins_with_storage": list(self.origins_with_storage),
            "tabs": list(self.tabs),
            "captured": list(self.captured), "not_captured": list(self.not_captured),
            "pinned": self.pinned, "note": self.note, "verified": self.verified,
        }
        return d


#: The three verification verdicts. A restore that lands on a login wall must say so — a perfect
#: LOCAL restore does not mean the SERVER still honours the session, and `PLAN_staleness.md`
#: already warns that nothing we measure locally sees a server-side expiry.
RESTORED_AUTHENTICATED = "restored_and_authenticated"
RESTORED_LOGGED_OUT = "restored_but_logged_out"
RESTORED_UNVERIFIED = "restored_unverified"


# ---------------------------------------------------------------------------------------------
# The encrypted blob store — beside the vault, never inside it
# ---------------------------------------------------------------------------------------------

def _store_dir() -> Path:
    """Where payloads live: beside the vault, under the same key and discipline.

    NOT inside `secrets_vault.json`, and the reason is measured rather than stylistic:
    `secrets_vault._save` re-serializes the WHOLE document (`json.dumps(doc, indent=2)`) and
    rewrites it under `O_TRUNC`. Putting a 1–2.5 MB base64 blob in there means every snapshot
    truncate-rewrites the file holding the account passwords, and a crash mid-write loses those
    too. The brief said to check the size assumption before reusing the vault; this is the answer.
    Same key provider, same 0600, separate file, ATOMIC write (see `_write_blob`) — because
    `apply_state_store.save`'s plain `write_text` is fine for a blackboard that can be rebuilt and
    not fine for the only copy of a login.
    """
    from settings import settings  # local: keeps import order identical to the rest of the app
    base = Path(settings.observer_artifacts_dir).expanduser()
    d = base / "cache" / "session_snapshots"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _blob_path(snapshot_id: str) -> Path:
    return _store_dir() / f"{snapshot_id}.enc"


def _meta_path() -> Path:
    return _store_dir() / "index.json"


def _write_blob(snapshot_id: str, payload: dict[str, Any]) -> int:
    """Encrypt and write atomically. Returns ciphertext bytes."""
    from secrets_vault import get_provider
    token = get_provider().encrypt(json.dumps(payload))
    path = _blob_path(snapshot_id)
    tmp = path.with_suffix(".enc.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)  # atomic: a torn write can never become the only copy of a login
    return len(token)


def _read_blob(snapshot_id: str) -> Optional[dict[str, Any]]:
    from secrets_vault import get_provider
    path = _blob_path(snapshot_id)
    if not path.exists():
        return None
    try:
        return json.loads(get_provider().decrypt(path.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001 — an undecryptable blob is absent, not a crash
        return None


def _load_index() -> list[SnapshotMeta]:
    p = _meta_path()
    if not p.exists():
        return []
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            out.append(SnapshotMeta(
                id=r["id"], profile=r["profile"], tier=r["tier"], taken_at=float(r["taken_at"]),
                platform=r.get("platform"), chrome=r.get("chrome", ""),
                cookie_count=int(r.get("cookie_count", 0)),
                domain_count=int(r.get("domain_count", 0)),
                session_cookie_count=int(r.get("session_cookie_count", 0)),
                auth_cookies_found=tuple(r.get("auth_cookies_found") or ()),
                auth_ttl_s=r.get("auth_ttl_s"),
                origins_with_storage=tuple(r.get("origins_with_storage") or ()),
                tabs=tuple(r.get("tabs") or ()),
                captured=tuple(r.get("captured") or ()),
                not_captured=tuple(r.get("not_captured") or ()),
                pinned=bool(r.get("pinned")), note=r.get("note", ""),
                verified=r.get("verified"),
            ))
        except Exception:  # noqa: BLE001 — one bad row never hides the rest
            continue
    return out


def _save_index(rows: list[SnapshotMeta]) -> None:
    p = _meta_path()
    tmp = p.with_suffix(".json.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump([m.as_dict() for m in rows], fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


def list_snapshots(profile: Optional[str] = None) -> list[SnapshotMeta]:
    rows = _load_index()
    if profile:
        rows = [r for r in rows if r.profile == profile]
    return sorted(rows, key=lambda r: r.taken_at, reverse=True)


def get_meta(snapshot_id: str) -> Optional[SnapshotMeta]:
    return next((r for r in _load_index() if r.id == snapshot_id), None)


def set_pinned(snapshot_id: str, pinned: bool) -> Optional[SnapshotMeta]:
    rows = _load_index()
    out, hit = [], None
    for r in rows:
        if r.id == snapshot_id:
            hit = replace(r, pinned=pinned)
            out.append(hit)
        else:
            out.append(r)
    if hit:
        _save_index(out)
    return hit


def delete_snapshot(snapshot_id: str) -> bool:
    rows = _load_index()
    keep = [r for r in rows if r.id != snapshot_id]
    if len(keep) == len(rows):
        return False
    _blob_path(snapshot_id).unlink(missing_ok=True)
    _save_index(keep)
    return True


def enforce_retention(profile: str, *, keep: int = KEEP_PER_PROFILE) -> list[str]:
    """Sweep old snapshots for one profile. Pinned rows are fixtures and never swept.

    Enforced in CODE rather than stated in a doc, because "decide a policy" that lives in prose is
    how 433 MB of profiles got onto this disk unnoticed in the first place.
    """
    rows = sorted([r for r in _load_index() if r.profile == profile],
                  key=lambda r: r.taken_at, reverse=True)
    unpinned = [r for r in rows if not r.pinned]
    doomed = unpinned[keep:]
    for r in doomed:
        delete_snapshot(r.id)
    return [r.id for r in doomed]


# ---------------------------------------------------------------------------------------------
# The CDP seam — a protocol so every test above runs without a browser
# ---------------------------------------------------------------------------------------------

class _WSCDP:
    """One websocket, request/response by id. Deliberately tiny: this module needs six methods."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._id = 0

    async def send(self, method: str, params: Optional[dict] = None) -> dict:
        self._id += 1
        mine = self._id
        await self._ws.send(json.dumps({"id": mine, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await self._ws.recv())
            if msg.get("id") == mine:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error'].get('message', 'CDP error')}")
                return msg.get("result", {})


def _http_json(url: str, timeout: float = 5.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as fh:
        return json.loads(fh.read().decode("utf-8"))


def browser_url_for(port: int) -> str:
    return f"http://127.0.0.1:{port}"


async def _connect(ws_url: str):
    import websockets
    return await websockets.connect(ws_url, max_size=64 * 1024 * 1024)


def _domain_of(cookie: dict[str, Any]) -> str:
    return (cookie.get("domain") or "").lstrip(".")


def _platform_of_jar(cookies: list[dict[str, Any]]) -> Optional[str]:
    """Which platform this jar is FOR, read off the cookies rather than asserted by a caller.

    The `upsert_observed_jobs` rule, one layer down: the page is the authority on what platform it
    is. A caller's wrong guess here would file a LinkedIn identity under `indeed` and the restore
    would put it back on the wrong browser.
    """
    counts: dict[str, int] = {}
    for c in cookies:
        dom = _domain_of(c)
        for plat, hosts in _PLATFORM_HOSTS.items():
            if any(dom == h or dom.endswith("." + h) for h in hosts):
                counts[plat] = counts.get(plat, 0) + 1
    return max(counts, key=counts.get) if counts else None  # type: ignore[arg-type]


_WARM_CAPTURED = ("cookies (all domains, incl. session cookies)", "open tab urls",
                  "localStorage + sessionStorage for origins with an OPEN TAB")
#: The gap, named rather than left to be rediscovered — the brief's DoD asks for exactly this.
_WARM_NOT_CAPTURED = (
    "localStorage/sessionStorage for origins with NO open tab (CDP has no browser-wide enumerator "
    "that does not require attaching per origin)",
    "IndexedDB (linkedin holds 2.1 MB of it; unmeasured whether any of it carries identity)",
    "Login Data (saved passwords) — deliberately: the vault is the authority for those",
    "Service Worker registrations",
)


async def capture_warm(*, port: int, profile: str, note: str = "",
                       connect: Callable[[str], Any] = _connect,
                       http_json: Callable[[str], Any] = _http_json) -> SnapshotMeta:
    """Read a running browser's identity over CDP and store it encrypted.

    Warm, not cold, and the reason is mechanical: Chrome keeps Cookies in a WAL-backed SQLite file,
    so a file copy taken under a live browser can read torn state. `Storage.getCookies` at the
    browser level is complete, consistent, and costs the session nothing.
    """
    base = browser_url_for(port)
    version = http_json(f"{base}/json/version")
    ws = await connect(version["webSocketDebuggerUrl"])
    try:
        cdp = _WSCDP(ws)
        cookies = (await cdp.send("Storage.getCookies")).get("cookies", [])
        targets = (await cdp.send("Target.getTargets")).get("targetInfos", [])
    finally:
        await ws.close()

    pages = [t for t in targets if t.get("type") == "page"]
    tabs = tuple((t.get("url") or "")[:300] for t in pages if t.get("url"))

    storage = await _capture_open_tab_storage(base, http_json=http_json, connect=connect)

    platform = _platform_of_jar(cookies)
    ttl, found = auth_ttl_s(cookies, platform)
    snapshot_id = f"{profile}-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"

    _write_blob(snapshot_id, {"cookies": cookies, "storage": storage, "tabs": list(tabs)})

    meta = SnapshotMeta(
        id=snapshot_id, profile=profile, tier="identity_warm", taken_at=time.time(),
        platform=platform, chrome=str(version.get("Browser") or ""),
        cookie_count=len(cookies),
        domain_count=len({_domain_of(c) for c in cookies}),
        session_cookie_count=sum(1 for c in cookies
                                 if c.get("expires") is None or c.get("expires", -1) < 0),
        auth_cookies_found=tuple(found), auth_ttl_s=ttl,
        origins_with_storage=tuple(sorted(storage)), tabs=tabs,
        captured=_WARM_CAPTURED, not_captured=_WARM_NOT_CAPTURED, note=note,
    )
    _save_index(_load_index() + [meta])
    enforce_retention(profile)
    return meta


async def _capture_open_tab_storage(base: str, *, http_json: Callable[[str], Any],
                                    connect: Callable[[str], Any]) -> dict[str, Any]:
    """localStorage + sessionStorage for each open tab's origin.

    Only origins with an open tab — that limit is in `_WARM_NOT_CAPTURED` rather than papered over.
    It is not nothing: an in-progress apply keeps its wizard state here, and those are the tabs
    that are open when a session goes stale.
    """
    out: dict[str, Any] = {}
    try:
        listing = http_json(f"{base}/json/list")
    except Exception:  # noqa: BLE001
        return out
    for t in listing if isinstance(listing, list) else []:
        if t.get("type") != "page" or not t.get("webSocketDebuggerUrl"):
            continue
        try:
            ws = await connect(t["webSocketDebuggerUrl"])
        except Exception:  # noqa: BLE001 — a tab that will not attach is one origin missed
            continue
        try:
            res = await _WSCDP(ws).send("Runtime.evaluate", {
                "expression": (
                    "(() => { const g = s => { try { return Object.fromEntries("
                    "Object.entries(s)); } catch (e) { return null; } };"
                    " return {origin: location.origin, local: g(localStorage),"
                    " session: g(sessionStorage)}; })()"),
                "returnByValue": True})
            val = (res.get("result") or {}).get("value") or {}
            origin = val.get("origin")
            if origin:
                out[origin] = {"local": val.get("local") or {},
                               "session": val.get("session") or {}}
        except Exception:  # noqa: BLE001
            pass
        finally:
            await ws.close()
    return out


async def restore_warm(*, port: int, snapshot_id: str,
                       connect: Callable[[str], Any] = _connect,
                       http_json: Callable[[str], Any] = _http_json) -> dict[str, Any]:
    """Put a snapshot's cookies back into a running browser. Tabs are PROPOSED, never opened.

    Two deliberate limits:

    * **Cookies only.** Restoring per-origin storage means navigating to each origin and
      evaluating into it — that is a DRIVE, on an account, and §3 says states are reached by
      clicking. A recovery is not a licence to URL-jump an account's origins unbidden.
    * **The tab list comes back as a proposal.** `PLAN_staleness.md` is explicit that nothing acts
      on a verdict while the thresholds are guesses, and reopening an account's tabs unbidden is
      bot-safety-relevant besides. Offer the remedy; the operator presses.
    """
    payload = _read_blob(snapshot_id)
    if payload is None:
        return {"ok": False, "detail": f"snapshot {snapshot_id} has no readable payload"}
    cookies = payload.get("cookies") or []
    base = browser_url_for(port)
    version = http_json(f"{base}/json/version")
    ws = await connect(version["webSocketDebuggerUrl"])
    try:
        cdp = _WSCDP(ws)
        await cdp.send("Storage.clearCookies")
        await cdp.send("Storage.setCookies", {"cookies": cookies})
        after = (await cdp.send("Storage.getCookies")).get("cookies", [])
    finally:
        await ws.close()
    meta = get_meta(snapshot_id)
    return {
        "ok": True, "snapshot_id": snapshot_id,
        "cookies_written": len(cookies), "cookies_present_after": len(after),
        # A count that does not match is not a failure — Chrome refuses cookies whose domain no
        # longer resolves, and saying so beats a silent "restored".
        "shortfall": max(0, len(cookies) - len(after)),
        "tabs_proposed": list(meta.tabs) if meta else [],
        "storage_not_restored": sorted(payload.get("storage") or {}),
        "verified": None,  # verify_restore fills this; a restore never grades itself
    }


def verify_verdict(auth_state: dict[str, Any]) -> str:
    """Turn an `/auth_state` reading into one of the three verdicts.

    `ok: false` becomes `restored_unverified` rather than `restored_but_logged_out`: the probe
    covers indeed and linkedin only (`_AUTH_JS_BY_PLATFORM`, main_server.py:5697) and every other
    host falls into its `except`. A check that was not performed has a defined, strict consequence,
    and it is never "assume fine" — nor "assume broken", which would send an operator to re-login a
    session that was fine.
    """
    if not auth_state.get("ok"):
        return RESTORED_UNVERIFIED
    return RESTORED_AUTHENTICATED if auth_state.get("logged_in") else RESTORED_LOGGED_OUT


def record_verification(snapshot_id: str, verdict: str) -> Optional[SnapshotMeta]:
    rows = _load_index()
    out, hit = [], None
    for r in rows:
        if r.id == snapshot_id:
            hit = replace(r, verified=verdict)
            out.append(hit)
        else:
            out.append(r)
    if hit:
        _save_index(out)
    return hit
