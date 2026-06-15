"""Phase 7 tests — ActionVerifierV1 (pure AX/DOM delta predicate + retry policy).

Run from apps/controlplane-api:
    ../../.venv/bin/python -m pytest select_stage/test_verifier.py -q
"""

from select_stage import verifier as v
from select_stage.verifier import Snapshot


def snap(**over):
    base = dict(url="https://fb.com/login", route="fb.com/login",
                ax=frozenset({("textbox", "Email"), ("button", "Log In")}),
                ax_by_backend={}, dialog_present=False, active_element="", body_text="Log in to Facebook", scroll_y=0.0)
    base.update(over)
    return Snapshot(**base)


def test_click_with_change_passes():
    before = snap()
    after = snap(route="fb.com/home", body_text="Welcome back")  # navigated
    r = v.verify(action_id="click", before=before, after=after)
    assert r.ok and "route" in r.observed


def test_click_with_no_change_fails():
    before = snap()
    after = snap()  # identical -> the click did nothing
    r = v.verify(action_id="click", before=before, after=after)
    assert not r.ok and "NO expected change" in r.reason


def test_type_value_appears_in_text_passes():
    before = snap(body_text="Email")
    after = snap(body_text="Email alice@example.com")
    r = v.verify(action_id="type", before=before, after=after, expected_value="alice@example.com")
    assert r.ok


def test_type_no_change_fails():
    before = snap()
    after = snap()
    r = v.verify(action_id="type", before=before, after=after, expected_value="secret")
    assert not r.ok


def test_submit_requires_navigation():
    assert v.verify(action_id="submit", before=snap(), after=snap(route="fb.com/home")).ok
    assert not v.verify(action_id="submit", before=snap(), after=snap()).ok


def test_scroll_detected():
    assert v.verify(action_id="scroll", before=snap(scroll_y=0), after=snap(scroll_y=400)).ok
    assert not v.verify(action_id="scroll", before=snap(scroll_y=0), after=snap(scroll_y=2)).ok  # within eps


def test_dialog_appearance_counts_as_click_change():
    before = snap()
    after = snap(dialog_present=True)
    assert v.verify(action_id="click", before=before, after=after).ok


def test_next_step_bounded_retry():
    ok = v.VerificationResult(ok=True, predicted="", observed="", reason="")
    bad = v.VerificationResult(ok=False, predicted="", observed="", reason="")
    assert v.next_step(ok, 0) == "ok"
    assert v.next_step(bad, 0) == "retry"          # first failure -> retry
    assert v.next_step(bad, v.MAX_RETRIES) == "escalate"  # exhausted -> human


def test_snapshot_from_artifact():
    artifact = {"acquisition": {
        "page_identity": {"url": "https://fb.com/item/42"},
        "frame_state": {"dialog_present": True, "active_element": {"tag": "input"}},
        "viewport_state": {"scroll_y": 120},
        "js_state": {"body_text_preview": "hello"},
    }}
    ax = [{"role": "button", "caption": "Go", "backend_node_id": 7}]
    s = v.snapshot_from_artifact(artifact, ax)
    assert s.route == "fb.com/item/{id}" and s.dialog_present and s.scroll_y == 120
    assert ("button", "Go") in s.ax and s.ax_by_backend[7] == ("button", "Go")
