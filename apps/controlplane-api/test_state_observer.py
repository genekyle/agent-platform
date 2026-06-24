"""Tests for the coarse auth-stage observer (L3 v0): feature extraction + NB train/eval."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import state_observer


@dataclass
class FakeCapture:
    artifact_filename: str
    observed_page_state: Optional[str]


def _artifact(url: str, labels: list[str], roles: list[str]) -> dict:
    return {
        "acquisition": {"page_identity": {"url": url}},
        "ranked_candidates": [
            {"target": {"role": r, "label": l}}
            for r, l in zip(roles, labels)
        ],
    }


def test_extract_features_includes_route_role_and_tokens():
    feats = state_observer.extract_features(
        _artifact("https://example.com/login", ["Log In", "Password"], ["button", "textbox"]))
    assert any(f.startswith("route:") for f in feats)
    assert "role:button" in feats
    assert "tok:log" in feats and "tok:password" in feats


def _write(traces_dir: Path, fn: str, artifact: dict):
    (traces_dir / fn).write_text(json.dumps(artifact), encoding="utf-8")


def test_train_separates_auth_from_unauth(tmp_path: Path):
    traces = tmp_path / "observer-traces"
    traces.mkdir(parents=True)

    stage_by_state = {"login_wall": "unauthenticated", "home_feed": "authenticated"}
    captures = []
    # Several clearly-separable examples per class so the held-out split is learnable.
    for i in range(5):
        fn = f"unauth_{i}.json"
        _write(traces, fn, _artifact("https://site.com/login",
                                     ["Log In", "Sign up", "Password"], ["button", "link", "textbox"]))
        captures.append(FakeCapture(fn, "login_wall"))
    for i in range(5):
        fn = f"auth_{i}.json"
        _write(traces, fn, _artifact("https://site.com/home",
                                     ["Account", "Sign out", "Inbox"], ["button", "link", "navigation"]))
        captures.append(FakeCapture(fn, "home_feed"))

    result = state_observer.train_stage_observer(tmp_path, captures=captures, stage_by_state=stage_by_state)
    assert result["ok"] is True
    assert result["metrics"]["accuracy"] >= 0.8
    assert set(result["metrics"]["label_counts"]) == {"authenticated", "unauthenticated"}


def test_predict_picks_trained_class(tmp_path: Path):
    traces = tmp_path / "observer-traces"
    traces.mkdir(parents=True)
    stage_by_state = {"login_wall": "unauthenticated", "home_feed": "authenticated"}
    captures = []
    for i in range(4):
        fn = f"u{i}.json"
        _write(traces, fn, _artifact("https://s.com/login", ["Log In", "Password"], ["button", "textbox"]))
        captures.append(FakeCapture(fn, "login_wall"))
        fn = f"a{i}.json"
        _write(traces, fn, _artifact("https://s.com/home", ["Sign out", "Account"], ["button", "link"]))
        captures.append(FakeCapture(fn, "home_feed"))
    res = state_observer.train_stage_observer(tmp_path, captures=captures, stage_by_state=stage_by_state)
    model = json.loads((Path(res["model_dir"]) / "model.json").read_text())

    login_feats = state_observer.extract_features(
        _artifact("https://s.com/login", ["Log In", "Password"], ["button", "textbox"]))
    assert state_observer.predict(model, login_feats)["label"] == "unauthenticated"


def test_insufficient_data_returns_not_ok(tmp_path: Path):
    (tmp_path / "observer-traces").mkdir(parents=True)
    res = state_observer.train_stage_observer(tmp_path, captures=[], stage_by_state={})
    assert res["ok"] is False
    assert res["reason"] == "insufficient_labeled_data"


def test_unmapped_states_are_skipped(tmp_path: Path):
    traces = tmp_path / "observer-traces"
    traces.mkdir(parents=True)
    _write(traces, "x.json", _artifact("https://s.com/x", ["Hi"], ["button"]))
    captures = [FakeCapture("x.json", "some_unregistered_state")]
    res = state_observer.train_stage_observer(tmp_path, captures=captures, stage_by_state={})
    # No usable labels → not ok, and the skip is attributed.
    assert res["ok"] is False
    assert res["skipped"]["no_stage_mapping"] == 1
