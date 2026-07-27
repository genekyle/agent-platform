"""What it takes for the Gmail errand to be a real, callable capability — pinned end to end.

Gmail is the first member of the `google` PROVIDER group, and the provider is a different shape
from the Career Search GROUP: its members don't hand work to each other, they share one identity,
and what other domains want from them is a bounded favour that RETURNS — an errand.

These tests are the checklist a SECOND provider member (Docs, Sheets, Drive — the operator's stated
next surfaces, likely API-backed rather than driven) has to satisfy. Each one names the wrong
answer it prevents, because every seam here has a plausible wrong answer that fails silently:
a stale code looks exactly like a fresh one, a second Chrome profile looks exactly like the signed-in
one until nothing is signed in, and a goal with no scenario looks fine in every picker.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import accounts
import command_center
import errands
import gmail_recipe
import providers
import seed
from db import Base
from models import DomainRegistry, GoalRegistry, ScenarioRegistry

NOW = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)


def _row(subject: str, *, sender: str = "no-reply@indeed.com", ago_seconds: int = 30,
         snippet: str = "") -> dict:
    return {
        "sender": sender,
        "subject": subject,
        "snippet": snippet,
        "received_at": (NOW - timedelta(seconds=ago_seconds)).isoformat(),
    }


def _request(**params) -> errands.ErrandRequest:
    return errands.ErrandRequest(
        errand_id="fetch_login_code",
        requested_by="indeed_jobs",
        reason="Indeed offered 'sign in with a code instead'",
        params=params,
    )


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


# --- the registry seams ------------------------------------------------------------------

def test_the_errand_goal_has_a_scenario_so_a_session_can_be_created(db):
    """WRONG ANSWER PREVENTED: `fetch_login_code` shows up in every picker and no session can be
    created for it, because create_training_session 404s when the scenario is missing. This is the
    exact bug commit 642c8dd fixed for LinkedIn — a domain (or here, a GOAL) with no scenario is
    registered and unusable, and nothing about it looks broken from the outside."""
    seed.seed_training_registry(db)

    goals = {g.goal_id for g in db.scalars(
        select(GoalRegistry).where(GoalRegistry.domain_id == "gmail"))}
    assert "fetch_login_code" in goals

    scenarios = db.scalars(
        select(ScenarioRegistry).where(ScenarioRegistry.domain_id == "gmail")).all()
    served = {s.goal_id for s in scenarios}
    assert "fetch_login_code" in served, (
        "the errand goal has no scenario — it is registered and unusable")


def test_every_gmail_scenario_starts_on_a_state_the_domain_declares(db):
    """WRONG ANSWER PREVENTED: a session starts against a page state the domain never declared, so
    the classifier has no label to match and the drive begins from nowhere. Same invariant
    test_career_aggregators pins for the job engines."""
    seed.seed_training_registry(db)
    domain = db.get(DomainRegistry, "gmail")
    declared = {s["page_state_id"] for s in domain.page_states}
    starts = {s.start_page_state for s in db.scalars(
        select(ScenarioRegistry).where(ScenarioRegistry.domain_id == "gmail")) if s.start_page_state}
    assert starts <= declared, f"scenarios start on undeclared states: {starts - declared}"


def test_the_top_up_seeder_adds_scenarios_to_an_already_seeded_db(db):
    """WRONG ANSWER PREVENTED: the fix works on a fresh database and does nothing on the operator's
    real one. `seed_training_registry` early-returns when ANY domain row exists, so on every
    already-seeded DB the new scenario arrives only via the top-up seeder — which, before this
    change, topped up domains and goals but not tasks or scenarios."""
    db.add(DomainRegistry(domain_id="facebook_marketplace", display_name="FB", host_patterns=[],
                          page_states=[], capture_defaults={}, validation_expectations=[],
                          config_version="v1", status="active"))
    db.commit()

    seed.seed_training_registry(db)  # no-op: the registry is not empty
    assert db.get(DomainRegistry, "gmail") is None

    seed.seed_gmail_domain(db)
    assert db.get(DomainRegistry, "gmail") is not None
    served = {s.goal_id for s in db.scalars(
        select(ScenarioRegistry).where(ScenarioRegistry.domain_id == "gmail"))}
    assert "fetch_login_code" in served


def test_the_seeder_merges_and_never_removes(db):
    """WRONG ANSWER PREVENTED: the seeder reconciles a hand-made `gmail` row by overwriting it, and
    a concurrent session's page states vanish. Merge-never-remove is the discipline every top-up
    seeder here follows."""
    db.add(DomainRegistry(domain_id="gmail", display_name="Gmail", host_patterns=["custom.host"],
                          page_states=[{"page_state_id": "operator_added",
                                        "display_name": "Operator Added"}],
                          capture_defaults={}, validation_expectations=[], config_version="v1",
                          status="active"))
    db.commit()

    seed.seed_gmail_domain(db)

    row = db.get(DomainRegistry, "gmail")
    states = {s["page_state_id"] for s in row.page_states}
    assert "operator_added" in states, "the seeder removed a state it did not own"
    assert "inbox" in states, "the seeder failed to merge in the canonical states"
    assert "custom.host" in row.host_patterns


# --- the provider seam -------------------------------------------------------------------

def test_gmail_and_its_provider_agree_on_one_chrome_profile():
    """WRONG ANSWER PREVENTED: Docs and Sheets later launch into a Chrome profile that is NOT the
    one the operator signed into, and every 'shared login' promise quietly fails. A provider exists
    precisely so ONE sign-in authenticates every member — if the account and the provider name
    different profiles, the group is decorative."""
    google = providers.get_provider("google")
    account = accounts.get_account("gmail_default")
    assert account["profile"] == google["profile"], (
        f"gmail_default uses profile {account['profile']!r} but the google provider shares "
        f"{google['profile']!r} — a second profile is a second, unauthenticated browser")


def test_the_errand_routes_through_the_provider_not_a_hardcoded_gmail():
    """WRONG ANSWER PREVENTED: every caller hardcodes 'gmail', and when the code-reading surface
    moves (to the Google API, or to a different member) each caller has to be found and edited.
    The provider's `code_delivery` block is the indirection — this asserts it is actually read."""
    resolved = errands.route("fetch_login_code")
    assert resolved is not None, "the errand has no route — code_delivery is unread again"
    assert resolved["domain_id"] == "gmail"
    assert resolved["provider_id"] == "google"
    assert resolved["profile"] == providers.get_provider("google")["profile"]
    assert errands.route("no_such_errand") is None


def test_command_center_does_not_report_gmail_as_a_jobs_domain(db):
    """WRONG ANSWER PREVENTED: the rollup's kind check is a binary (selling vs everything-else),
    so a comms domain falls through to the JOBS branch and Gmail's tile reports 'Jobs found: 0'
    while querying ObservedJob for a platform that will never exist. This is the LinkedIn lesson
    one altitude up: a branch with no case for you still answers, and answers wrongly."""
    tile = next((d for d in command_center.DOMAINS if d["id"] == "gmail"), None)
    assert tile is not None, "gmail has no rollup tile"
    assert tile["kind"] != "jobs"
    summary = command_center.build_summary(db)
    gmail_tile = next(t for t in summary["domains"] if t["id"] == "gmail")
    assert gmail_tile["primary"]["label"] != "Jobs found"


# --- the resolver: freshness ---------------------------------------------------------------

def test_a_fresh_code_is_read_out_of_the_subject_line():
    """The 2026-07-10 live case, verbatim: Indeed puts the code in the SUBJECT, so the inbox list
    is enough and the thread is never opened."""
    result = errands.resolve_login_code(
        _request(), [_row("Sign in to Indeed with code: 418302")], requested_at=NOW)
    assert result.status == errands.OK
    assert result.value == "418302"
    assert "418302" not in str(result.evidence.get("matched_pattern"))
    assert result.evidence["subject"].startswith("Sign in to Indeed")


def test_a_stale_code_is_refused():
    """WRONG ANSWER PREVENTED — the one that fails silently. A login-code inbox is full of old
    codes from previous attempts, every one of them matching sender and subject perfectly. Return
    yesterday's and the form just says 'invalid'; nothing errors, nothing looks broken, and the
    drive stalls somewhere that looks like a different problem entirely."""
    result = errands.resolve_login_code(
        _request(), [_row("Sign in to Indeed with code: 111111", ago_seconds=7200)],
        requested_at=NOW)
    assert result.status == errands.NOT_FOUND
    assert result.value is None
    assert any("stale" in c["rejected"] for c in result.considered)


def test_the_newest_code_wins_when_the_same_code_arrives_twice():
    """A resend of the SAME code is not ambiguity — it is one answer, delivered twice."""
    result = errands.resolve_login_code(
        _request(),
        [_row("Your Indeed code is 553311", ago_seconds=200),
         _row("Your Indeed code is 553311", ago_seconds=20)],
        requested_at=NOW)
    assert result.status == errands.OK
    assert result.value == "553311"
    assert result.evidence["age_seconds"] == 20


def test_a_row_without_a_timestamp_cannot_prove_freshness():
    """WRONG ANSWER PREVENTED: an unparseable timestamp is treated as 'probably fine'. Without a
    timestamp this attempt's code and the last one's are indistinguishable in every other respect,
    so absence of proof is failure, not a pass."""
    result = errands.resolve_login_code(
        _request(), [{"sender": "no-reply@indeed.com",
                      "subject": "Sign in to Indeed with code: 900001",
                      "received_at": "yesterday-ish"}],
        requested_at=NOW)
    assert result.status == errands.NOT_FOUND
    assert any("freshness" in c["rejected"] for c in result.considered)


def test_a_future_timestamp_is_refused_as_clock_skew():
    result = errands.resolve_login_code(
        _request(), [_row("Sign in to Indeed with code: 777777", ago_seconds=-600)],
        requested_at=NOW)
    assert result.status == errands.NOT_FOUND
    assert any("skew" in c["rejected"] for c in result.considered)


# --- the resolver: never guess -------------------------------------------------------------

def test_two_different_codes_escalate_rather_than_picking_one():
    """WRONG ANSWER PREVENTED: the system picks the newer of two near-simultaneous codes and types
    it. A one-time code typed wrong is not a free retry — it burns an attempt, and enough of them
    lock the account. Choosing between credentials is exactly the judgement we escalate."""
    result = errands.resolve_login_code(
        _request(),
        [_row("Sign in to Indeed with code: 222222", ago_seconds=40),
         _row("Sign in to Indeed with code: 333333", ago_seconds=20)],
        requested_at=NOW)
    assert result.status == errands.AMBIGUOUS
    assert result.value is None
    assert result.status in errands.ESCALATING
    assert "operator" in result.escalation.lower()


def test_an_unlabelled_number_is_reported_not_returned():
    """WRONG ANSWER PREVENTED: 'grab the longest number in the subject' reads order numbers, totals
    and years as credentials. A number nothing calls a code is surfaced to the human as considered,
    never handed back as an answer."""
    result = errands.resolve_login_code(
        _request(), [_row("Your Indeed application #4839201 was viewed")], requested_at=NOW)
    assert result.status == errands.NOT_FOUND
    assert any("nothing labels it a code" in c["rejected"] for c in result.considered)


def test_another_senders_code_is_not_the_callers_code():
    """WRONG ANSWER PREVENTED: a fresh, perfectly-formed code from LinkedIn is handed to Indeed's
    login form because both are 'a login code'."""
    result = errands.resolve_login_code(
        _request(),
        [_row("Sign in to LinkedIn with code: 654321", sender="no-reply@linkedin.com")],
        requested_at=NOW)
    assert result.status == errands.NOT_FOUND
    assert any("indeed" in c["rejected"] for c in result.considered)


def test_the_caller_domain_stem_is_matched_not_the_registry_id():
    """WRONG ANSWER PREVENTED: matching the literal registry id `indeed_jobs` against an email from
    `indeed.com` finds nothing, so a perfectly good code is missed. The registry id and the brand
    are deliberately different strings — the same `indeed_jobs` vs `indeed` split that already cost
    us a wrong rollup."""
    result = errands.resolve_login_code(
        _request(), [_row("Sign in to Indeed with code: 246813")], requested_at=NOW)
    assert result.status == errands.OK
    assert result.evidence["matched_on"] == "indeed"


@pytest.mark.parametrize("subject,expected", [
    ("Sign in to Indeed with code: 418302", "418302"),
    ("418302 is your verification code", "418302"),
    ("Your code is G-418302", "G-418302"),
    ("OTP: 4821", "4821"),
    ("passcode 908213 expires in 10 minutes", "908213"),
])
def test_code_shapes_we_have_actually_seen(subject, expected):
    found = errands.extract_code(subject)
    assert found is not None, f"failed to read a code out of {subject!r}"
    assert found[0] == expected


# --- the result contract -------------------------------------------------------------------

def test_ok_does_not_mean_authenticated():
    """WRONG ANSWER PREVENTED: a caller reads status=ok as 'we are in'. On 2026-07-10 the email
    code got Indeed past the email wall and it then demanded phone 2FA — the errand gets you PAST
    one factor, not through the door."""
    result = errands.resolve_login_code(
        _request(), [_row("Sign in to Indeed with code: 135790")], requested_at=NOW)
    assert result.status == errands.OK
    assert result.expect_followup_factor is True


def test_the_public_view_never_carries_the_code():
    """WRONG ANSWER PREVENTED: the code reaches the activity feed, the UI, or a log line. The
    caller that asked gets it off the dataclass; every other consumer gets as_public()."""
    result = errands.resolve_login_code(
        _request(), [_row("Sign in to Indeed with code: 864209")], requested_at=NOW)
    public = result.as_public()
    assert public["value"] is None
    assert public["has_value"] is True
    assert "864209" not in str(public)


def test_the_evidence_masks_the_code_it_cites():
    """WRONG ANSWER PREVENTED — and this one was live for the first draft of this module. The
    subject line is our best evidence AND it is where the code lives, so citing it verbatim writes
    the secret into the errand log, the Errands tab, and any escalation a human reads. The sentence
    is worth keeping; the digits are not."""
    result = errands.resolve_login_code(
        _request(), [_row("Sign in to Indeed with code: 864209")], requested_at=NOW)
    assert result.value == "864209", "the caller must still get the real code"
    assert "864209" not in result.evidence["subject"]
    assert "[code:6]" in result.evidence["subject"]


def test_a_rejected_stale_code_is_not_written_down_either():
    """A stale row's subject carries a real, recently-valid code. 'We didn't use it' is not a
    reason to keep it in an append-only log forever."""
    result = errands.resolve_login_code(
        _request(), [_row("Sign in to Indeed with code: 111111", ago_seconds=7200)],
        requested_at=NOW)
    assert result.status == errands.NOT_FOUND
    assert "111111" not in str(result.considered)


def test_an_empty_inbox_is_retryable_not_an_escalation():
    """The mail may simply not have arrived yet. NOT_FOUND must not summon a human — only AMBIGUOUS
    and BLOCKED do."""
    result = errands.resolve_login_code(_request(), [], requested_at=NOW)
    assert result.status == errands.NOT_FOUND
    assert result.status not in errands.ESCALATING


# --- the HTTP seam other domains call ------------------------------------------------------

@pytest.fixture()
def api(monkeypatch, tmp_path):
    """A client with the capture server faked at the one seam, and the errand log redirected into
    a temp dir so a test run never appends to the operator's real record."""
    from fastapi.testclient import TestClient

    import errand_log
    import main
    from routers import errands as errands_router

    monkeypatch.setattr(errand_log, "_path", lambda: tmp_path / "errands.jsonl")

    inbox: dict = {}

    async def fake_capture_post(path, payload, timeout=30.0):
        assert path == "/read_inbox"
        return inbox["response"]

    monkeypatch.setattr(errands_router, "_capture_post", fake_capture_post)
    client = TestClient(main.app)
    return client, inbox


def _inbox_ok(rows, **overrides):
    return {"ok": True, "signed_in": True, "list_found": True, "row_count": len(rows),
            "rows": rows, "url": "https://mail.google.com/mail/u/0/#inbox",
            "read_at": NOW.isoformat(), **overrides}


def test_the_route_returns_the_code_to_the_caller_that_asked(api):
    client, inbox = api
    inbox["response"] = _inbox_ok([_row("Sign in to Indeed with code: 418302")])

    response = client.post("/api/errands/fetch_login_code",
                           json={"requested_by": "indeed_jobs", "reason": "code sign-in"})
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["code"] == "418302"
    assert body["expect_followup_factor"] is True
    assert body["route"]["domain_id"] == "gmail"


def test_a_signed_out_profile_blocks_rather_than_reporting_no_mail(api):
    """WRONG ANSWER PREVENTED: signed out reads as 'no code yet', so the caller retries forever
    against a browser that will never have mail in it. Signed out needs a human — and one sign-in
    unblocks every Google surface at once, which is the whole point of the shared profile."""
    client, inbox = api
    inbox["response"] = _inbox_ok([], signed_in=False)

    body = client.post("/api/errands/fetch_login_code",
                       json={"requested_by": "indeed_jobs"}).json()
    assert body["status"] == "blocked"
    assert "signed out" in body["escalation"].lower()


def test_an_unfindable_list_is_not_reported_as_an_empty_inbox(api):
    """WRONG ANSWER PREVENTED — the failure mode this codebase keeps relearning. A reader that
    returns [] for 'I could not find the list' is indistinguishable from 'there is no mail', and
    one of those needs a human while the other needs patience. Same shape as the LinkedIn
    virtualised-list undercount: nothing errors, so nothing looks wrong."""
    client, inbox = api
    inbox["response"] = _inbox_ok([], list_found=False)

    body = client.post("/api/errands/fetch_login_code",
                       json={"requested_by": "indeed_jobs"}).json()
    assert body["status"] == "blocked"
    assert "not reporting this as 'no mail'" in body["escalation"].lower()


def test_an_unreachable_browser_blocks_honestly(api):
    client, inbox = api
    inbox["response"] = {"ok": False, "detail": "ConnectError: connection refused"}

    body = client.post("/api/errands/fetch_login_code",
                       json={"requested_by": "indeed_jobs"}).json()
    assert body["status"] == "blocked"
    assert "connection refused" in body["escalation"]


def test_freshness_is_judged_against_the_browsers_clock(api):
    """WRONG ANSWER PREVENTED: the mail's timestamp comes from the browser's machine and 'now'
    comes from ours, so if the capture server ever moves off this host every freshness verdict
    skews silently. Here the browser reports a read_at two hours ahead of the mail — stale, even
    though the mail is seconds old by our clock."""
    client, inbox = api
    rows = [_row("Sign in to Indeed with code: 418302", ago_seconds=0)]
    inbox["response"] = _inbox_ok(rows, read_at=(NOW + timedelta(hours=2)).isoformat())

    body = client.post("/api/errands/fetch_login_code",
                       json={"requested_by": "indeed_jobs"}).json()
    assert body["status"] == "not_found"
    assert any("stale" in c["rejected"] for c in body["considered"])


def test_the_errand_history_never_carries_a_code(api):
    client, inbox = api
    inbox["response"] = _inbox_ok([_row("Sign in to Indeed with code: 864209")])
    client.post("/api/errands/fetch_login_code", json={"requested_by": "indeed_jobs"})

    history = client.get("/api/errands").json()
    assert history["stats"]["served"] == 1
    assert history["stats"]["ok"] == 1
    assert "864209" not in str(history), "the errand log wrote the code down"
    assert history["errands"][0]["found"] is True


def test_the_catalog_describes_the_errand_before_anything_has_run(api):
    client, _ = api
    catalog = client.get("/api/errands/catalog").json()
    spec = catalog["errands"][0]
    assert spec["errand_id"] == "fetch_login_code"
    assert spec["route"]["domain_id"] == "gmail"
    assert any("never" in g.lower() for g in spec["guarantees"])


# --- the recipe --------------------------------------------------------------------------

def test_google_auth_is_not_read_with_indeeds_detector():
    """WRONG ANSWER PREVENTED: reusing the Indeed auth probe on a Google surface. It reported
    logged_in:false on myaccount.google.com — meaningless there, and confidently wrong. Being on a
    signed-in-only URL is the real signal (LEARNINGS 2026-07-10)."""
    assert gmail_recipe.map_url_to_state("https://mail.google.com/mail/u/0/#inbox") == "inbox"
    assert gmail_recipe.map_url_to_state(
        "https://accounts.google.com/v3/signin/identifier") == "google_signin_email"


def test_the_sso_states_gmail_owns_for_the_whole_provider_are_mapped():
    """Gmail owns the google_signin_* states on behalf of every provider member — they are the ONE
    sign-in Docs and Sheets will reuse. If the recipe cannot name them, that training data has no
    home."""
    declared = set(providers.get_provider("google")["auth"]["login_states"])
    mapped = {gmail_recipe.map_url_to_state(u) for u in [
        "https://accounts.google.com/v3/signin/identifier",
        "https://accounts.google.com/v3/signin/challenge/pwd",
        "https://accounts.google.com/v3/signin/challenge/totp",
        "https://accounts.google.com/signin/oauth/consent",
    ]}
    assert declared <= mapped, f"unmapped provider SSO states: {declared - mapped}"


def test_google_password_and_2fa_are_human_required_branches():
    """PRINCIPLES: we drive to Google's wall and stop. The password is the operator's crown-jewel
    credential and accounts.google.com is the most bot-fingerprinted page on the web — this is the
    same class of refusal as never auto-solving a captcha."""
    for state in ("google_signin_password", "google_signin_2fa"):
        described = gmail_recipe.describe_tab(f"https://accounts.google.com/x/{state}")
        branch = gmail_recipe.GMAIL_LOGIN_BRANCHES.get(state)
        assert branch is not None and branch["human_required"], (
            f"{state} must be a human-required branch")
        assert described is not None
