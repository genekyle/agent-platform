"""Phase 1-4 tests — fingerprint, cache, and the orchestrator cascade.

Haiku is mocked (no API calls, no cost). Cache/telemetry are redirected to a
tmp dir so tests don't touch real artifacts.

Run from apps/controlplane-api:
    ../../.venv/bin/python -m pytest select_stage/test_select_core.py -q
"""

import pytest

from select_stage import cache, fingerprint, selector
from select_stage import haiku_selector as hs
from select_stage.schema import ActionId, ReasonCode


@pytest.fixture(autouse=True)
def _tmp_artifacts(tmp_path, monkeypatch):
    """Point cache + telemetry at a tmp dir (absolute, so the _path helpers use it)."""
    from settings import settings
    monkeypatch.setattr(settings, "observer_artifacts_dir", str(tmp_path), raising=False)


def _ax(bid, role, name, x=10, y=20):
    return {"backend_node_id": bid, "role": role, "caption": name,
            "bbox": {"x": x, "y": y, "width": 100, "height": 30}}


# --- Phase 1: fingerprint ----------------------------------------------------
def test_route_template_ids():
    assert fingerprint.route_template("https://www.facebook.com/marketplace/item/123?ref=x") \
        == "www.facebook.com/marketplace/item/{id}"
    assert fingerprint.route_template("https://x.com/u/550e8400-e29b-41d4-a716-446655440000") \
        == "x.com/u/{id}"
    assert fingerprint.route_template("https://x.com/login/") == "x.com/login"


def test_viewport_class_buckets():
    assert fingerprint.viewport_class(1200, 700) == "md-landscape"
    assert fingerprint.viewport_class(1206, 700) == "md-landscape"  # bucket stable to small resize
    assert fingerprint.viewport_class(390, 844) == "sm-portrait"


def test_fingerprint_stable_across_content_but_not_layout():
    vp = {"viewport_width": 1200, "viewport_height": 700}
    cands = [_ax(1, "textbox", "Email"), _ax(2, "button", "Log In")]
    fp_a = fingerprint.compute(url="https://fb.com/item/111", viewport=vp, candidates=cands, task_goal="login")
    fp_b = fingerprint.compute(url="https://fb.com/item/222", viewport=vp, candidates=cands, task_goal="login")
    fp_c = fingerprint.compute(url="https://fb.com/item/111", viewport=vp,
                               candidates=cands + [_ax(3, "link", "Forgot?")], task_goal="login")
    assert fp_a == fp_b           # different content id -> same fingerprint (templated)
    assert fp_a != fp_c           # different candidate set -> different fingerprint


# --- Phase 2: cache ----------------------------------------------------------
def test_cache_roundtrip_and_version():
    from select_stage.schema import candidates_from_ax
    cands = candidates_from_ax([_ax(5, "textbox", "Password")])
    fp = "abc123"
    assert cache.lookup(fingerprint=fp, task_goal="enter pw", candidates=cands) is None
    cache.store(fingerprint=fp, task_goal="enter pw", chosen=cands[0], action_id=ActionId.TYPE)
    hit = cache.lookup(fingerprint=fp, task_goal="enter pw", candidates=cands)
    assert hit is not None and hit["candidate"].backend_node_id == 5 and hit["action_id"] == ActionId.TYPE
    # element gone in a later capture -> miss
    other = candidates_from_ax([_ax(9, "button", "Submit")])
    assert cache.lookup(fingerprint=fp, task_goal="enter pw", candidates=other) is None


# --- Phase 4: orchestrator ---------------------------------------------------
def _common(**over):
    base = dict(url="https://fb.com/login", task_goal="the password field",
                ax_candidates=[_ax(5, "textbox", "Password"), _ax(6, "button", "Log In")],
                screenshot_path="/nonexistent.png",
                viewport={"viewport_width": 1200, "viewport_height": 700})
    base.update(over)
    return base


def test_stop_state_escalates_before_haiku(monkeypatch):
    monkeypatch.setattr(hs, "pick", lambda **k: pytest.fail("Haiku must not run on a stop-state"))
    r = selector.select(**_common(url="https://www.facebook.com/two_step_verification/authentication"))
    assert r.needs_human and r.reason_code == ReasonCode.STOP_STATE and r.layer == "classify"


def test_haiku_resolves_then_cache_hit_avoids_haiku(monkeypatch):
    from select_stage.schema import candidates_from_ax
    calls = {"n": 0}

    def fake_pick(**k):
        calls["n"] += 1
        # candidates passed are Candidate objects already; pick the Password textbox
        target = next(c for c in k["candidates"] if c.name == "Password")
        return {"action_id": ActionId.TYPE, "candidate": target, "confidence": 0.95,
                "needs_human": False, "reason_code": ReasonCode.SOM_PICK, "cost_usd": 0.0023, "mark": target.mark}

    monkeypatch.setattr(hs, "pick", fake_pick)
    r1 = selector.select(**_common())
    assert r1.reason_code == ReasonCode.SOM_PICK and r1.target_backend_node_id == 5 and r1.action_id == ActionId.TYPE
    assert calls["n"] == 1
    # second identical call -> cache hit, Haiku NOT called again
    r2 = selector.select(**_common())
    assert r2.reason_code == ReasonCode.CACHE_HIT and r2.layer == "cache" and r2.cost_usd == 0.0
    assert calls["n"] == 1  # unchanged


def test_low_confidence_escalates(monkeypatch):
    def fake_pick(**k):
        target = k["candidates"][0]
        return {"action_id": ActionId.CLICK, "candidate": target, "confidence": 0.2,
                "needs_human": False, "reason_code": ReasonCode.SOM_PICK, "cost_usd": 0.0023, "mark": 1}
    monkeypatch.setattr(hs, "pick", fake_pick)
    r = selector.select(**_common(task_goal="ambiguous thing"))
    assert r.needs_human and r.reason_code == ReasonCode.LOW_CONFIDENCE


def test_budget_exceeded_escalates(monkeypatch):
    import anthropic_usage
    def boom(**k):
        raise anthropic_usage.BudgetExceededError({"spent_usd": 9.0, "limit_usd": 5.0, "period": "rolling_7d"})
    monkeypatch.setattr(hs, "pick", boom)
    r = selector.select(**_common(task_goal="something new and uncached"))
    assert r.needs_human and r.reason_code == ReasonCode.BUDGET_EXCEEDED
