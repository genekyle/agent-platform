"""What it takes for a career-search AGGREGATOR to be a real domain, pinned end to end.

Career Search is a GROUP, and Indeed was its only member for long enough that "Indeed" and "the
jobs domain" became the same thing in several places: the dashboard route was named after it, the
rollup queried its platform literally, and the Accounts tab was chosen by `parent ==
"career_search"` — which sent every engine to the per-EMPLOYER ATS panel, so the site that most
needs a sign-in had nowhere to type one.

LinkedIn is the second member, and these tests are the checklist a THIRD one (ZipRecruiter,
Glassdoor) has to satisfy. Each one names the seam that would otherwise silently answer "Indeed"
for a domain that is not Indeed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import accounts
import ats_registry
import command_center
import main
import seed
import session_activity
import tab_finder
from db import Base, get_db
from models import DomainRegistry, GoalRegistry
from perception import facets

client = TestClient(main.app)


def _db(shared: bool = False):
    """An empty in-memory DB. `shared` pins ONE connection (StaticPool) so a TestClient request,
    which runs on another thread, sees the same database instead of a fresh empty one."""
    kwargs = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}} if shared else {}
    engine = create_engine("sqlite://", **kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture
def api_db():
    """Route the API's `get_db` at an empty in-memory DB — these tests are about which domain a
    route answers FOR, not about the rows, so an empty table is the right fixture."""
    db = _db(shared=True)
    main.app.dependency_overrides[get_db] = lambda: db
    try:
        yield db
    finally:
        main.app.dependency_overrides.pop(get_db, None)


# --- the domain is registered where the rollup and the router can see it ----------------------
def test_linkedin_is_a_jobs_domain_beside_indeed():
    """Same `kind`, so it inherits the whole jobs operating pattern (status card, goals, tasks,
    training) instead of needing a parallel one."""
    by_id = {d["id"]: d for d in command_center.DOMAINS}
    assert by_id["linkedin_jobs"]["kind"] == by_id["indeed_jobs"]["kind"] == "jobs"
    assert by_id["linkedin_jobs"]["host"] == "linkedin"


def test_the_dashboard_route_is_keyed_by_domain_not_by_engine(api_db):
    """`/api/dashboards/indeed_jobs` was a route per engine. Every aggregator answers the same
    questions off the same table, so the domain is the parameter and the platform is derived."""
    for domain_id, platform in (("indeed_jobs", "indeed"), ("linkedin_jobs", "linkedin")):
        body = client.get(f"/api/dashboards/{domain_id}").json()
        assert body["domain_id"] == domain_id
        assert body["platform"] == platform
        assert "totals" in body and "jobs_seen" in body


def test_an_explicit_platform_still_wins_over_the_domain(api_db):
    """So a platform with no workspace yet is still readable."""
    body = client.get("/api/dashboards/linkedin_jobs?platform=ziprecruiter").json()
    assert body["platform"] == "ziprecruiter"


# --- the credentials seam ---------------------------------------------------------------------
def test_linkedin_has_a_domain_login_of_its_own(monkeypatch, tmp_path):
    """The operator's ask: somewhere to put the LinkedIn credentials. An aggregator login is a
    DOMAIN account (one login for the site), not the per-employer ATS kind — and it starts honest:
    present in the registry, `has_creds` false until something is actually saved."""
    monkeypatch.setattr(accounts, "_path", lambda: tmp_path / "accounts.json")
    monkeypatch.setattr(accounts, "_read_env_value", lambda key: "")

    rows = accounts.list_accounts(domain_id="linkedin_jobs")
    assert [r["account_id"] for r in rows] == ["linkedin_default"]
    acct = rows[0]
    assert acct["kind"] == "domain"          # not "ats" — the Accounts tab branches on this
    assert acct["profile"] == "linkedin"     # its own Chrome profile: sessions never bleed
    assert acct["has_creds"] is False


def test_saving_the_linkedin_login_puts_it_in_the_vault_not_the_registry(monkeypatch, tmp_path):
    """The whole point of the credentials spot: the typed password is encrypted at rest and the
    registry keeps only a masked hint. Nothing readable ever lands in accounts.json."""
    import secrets_vault
    monkeypatch.setattr(accounts, "_path", lambda: tmp_path / "accounts.json")
    monkeypatch.setattr(accounts, "_read_env_value", lambda key: "")
    monkeypatch.setenv("AGENT_VAULT_KEY_PATH", str(tmp_path / "vault.key"))
    monkeypatch.setattr(secrets_vault, "_vault_path", lambda: tmp_path / "vault.json")
    secrets_vault.reset_provider_cache()

    acct = accounts.set_credentials("linkedin_default", "person@example.com", "hunter2-not-real")
    assert acct["has_creds"] is True
    assert acct["secret_backend"] == "vault"
    assert acct["username_hint"] == "p***@example.com"
    registry_text = (tmp_path / "accounts.json").read_text()
    assert "hunter2-not-real" not in registry_text
    assert "person@example.com" not in registry_text


# --- the classifiers that would otherwise call LinkedIn something else ------------------------
def test_easy_apply_is_named_without_shadowing_a_real_ats():
    """LinkedIn's on-engine apply needs a name (like Indeed's smartapply), but its broad host must
    not swallow a posting that hands off to a real ATS."""
    assert ats_registry.classify_ats("https://www.linkedin.com/jobs/view/123") == "linkedin_easy_apply"
    # a hand-off keeps its own ATS, even though the hunt started on LinkedIn
    assert ats_registry.classify_ats("https://acme.wd5.myworkdayjobs.com/job/x") == "workday"
    assert ats_registry.classify_ats("https://boards.greenhouse.io/acme/jobs/1") == "greenhouse"


def test_a_linkedin_page_derives_as_linkedin_not_as_a_company_site():
    """`classify_ats` answers `company_site` for anything it does not recognize, and `company_site`
    is a real platform in the facet vocabulary — so an unregistered LinkedIn would have derived as
    a confident wrong answer, the same trap facebook.com fell into (facets.py, 2026-07-22)."""
    f = facets.facets_for("job_search", url="https://www.linkedin.com/jobs/search")
    assert f.platform == "linkedin"
    assert f.domain == "career_search"
    assert f.phase == "search_results"
    # and the on-engine apply is the ENGINE, not a platform of its own
    assert facets.platform_for(url="https://www.linkedin.com/jobs/view/1") == "linkedin"
    assert facets.platform_for("", domain_id="linkedin_jobs") == "linkedin"


def test_a_linkedin_session_counts_as_career_search():
    """tab_finder scans career-search sessions ONLY when resolving an account drive's target tab.
    A LinkedIn session that didn't count would be invisible to it."""
    assert tab_finder.is_career_search_session("linkedin_jobs")
    assert not tab_finder.is_career_search_session("facebook_marketplace")


def test_activity_rows_are_attributed_to_the_ENGINE_not_just_the_group():
    """Journal rows carry no domain field; the feed infers one. It used to answer `career_search`
    for everything underneath it, which is right for the group's tab and useless for an engine's:
    a feed titled "LinkedIn" that fills with Indeed rows is worse than an empty one. The most
    specific true answer wins."""
    assert session_activity._infer_domain(url="https://www.linkedin.com/jobs") == "linkedin_jobs"
    assert session_activity._infer_domain(ats="linkedin_easy_apply") == "linkedin_jobs"
    assert session_activity._infer_domain(url="https://www.indeed.com/jobs") == "indeed_jobs"
    # an ATS row is not either engine's — it stays with the group that owns the hand-off
    assert session_activity._infer_domain(ats="workday") == "career_search"


def test_the_group_collects_its_members_but_a_member_does_not_collect_its_siblings():
    """The asymmetry the two feeds need: Career Search shows everything underneath it, LinkedIn
    shows LinkedIn."""
    rows = [
        {"ts": "2026-07-27T10:00:00Z", "domain": "linkedin_jobs", "source": "action", "kind": "action"},
        {"ts": "2026-07-27T09:00:00Z", "domain": "indeed_jobs", "source": "action", "kind": "action"},
        {"ts": "2026-07-27T08:00:00Z", "domain": "career_search", "source": "action", "kind": "action"},
        {"ts": "2026-07-27T07:00:00Z", "domain": None, "source": "reasoning", "kind": "reasoning"},
    ]

    def _feed(domain):
        return [e for e in rows if session_activity._keep_for(e, domain)]

    assert {e["domain"] for e in _feed("linkedin_jobs")} == {"linkedin_jobs"}
    assert {e["domain"] for e in _feed("career_search")} == {
        "linkedin_jobs", "indeed_jobs", "career_search", None}


# --- the seeder, for databases that predate the promotion ------------------------------------
def test_seeding_linkedin_is_idempotent_and_additive():
    """`seed_training_registry` only runs on an EMPTY registry, so every already-seeded DB needs
    this top-up — and it must merge, never remove, because another session may own the row."""
    db = _db()
    db.add(DomainRegistry(domain_id="linkedin_jobs", display_name="LinkedIn Jobs", status="active",
                          host_patterns=["linkedin.com"],
                          page_states=[{"page_state_id": "mine", "display_name": "Hand-added"}]))
    db.commit()

    seed.seed_linkedin_domain(db)
    seed.seed_linkedin_domain(db)               # twice: idempotent

    row = db.get(DomainRegistry, "linkedin_jobs")
    states = {s["page_state_id"] for s in row.page_states}
    assert "mine" in states                      # the hand-added state survived
    assert {"job_search", "job_detail", "login_wall"} <= states
    goals = set(db.scalars(select(GoalRegistry.goal_id)
                           .where(GoalRegistry.domain_id == "linkedin_jobs")).all())
    assert goals == {"search_linkedin_jobs", "open_linkedin_job", "apply_to_linkedin_job"}


def test_a_domain_without_a_scenario_cannot_have_a_session():
    """A training session is (domain, scenario) and the create endpoint 404s on a missing scenario.
    So a domain seeded with goals but no scenario is registered and UNUSABLE — it shows up in every
    picker and nothing can be started for it. That is exactly where linkedin_jobs sat until the
    seeder learned to top up scenarios + tasks as well."""
    from models import ScenarioRegistry, TaskRegistry
    db = _db()
    seed.seed_linkedin_domain(db)
    scenarios = set(db.scalars(select(ScenarioRegistry.scenario_id)
                               .where(ScenarioRegistry.domain_id == "linkedin_jobs")).all())
    assert scenarios == {"linkedin_login_wall_log_in", "linkedin_job_search_open_job",
                         "linkedin_job_detail_apply"}
    # every scenario must start on a page state the domain actually declares, or the session opens
    # pointing at a state nothing can ever classify
    row = db.get(DomainRegistry, "linkedin_jobs")
    declared = {s["page_state_id"] for s in row.page_states}
    starts = set(db.scalars(select(ScenarioRegistry.start_page_state)
                            .where(ScenarioRegistry.domain_id == "linkedin_jobs")).all())
    assert starts <= declared, f"scenarios start on undeclared states: {starts - declared}"
    assert db.get(TaskRegistry, "linkedin_apply_flow") is not None


def test_seeding_linkedin_on_a_bare_database_creates_it():
    db = _db()
    seed.seed_linkedin_domain(db)
    assert db.get(DomainRegistry, "linkedin_jobs") is not None


# --- the ladder climbs either engine ----------------------------------------------------------
# session_control's cadence (one query per session, floor the radius, one page at a time) is about
# how we behave, so it is shared. These pin the four things that genuinely differ per engine, each
# of which silently answered "Indeed" before.
def test_a_results_url_is_recognised_per_engine():
    from routers import session_control as sc
    assert sc.engine_of_url("https://www.indeed.com/jobs?q=analyst")["platform"] == "indeed"
    assert sc.engine_of_url("https://www.linkedin.com/jobs/search/?keywords=analyst")["platform"] == "linkedin"
    # host WITHOUT the results path is not a job search — treating the feed as one would report a
    # query that was never run.
    assert sc.engine_of_url("https://www.linkedin.com/feed/") is None
    assert sc.engine_of_url("https://acme.wd5.myworkdayjobs.com/job/x") is None


def test_the_search_tab_is_matched_on_each_engines_own_query_param():
    """Indeed carries the query in `q`, LinkedIn in `keywords`. Matching on the query is what stops
    us adopting somebody else's search — so a hardcoded `q` would make every LinkedIn results tab
    look like a non-match, and the ladder would offer to run the one query the session gets."""
    from routers import session_control as sc
    tabs = [
        {"url": "https://www.linkedin.com/jobs/search/?keywords=reporting+analyst&start=25"},
        {"url": "https://www.indeed.com/jobs?q=reporting+analyst"},
    ]
    assert sc._find_search_tab(tabs, "reporting analyst") is tabs[0]
    assert sc._find_search_tab(tabs, "welder") is None       # neither tab is our query


def test_page_number_uses_each_engines_page_size():
    """Both paginate with ?start=, but a page is 10 on Indeed and 25 on LinkedIn. Reading LinkedIn
    with Indeed's size reports page 3 as page 6 — the ladder would believe it had climbed twice as
    far as it had, and the bounds exist to be believed."""
    from routers import session_control as sc
    assert sc._page_from_url("https://www.indeed.com/jobs?q=x&start=20") == 3
    assert sc._page_from_url("https://www.linkedin.com/jobs/search/?keywords=x&start=50") == 3
    assert sc._page_from_url("https://www.indeed.com/jobs?q=x") == 1


def test_the_engine_comes_from_the_live_tab_before_the_declared_domain():
    """A tab is a fact; `session.domain_id` is a label. A session started as indeed_jobs that the
    operator drove to LinkedIn must be read as LinkedIn — same precedence as perception/facets."""
    from routers import session_control as sc

    class _S:
        domain_id = "indeed_jobs"

    on_linkedin = {"url": "https://www.linkedin.com/jobs/search/?keywords=x"}
    assert sc.engine_for(_S(), on_linkedin)["platform"] == "linkedin"
    assert sc.engine_for(_S(), None)["platform"] == "indeed"          # falls back to the label
    assert sc.engine_for(_S(), {"url": "about:blank"})["platform"] == "indeed"


def test_an_unknown_domain_still_gets_a_working_engine():
    """Never None: every branch of the ladder reads the engine, so an unrecognised domain has to
    degrade to Indeed's shape rather than crash the panel."""
    from routers import session_control as sc

    class _S:
        domain_id = "ziprecruiter"

    assert sc.engine_for(_S(), None) is sc.DEFAULT_ENGINE


def test_the_capture_server_picks_its_readers_off_the_tabs_host():
    """The control plane names the TAB; the capture server decides which site's markup it is
    reading. A caller-supplied platform string would drift from the page it describes."""
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "mcp"))
    from app.main_server import _platform_of
    assert _platform_of("https://www.linkedin.com/jobs/search/?keywords=x") == "linkedin"
    assert _platform_of("https://www.indeed.com/jobs?q=x") == "indeed"
    # anything unrecognised keeps the historical behaviour rather than inventing a third path
    assert _platform_of("https://example.com/careers") == "indeed"
