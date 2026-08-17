"""The dialect store — a site speaks one way, and the first widget teaches the rest.

Every case is the operator's 2026-08-11 thesis made checkable: candidates are offered in
learned → classified → cheapest order, impossibilities are dropped by tag, a win locks the
dialect, and a displaced dialect stays on the record.
"""

import importlib

import pytest


@pytest.fixture()
def dialect(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(tmp_path))
    from app import dialect as mod
    importlib.reload(mod)
    return mod


def test_the_first_verified_win_becomes_the_way(dialect):
    """Cornerstone's four selects, replayed: before any win the order is the generic bucket;
    after ONE native win, native leads every later call — the diagnosis is paid once."""
    first = dialect.candidate_order("cornerstone", dialect.FAMILY_OPTION_SELECT)
    assert first == ["native_select", "aria_listbox", "react_select"]
    dialect.record_win("cornerstone", dialect.FAMILY_OPTION_SELECT, "native_select",
                       evidence="#EEOQuestion-1 · selected Decline to specify")
    assert dialect.learned_protocol("cornerstone", dialect.FAMILY_OPTION_SELECT) == "native_select"
    again = dialect.candidate_order("cornerstone", dialect.FAMILY_OPTION_SELECT,
                                    classified="aria_listbox")
    assert again[0] == "native_select"          # the dialect outranks the classifier's hint
    assert again[1] == "aria_listbox"


def test_the_classifier_hint_leads_when_nothing_is_learned(dialect):
    order = dialect.candidate_order("greenhouse", dialect.FAMILY_OPTION_SELECT,
                                    classified="react_select")
    assert order[0] == "react_select"
    assert set(order) == {"native_select", "aria_listbox", "react_select"}


def test_the_tag_drops_the_structurally_impossible(dialect):
    """A bare <select> can never be a react-select; a div can never be a native one. The cycle
    must not attempt the impossible — a keystroke dance on a <select> mutates focus for
    nothing."""
    assert dialect.candidate_order("x", dialect.FAMILY_OPTION_SELECT, tag="select") \
        == ["native_select"]
    assert dialect.candidate_order("x", dialect.FAMILY_OPTION_SELECT, tag="div") \
        == ["aria_listbox", "react_select"]
    # A learned dialect that contradicts the tag is dropped too — the node outranks the record.
    dialect.record_win("x", dialect.FAMILY_OPTION_SELECT, "native_select")
    assert "native_select" not in dialect.candidate_order(
        "x", dialect.FAMILY_OPTION_SELECT, tag="div")


def test_a_different_winner_displaces_the_dialect_on_the_record(dialect):
    """Sites redesign. The new winner takes the seat; the displaced protocol and its win count
    stay in history — both sides of the change kept, per the Open Brain rule."""
    dialect.record_win("acme", dialect.FAMILY_OPTION_SELECT, "aria_listbox")
    dialect.record_win("acme", dialect.FAMILY_OPTION_SELECT, "aria_listbox")
    dialect.record_win("acme", dialect.FAMILY_OPTION_SELECT, "react_select")
    row = dialect.all_dialects()["acme::option_select"]
    assert row["protocol"] == "react_select" and row["wins"] == 1
    assert row["history"][0]["protocol"] == "aria_listbox"
    assert row["history"][0]["wins"] == 2


def test_unknown_platform_and_corrupt_store_stay_harmless(dialect):
    assert dialect.learned_protocol("", dialect.FAMILY_OPTION_SELECT) is None
    dialect.record_win("", dialect.FAMILY_OPTION_SELECT, "native_select")   # no-op, no crash
    dialect._store_path().write_text("{not json")
    assert dialect.all_dialects() == {}          # corrupt store = empty prior, never a crash


# --- the catch-all is not an identity ------------------------------------------------------------
def test_two_unmapped_employer_sites_do_not_share_a_dialect(dialect):
    """`company_site` is `ats_registry`'s bucket for "an employer's own careers page we do not
    recognise" — every unmapped site in the world lands in it. Keying a dialect there teaches one
    employer's answer to all of them.

    Measured live 2026-08-17: the store held `company_site::option_select -> native_select` with
    the evidence `#areaInterest · selected Information Technology` (Boston Children's BrassRing),
    and that was the first-tried protocol on WAHVE's form — whose dropdowns are bare
    `<div class="dropdown-label">` with no role and no `<select>` in the document, where
    native_select cannot possibly win.
    """
    dialect.record_win("company_site", dialect.FAMILY_OPTION_SELECT, "native_select",
                       evidence="#areaInterest · selected Information Technology",
                       site="https://jobs.bostonchildrens.org/apply")

    assert dialect.learned_protocol(
        "company_site", dialect.FAMILY_OPTION_SELECT,
        site="https://jobs.bostonchildrens.org/x") == "native_select"
    # A DIFFERENT unmapped employer inherits nothing.
    assert dialect.learned_protocol(
        "company_site", dialect.FAMILY_OPTION_SELECT,
        site="https://insurance.brainwahve.com/apply") is None
    order = dialect.candidate_order("company_site", dialect.FAMILY_OPTION_SELECT,
                                    site="https://insurance.brainwahve.com/apply")
    assert order == ["native_select", "aria_listbox", "react_select"], "generic bucket, unlearned"


def test_a_real_ats_still_generalises_across_tenants_and_engines(dialect):
    """The other half, and the reason the catch-all is special-cased rather than the key changed
    for everyone: an ATS id names ONE component library, so its dialect SHOULD cross every tenant
    and every engine that led there. A Workday found on LinkedIn speaks Workday."""
    dialect.record_win("workday", dialect.FAMILY_OPTION_SELECT, "aria_listbox",
                       evidence="#primaryQuestionnaire · selected",
                       site="https://acme.wd1.myworkdayjobs.com/x")
    for other in ("https://beta.wd5.myworkdayjobs.com/y", "", "https://anything.example/z"):
        assert dialect.learned_protocol("workday", dialect.FAMILY_OPTION_SELECT,
                                        site=other) == "aria_listbox"


def test_a_catch_all_with_no_host_keeps_the_old_bucket(dialect):
    """A key that changes shape depending on what we happened to know is worse than a shared
    prior: the win would be written under one key and read back under another, silently, and
    present as a dialect that never seems to be learned."""
    dialect.record_win("company_site", dialect.FAMILY_OPTION_SELECT, "react_select", site="")
    assert dialect.learned_protocol("company_site", dialect.FAMILY_OPTION_SELECT,
                                    site="") == "react_select"


def test_a_site_is_learnable_with_no_ats_id_at_all(dialect):
    """An unmapped employer site has no ATS id, and that is exactly the case the site key exists
    to serve — so an empty platform must not refuse the lesson when a host is known."""
    dialect.record_win("", dialect.FAMILY_OPTION_SELECT, "aria_listbox",
                       site="https://insurance.brainwahve.com/apply")
    assert dialect.learned_protocol(
        "", dialect.FAMILY_OPTION_SELECT,
        site="https://insurance.brainwahve.com/apply") == "aria_listbox"
    # Nothing to attach it to at all is still refused.
    dialect.record_win("", dialect.FAMILY_OPTION_SELECT, "react_select", site="")
    assert dialect.learned_protocol("", dialect.FAMILY_OPTION_SELECT, site="") is None


def test_the_host_is_normalised_the_same_way_on_write_and_read(dialect):
    """www., scheme, port and path must not split one site into several keys."""
    dialect.record_win("company_site", dialect.FAMILY_OPTION_SELECT, "native_select",
                       site="https://www.Example.com:443/careers/apply?x=1")
    for variant in ("http://example.com", "https://www.example.com/other",
                    "example.com:8080/deep/path"):
        assert dialect.learned_protocol("company_site", dialect.FAMILY_OPTION_SELECT,
                                        site=variant) == "native_select"
