"""Tests for the Haiku page-state teacher — parsing, guards, and budget gate.

The Anthropic client is injected as a fake, so these run with zero API cost.
"""

import io
import json
from pathlib import Path

import pytest
from PIL import Image

import anthropic_usage
from select_stage import haiku_page_state


def _png(tmp_path: Path) -> Path:
    p = tmp_path / "shot.png"
    Image.new("RGB", (40, 30), (200, 200, 200)).save(p)
    return p


class _FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeResp:
    def __init__(self, payload):
        self.content = [_FakeBlock(json.dumps(payload))]
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5,
                                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})()


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload
        self.messages = self

    def create(self, **kwargs):
        return _FakeResp(self._payload)


_STATES = [
    {"state_id": "login_wall", "display_name": "Login wall", "description": "must sign in"},
    {"state_id": "home_feed", "display_name": "Home feed", "description": "logged-in home"},
]


def test_classifies_known_state(tmp_path):
    client = _FakeClient({"state_id": "login_wall", "confidence": 0.95,
                          "needs_human": False, "is_new": False, "proposed_name": ""})
    out = haiku_page_state.classify(screenshot_path=_png(tmp_path), candidate_states=_STATES,
                                    url="https://x.com/login", client=client)
    assert out["state_id"] == "login_wall"
    assert out["confidence"] == 0.95
    assert out["is_new"] is False


def test_hallucinated_id_is_rejected_as_new(tmp_path):
    # Model returns an id not in the menu → coerced to is_new, state_id cleared.
    client = _FakeClient({"state_id": "totally_made_up", "confidence": 0.8,
                          "needs_human": False, "is_new": False, "proposed_name": ""})
    out = haiku_page_state.classify(screenshot_path=_png(tmp_path), candidate_states=_STATES,
                                    url="https://x.com", client=client)
    assert out["state_id"] == ""
    assert out["is_new"] is True
    assert out["proposed_name"] == "totally_made_up"


def test_new_state_proposal(tmp_path):
    client = _FakeClient({"state_id": "", "confidence": 0.6, "needs_human": False,
                          "is_new": True, "proposed_name": "two_factor_prompt"})
    out = haiku_page_state.classify(screenshot_path=_png(tmp_path), candidate_states=_STATES,
                                    url="https://x.com", client=client)
    assert out["is_new"] is True
    assert out["proposed_name"] == "two_factor_prompt"


def test_empty_candidate_states_abstains_without_api(tmp_path):
    out = haiku_page_state.classify(screenshot_path=_png(tmp_path), candidate_states=[], url="https://x.com")
    assert out["state_id"] == "" and out["is_new"] is True
    assert out["cost_usd"] == 0.0


def test_budget_gate_blocks_before_call(tmp_path, monkeypatch):
    def _over(_limit=None):
        raise anthropic_usage.BudgetExceededError(
            {"spent_usd": 9.99, "limit_usd": 5.0, "period": "7d"})

    monkeypatch.setattr(anthropic_usage, "enforce_budget", _over)
    with pytest.raises(anthropic_usage.BudgetExceededError):
        haiku_page_state.classify(screenshot_path=_png(tmp_path), candidate_states=_STATES,
                                  url="https://x.com", client=_FakeClient({}))
