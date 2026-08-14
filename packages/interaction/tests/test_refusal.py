"""A refusal carries the way out, or says why there is none.

Named on 2026-08-13 after three instances, and four MORE appeared on 08-14 — which is what makes
this a type rather than another entry in LEARNINGS. The class regenerates because a refusal is a
string and its exit is built somewhere else, with nothing binding them.
"""

from __future__ import annotations

import pytest
from interaction.refusal import Exit, Refusal, handed_over


def test_a_refusal_with_no_way_out_cannot_be_constructed():
    """The mechanism. Every new refusal starts life without an exit, and whether it ever got one
    depended on somebody noticing — so not noticing is now a crash."""
    with pytest.raises(ValueError, match="no way out"):
        Refusal(what="Cannot advance.", why="the form has unanswered required fields.")


def test_the_error_names_both_ways_to_satisfy_it():
    """A refusal the caller cannot act on is still a dead end — including a refusal to a
    programmer."""
    with pytest.raises(ValueError) as e:
        Refusal(what="Cannot advance.", why="something.")
    msg = str(e.value)
    assert "exit=" in msg and "no_exit_because=" in msg


def test_an_exit_makes_it_constructible_and_renders_as_a_button():
    r = Refusal(
        what="There is no application tab open to advance.",
        why="the step's page was closed while another step was being tidied.",
        exit=Exit(label="Start it again", endpoint="/apply_reopen",
                  body={"job_id": "indeed:bch", "reason": "its tab was closed"},
                  why="Re-walks this application from the posting."))
    d = r.as_dict()
    assert d["exit"]["label"] == "Start it again"
    assert d["exit"]["endpoint"] == "/apply_reopen"
    assert d["exit"]["body"]["job_id"] == "indeed:bch"
    assert "no_exit_because" not in d


def test_a_boundary_may_have_no_button_but_must_say_who_acts_instead():
    """`no_exit_because` is a REAL answer — a captcha, a credential, a federal self-ID are all
    cases where handing over is correct and no button may be offered. What it is not is a
    default."""
    r = handed_over("This screen is a captcha.", "we never solve one.", to="the operator")
    assert r.exit is None
    assert "operator" in r.no_exit_because
    assert "no_exit_because" in r.as_dict()


def test_a_shrug_is_not_a_reason():
    """Below a readable length the field becomes the default it exists not to be."""
    with pytest.raises(ValueError, match="somebody can read"):
        Refusal(what="Nope.", why="because.", no_exit_because="n/a")


def test_a_refusal_cannot_have_both():
    with pytest.raises(ValueError, match="never both"):
        Refusal(what="a", why="b", exit=Exit(label="x", endpoint="/y"),
                no_exit_because="this is the operator's to do, and nothing here may do it")


def test_a_refusal_must_say_what_and_why():
    with pytest.raises(ValueError, match="WHAT"):
        Refusal(what="", why="b", no_exit_because="the operator types this one, we never do")
    with pytest.raises(ValueError, match="WHY"):
        Refusal(what="a", why="", no_exit_because="the operator types this one, we never do")


def test_prose_is_unchanged_so_migrating_a_call_site_is_additive():
    """Every migrated site returned a sentence and keeps returning the same sentence — the button
    is new, the words the operator already reads are not."""
    r = Refusal(what="This screen still wants 2 answers.", why="Zip and Country are empty.",
                exit=Exit(label="Answer them", endpoint="/apply_fill", body={"execute": False},
                          why="Read the form and fill what the profile knows."))
    assert str(r) == ("This screen still wants 2 answers. Zip and Country are empty. "
                      "Read the form and fill what the profile knows.")
    assert r.as_dict()["detail"] == str(r)


def test_evidence_rides_so_the_surface_can_render_the_work():
    """The 2026-08-10 audit's core finding: a refusal must carry the form it refused OVER, not
    prose pointing at an endpoint the cockpit never shows."""
    r = Refusal(what="Cannot advance.", why="2 required fields are unanswered.",
                evidence={"form_scan": {"unanswered": [{"field": "Zip*"}]}},
                exit=Exit(label="Answer them", endpoint="/apply_fill", body={}))
    assert r.as_dict()["evidence"]["form_scan"]["unanswered"][0]["field"] == "Zip*"


def test_round_trips_across_the_process_boundary():
    original = Refusal(what="a.", why="b.", evidence={"k": 1},
                       exit=Exit(label="Go", endpoint="/x", body={"n": 2}, why="does a thing",
                                 consequential=True))
    import json
    back = Refusal.from_dict(json.loads(json.dumps(original.as_dict())))
    assert back is not None
    assert back.exit is not None
    assert back.exit.consequential is True and back.exit.body == {"n": 2}
    assert back.evidence == {"k": 1}


def test_a_payload_that_is_not_a_refusal_parses_as_none_rather_than_a_guess():
    """An unmigrated caller has not told us its exit, and inventing one is the same defect as
    inventing a measurement."""
    for payload in (None, {}, {"detail": "some prose"}, [], "no"):
        assert Refusal.from_dict(payload) is None
