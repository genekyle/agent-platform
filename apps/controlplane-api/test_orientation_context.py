"""Orientation context (SESSION 17) — asking what we already know, at the moment it matters.

Pinned in the order it costs to get wrong:
  1. The registry note reaches the rung SCOPED to what that rung does — the Cornerstone
     "rendered twice, drive the VISIBLE one" sentence is the eight-instance class in one line.
  2. Silence is REPORTED, never rendered as absence: an empty context that reads like a clean
     bill of health is the failure this exists to stop.
  3. `blockers` never says "nothing will stop you" from a ledger that has no store.
  4. The canonical account match catches the Odyssey shape and refuses a generic-word match.
  5. Nothing here can raise a drive down, and `cite()` is empty when nothing was learned.
"""
from __future__ import annotations

import orientation_context as oc


class _DeadDB:
    """A db that raises on any use — the guard, not the happy path: an orientation that can take
    the drive down is worse than one that is quiet."""
    def __getattr__(self, name):
        raise RuntimeError("db is down")


def _note(ats_id: str) -> str:
    """The REAL registry note. The first version of these tests used a paraphrase, and it passed
    while the extractor was throwing away the only line that mattered — the real Cornerstone note
    packs the finding into one 300-character sentence, which the sentence-granular first cut
    dropped on length. A fixture written to match the implementation tests the fixture."""
    import ats_registry as reg
    return (reg.get_ats(ats_id) or {}).get("notes") or ""


def test_the_note_reaches_the_entering_rung_with_the_clause_that_mattered():
    """2026-08-24: the Cornerstone note had said "rendered twice … drive the VISIBLE one" since
    08-11, and rediscovering it cost two failed cranks and a screenshot — in an entry
    `classify_ats` had already loaded to name the platform."""
    note = _note("cornerstone")
    assert "VISIBLE" in note, "the note was rewritten — re-point this test at what it says now"
    cues = oc.note_cues(note, "enter_apply")
    assert cues, "the entering rung got no cue from a note about its own control"
    assert "so drive the VISIBLE one" in cues[0], \
        "the operative clause must lead — our notes SHOUT the instruction, and that is the order"


def test_the_paylocity_modal_prediction_reaches_the_rung_that_meets_it():
    """2026-08-19: the note predicted the upload modal correctly, on file, and unread at the
    moment it mattered — "a note nothing reads at classify time is not memory, it is an archive"."""
    cues = oc.note_cues(_note("paylocity"), "enter_apply")
    assert any("MODAL" in c for c in cues)


def test_a_rung_only_hears_what_bears_on_what_it_does():
    account_cues = oc.note_cues(_note("cornerstone"), "account")
    assert any("Sign In" in c or "account" in c.lower() for c in account_cues)
    assert not any("VISIBLE" in c for c in account_cues), \
        "the account rung was handed the apply-button quirk — that is how a note becomes wallpaper"


def test_an_unmapped_rung_gets_an_opening_clause_not_nothing():
    assert oc.note_cues(_note("cornerstone"), "some_new_rung"), \
        "an unmapped rung should still learn what this platform IS"
    assert oc.note_cues("", "enter_apply") == []


def test_a_paragraph_wearing_a_full_stop_is_not_a_cue():
    wall = "Apply here. " + ("this sentence is about the apply button and goes on and on " * 8) + "."
    assert all(len(c) <= oc._MAX_CUE_CHARS for c in oc.note_cues(wall, "enter_apply"))


def test_silence_is_reported_and_nothing_raises_even_with_a_dead_db():
    ctx = oc.orientation_context(_DeadDB(), url="https://unknown.example.com/jobs/1",
                                 rung="enter_apply")
    assert ctx["headline"] is None
    assert "ats_brief" in ctx["silent"], "a silent authority must name itself"
    assert ctx["cues"] == []
    assert oc.cite(ctx) == "", "a crank that learned nothing must not write a ceremonial line"


def test_the_registry_note_survives_a_db_that_cannot_answer():
    """The note needs only the URL, and the first cut gated it behind `ats_brief`'s db call — so
    a db hiccup silenced the cheapest and most useful authority in the composer. Found by a test
    that drove the real crank; the platform is named from the host, independently."""
    ctx = oc.orientation_context(
        _DeadDB(), url="https://macomtech.csod.com/ux/ats/careersite/4/home/requisition/3553",
        rung="enter_apply")
    assert ctx["ats_id"] == "cornerstone", "the host names the platform without any database"
    assert any("VISIBLE" in c["text"] for c in ctx["cues"])
    assert "ats_brief" in ctx["silent"] and "registry_note" in ctx["consulted"]


def test_blockers_never_claims_a_clear_road_from_a_ledger_with_no_store():
    ctx = oc.orientation_context(_DeadDB(), url="https://x.example.com/jobs/1", rung="classify")
    b = ctx["blockers"]
    assert b["from_requirements_ledger"] is None, "an unstored ledger must not answer []"
    assert "never stored" in b["gap"]


def test_a_tab_another_job_holds_is_surfaced_before_it_misclassifies():
    """2026-08-24: a park leaves its tab alive on purpose, classify read MACOM's parked tab while
    working CEDENT, and the wrong platform outlived the call by hours."""
    ctx = oc.orientation_context(_DeadDB(), url="https://x.example.com/a", rung="classify",
                                 job_id="indeed:cedent",
                                 tab_claims={"tab-9": {"job_id": "indeed:macom",
                                                       "url": "https://macomtech.csod.com/x"}})
    assert ctx["tab_conflict"]["held_by"] == "indeed:macom"
    assert "indeed:macom" in oc.cite(ctx)


def test_the_citation_carries_what_was_learned_and_only_that():
    ctx = {"cues": [{"source": "registry_note", "text": "drive the VISIBLE one", "why": ""}],
           "account": {"account_id": "ats_acme_icims", "status": "created"},
           "auth_promise": "account", "stale_claims": [], "tab_conflict": None}
    line = oc.cite(ctx)
    assert line.startswith("consulted: ")
    assert "drive the VISIBLE one" in line
    assert "ats_acme_icims" in line
    assert "account wall" in line


# --- the account lookup: the 2026-08-24 duplicate row -------------------------------------------
def test_the_canonical_match_catches_the_odyssey_shape(monkeypatch):
    import ats_accounts

    monkeypatch.setattr(ats_accounts.accounts_mod, "list_accounts", lambda: [
        {"kind": "ats", "ats_id": "icims", "account_id": "ats_odyssey_consulting_icims",
         "company": "Odyssey Consulting", "status": "created", "has_creds": True},
    ])
    hit = ats_accounts.find_existing(
        None, company="Odyssey Systems Consulting Group Ltd", ats_id="icims")
    assert hit is not None, "the creds on file were missed — the exact 2026-08-24 miss"
    assert hit["match"] == "canonical" and hit["has_creds"] is True
    assert "odyssey" in hit["matched_on"]
    assert "confirm it is the same employer" in hit["caveat"]


def test_a_match_may_never_rest_on_generic_words_alone(monkeypatch):
    import ats_accounts

    monkeypatch.setattr(ats_accounts.accounts_mod, "list_accounts", lambda: [
        {"kind": "ats", "ats_id": "workday", "account_id": "ats_health_systems_group_workday",
         "company": "Health Systems Group", "status": "created", "has_creds": True},
    ])
    assert ats_accounts.find_existing(None, company="Consulting Group", ats_id="workday") is None
    # and a different employer sharing one generic word is not a match either
    assert ats_accounts.find_existing(None, company="Boston Group", ats_id="workday") is None


def test_a_different_ats_for_the_same_company_is_not_a_hit(monkeypatch):
    import ats_accounts

    monkeypatch.setattr(ats_accounts.accounts_mod, "list_accounts", lambda: [
        {"kind": "ats", "ats_id": "icims", "account_id": "ats_acme_icims",
         "company": "Acme", "status": "created", "has_creds": True},
    ])
    assert ats_accounts.find_existing(None, company="Acme", ats_id="workday") is None
    assert ats_accounts.find_existing(None, company="Acme", ats_id="icims")["match"] == "exact"
