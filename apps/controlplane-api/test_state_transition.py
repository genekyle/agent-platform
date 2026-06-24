"""Tests for the state-transition model (planner look-ahead edge-model)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import state_transition


@dataclass
class FakeCapture:
    artifact_filename: str
    observed_page_state: Optional[str]
    post_action_state: Optional[str]
    action_type_hint: str = "click"
    domain_id: str = "indeed"


def _caps(triples):
    return [FakeCapture(f"t{i}.json", a, b, action) for i, (a, action, b) in enumerate(triples)]


def test_predicts_exact_edge(tmp_path: Path):
    caps = _caps([("login_email", "submit", "passcode_entry")] * 4
                 + [("passcode_entry", "submit", "home")] * 4)
    res = state_transition.train_transition_model(tmp_path, captures=caps)
    assert res["ok"] is True
    import json
    model = json.loads((Path(res["model_dir"]) / "model.json").read_text())
    pred = state_transition.predict(model, "login_email", "submit")
    assert pred["label"] == "passcode_entry"
    assert pred["backoff"] is False


def test_backoff_when_action_unseen(tmp_path: Path):
    caps = _caps([("home", "click", "menu_open")] * 3)
    import json
    res = state_transition.train_transition_model(tmp_path, captures=caps)
    model = json.loads((Path(res["model_dir"]) / "model.json").read_text())
    # action 'scroll' never seen at 'home' → falls back to all transitions from 'home'.
    pred = state_transition.predict(model, "home", "scroll")
    assert pred["label"] == "menu_open"
    assert pred["backoff"] is True


def test_unknown_from_state_returns_none(tmp_path: Path):
    caps = _caps([("home", "click", "menu_open")] * 2)
    import json
    res = state_transition.train_transition_model(tmp_path, captures=caps)
    model = json.loads((Path(res["model_dir"]) / "model.json").read_text())
    pred = state_transition.predict(model, "never_seen_state", "click")
    assert pred["label"] is None
    assert pred["reason"] == "unknown_from_state"


def test_captures_without_transition_pair_are_skipped(tmp_path: Path):
    caps = [FakeCapture("a.json", "home", None), FakeCapture("b.json", None, "x")]
    res = state_transition.train_transition_model(tmp_path, captures=caps)
    assert res["ok"] is False
    assert res["reason"] == "insufficient_transition_pairs"
    assert res["skipped"]["no_transition_pair"] == 2


def test_ranked_distribution_normalizes(tmp_path: Path):
    # From 'fork', 'submit' lands on 'a' twice and 'b' once → a ranks first, probs sum ~1.
    caps = _caps([("fork", "submit", "a"), ("fork", "submit", "a"), ("fork", "submit", "b")])
    import json
    res = state_transition.train_transition_model(tmp_path, captures=caps)
    model = json.loads((Path(res["model_dir"]) / "model.json").read_text())
    pred = state_transition.predict(model, "fork", "submit")
    assert pred["label"] == "a"
    assert abs(sum(r["prob"] for r in pred["ranked"]) - 1.0) < 1e-6
