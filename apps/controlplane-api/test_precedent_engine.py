"""The precedent rung in the student seat (§11 item 2): proposes from neighbors, abstains on
empty ground, keeps its own name through the cascade, fills the shadow seat by default, and
the scorecard's in-house numbers read it back."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from controller.bundle import build_bundle
from controller.decide import decide
from controller import shadow as shadow_mod
from interaction.decision import Decision

URL = "https://smartapply.indeed.com/questions/8839201"


class _NoPrograms:
    def get(self, task, state):
        return None


def _seed_store(root, n_click=5, n_observe=1):
    """Bank near-identical precedents for the questions state so the vote is deterministic."""
    from precedent.embedder import FusionEmbedder, PrecedentDoc, compose_decision_text
    from precedent.store import VectorStore

    store = VectorStore(root / "vectors.db")
    embedder = FusionEmbedder()
    text = compose_decision_text(
        goal_text="apply with indeed", state="indeed_apply_questions", url=URL,
        ax_identities=["button|Continue"], unanswered=[], expected_next=None)
    seeds = [("click", "Continue")] * n_click + [("observe", "")] * n_observe
    for i, (intent, ref) in enumerate(seeds):
        doc = PrecedentDoc(
            kind="decision", source_key=f"seed:{i}", text=text,
            facets={"platform": "indeed_quick_apply", "ats": "indeed_quick_apply",
                    "state": "indeed_apply_questions", "task": "indeed_apply"},
            intent=intent, ref=ref, session=f"s{i}",
            ats="indeed_quick_apply", state="indeed_apply_questions", task="indeed_apply")
        vec, hv = embedder.embed_doc(doc)
        store.add(doc, vec, hv)
    store.commit()
    store.close()


def _bundle():
    return build_bundle("indeed_apply", URL, goal_text="apply with indeed",
                        ax_candidates=[{"role": "button", "name": "Continue"}])


def test_propose_votes_intent_and_ref_from_neighbors(tmp_path, monkeypatch):
    monkeypatch.setenv("PRECEDENT_DATA_ROOT", str(tmp_path))
    _seed_store(tmp_path)
    from precedent.engine import propose

    decision = propose(_bundle())
    assert decision is not None
    assert decision.rung == "precedent"
    assert decision.intent == "click"
    assert decision.params.get("ref") == "Continue"
    assert 0.0 < decision.confidence <= 1.0
    assert "precedent vote" in decision.rationale and decision.evidence


def test_propose_abstains_on_empty_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PRECEDENT_DATA_ROOT", str(tmp_path))
    from precedent.engine import propose

    assert propose(_bundle()) is None   # no neighborhood -> abstain, never guess


def test_cascade_keeps_the_precedent_rung_name(tmp_path, monkeypatch):
    monkeypatch.setenv("PRECEDENT_DATA_ROOT", str(tmp_path))
    _seed_store(tmp_path)
    from precedent.engine import reasoner

    decision = decide(_bundle(), programs=_NoPrograms(), model=reasoner())
    # Confident -> acts as precedent; below the floor -> the escalate wrapper must still say
    # WHO guessed (the decide.py fix) — either way the journal reads "precedent".
    assert decision.rung == "precedent"
    assert decision.intent == "click"


def test_shadow_seat_defaults_to_precedent_and_respects_the_switch(monkeypatch):
    marker = Decision(intent="click", params={"ref": "X"}, confidence=0.99,
                      rung="precedent", rationale="precedent vote: fake", evidence=())
    monkeypatch.setattr("precedent.engine.reasoner", lambda: (lambda b: marker))
    from settings import settings

    monkeypatch.setattr(settings, "precedent_shadow", True)
    decision = shadow_mod.shadow_decision(_bundle(), programs=_NoPrograms())
    assert decision.rung == "precedent" and decision.params.get("ref") == "X"

    monkeypatch.setattr(settings, "precedent_shadow", False)
    decision = shadow_mod.shadow_decision(_bundle(), programs=_NoPrograms())
    assert decision.rung == "teacher"   # no seat-holder -> honest hand-up (no_program)


# --- the scorecard's in-house numbers ---------------------------------------
def _ts(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def test_in_house_share_counts_acted_rows_only():
    from routers.scorecard import _in_house_from_rows

    cutoff = datetime.now().astimezone() - timedelta(days=7)
    rows = [
        {"ts": _ts(1), "rung": "teacher"},
        {"ts": _ts(1), "rung": "recipe"},
        {"ts": _ts(1), "rung": "precedent"},
        {"ts": _ts(1), "rung": "teacher", "shadow": True},   # a measurement, not an act
        {"ts": _ts(30), "rung": "recipe"},                   # outside the window
    ]
    out = _in_house_from_rows(rows, cutoff)
    assert out["n"] == 3
    assert out["by_rung"] == {"teacher": 1, "recipe": 1, "precedent": 1}
    assert abs(out["share"] - (2 / 3)) < 1e-3


def test_precedent_shadow_coverage_and_agreement():
    from routers.scorecard import _precedent_shadow_from_rows

    cutoff = datetime.now().astimezone() - timedelta(days=7)
    rows = [
        {"ts": _ts(1), "shadow": True, "proposed_rung": "precedent",
         "proposed_intent": "click", "intent": "click"},
        {"ts": _ts(1), "shadow": True, "proposed_rung": "precedent",
         "proposed_intent": "click", "intent": "observe"},
        {"ts": _ts(1), "shadow": True, "proposed_rung": "teacher"},   # abstained seat
        {"ts": _ts(1), "shadow": False, "rung": "recipe"},            # not a pair
    ]
    out = _precedent_shadow_from_rows(rows, cutoff)
    assert out["shadow_pairs"] == 3 and out["proposed"] == 2
    assert abs(out["coverage"] - (2 / 3)) < 1e-3
    assert out["agreement"] == 0.5


def test_autonomy_counts_runs_and_operator_touches():
    from routers.scorecard import _autonomy_from_transitions

    rows = [
        # job A: submitted, fully in-house
        {"action": {"job_id": "indeed:a", "initiator": "teacher"},
         "rung": "enter_apply", "verdict": "confirmed"},
        {"action": {"job_id": "indeed:a", "initiator": "teacher"},
         "rung": "submit", "verdict": "confirmed"},
        # job B: applied per the ledger, but the operator pressed twice
        {"action": {"job_id": "indeed:b", "initiator": "operator"},
         "rung": "apply_step", "verdict": "confirmed"},
        {"action": {"job_id": "indeed:b", "initiator": "operator"},
         "rung": "submit", "verdict": "mismatch"},
        # job C: never reached submit and not applied -> not a run
        {"action": {"job_id": "indeed:c", "initiator": "operator"},
         "rung": "open_pane", "verdict": "confirmed"},
        # an operator row with no job linkage stays visible, never silently dropped
        {"action": {"initiator": "operator"}, "rung": "provisioned", "verdict": "read_only"},
    ]
    out = _autonomy_from_transitions(rows, applied_keys={"indeed:b"})
    assert out["runs_measured"] == 2 and out["jobs_seen"] == 3
    assert out["zero_touch"] == 1 and out["full_run_autonomy"] == 0.5
    assert out["avg_touches_per_run"] == 1.0
    assert out["unlinked_operator_rows"] == 1
