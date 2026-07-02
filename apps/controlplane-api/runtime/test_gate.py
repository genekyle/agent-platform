"""Tests for the runtime gates (captcha gate + compose).

The `/challenge_visibility` HTTP probe is faked by monkeypatching `httpx.Client`.
"""

from __future__ import annotations

from runtime import gate as gate_mod
from runtime.loop import Observation
from select_stage.schema import ActionId, ReasonCode, SelectionResult


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, reply):
        self._reply = reply

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, **k):
        if isinstance(self._reply, Exception):
            raise self._reply
        return _FakeResp(self._reply)


def _install(monkeypatch, reply):
    monkeypatch.setattr(gate_mod.httpx, "Client", _FakeClient(reply))


def _result():
    return SelectionResult(ActionId.CLICK, 5, 0.9, False, ReasonCode.SOM_PICK,
                           layer="cache", cost_usd=0.0, fingerprint="fp")


def _obs():
    return Observation(url="https://x.com", page_text="", ax_candidates=[], screenshot_path="")


def test_probe_captcha_returns_block_when_blocking(monkeypatch):
    _install(monkeypatch, {"ok": True, "blocking": True})
    block = gate_mod.probe_captcha(capture_server_url="http://mcp", browser_url="http://b")
    assert block is not None and block["reason"] == "captcha"


def test_probe_captcha_none_when_not_blocking(monkeypatch):
    _install(monkeypatch, {"ok": True, "blocking": False})
    assert gate_mod.probe_captcha(capture_server_url="http://mcp", browser_url="http://b") is None


def test_probe_captcha_none_on_error(monkeypatch):
    _install(monkeypatch, RuntimeError("down"))
    assert gate_mod.probe_captcha(capture_server_url="http://mcp", browser_url="http://b") is None


def test_captcha_gate_blocks_when_blocking(monkeypatch):
    _install(monkeypatch, {"ok": True, "blocking": True, "checkbox_visible": True})
    g = gate_mod.captcha_gate(capture_server_url="http://mcp", browser_url="http://b")
    block = g(_result(), _obs())
    assert block is not None
    assert block["reason"] == "captcha"
    assert "never auto-solve" in block["guidance"].lower()


def test_captcha_gate_allows_when_not_blocking(monkeypatch):
    _install(monkeypatch, {"ok": True, "blocking": False})
    g = gate_mod.captcha_gate(capture_server_url="http://mcp", browser_url="http://b")
    assert g(_result(), _obs()) is None


def test_captcha_gate_allows_when_probe_not_ok(monkeypatch):
    _install(monkeypatch, {"ok": False})
    g = gate_mod.captcha_gate(capture_server_url="http://mcp", browser_url="http://b")
    assert g(_result(), _obs()) is None


def test_captcha_gate_fails_open_on_probe_error(monkeypatch):
    """A flaky probe must not wedge every run — the gate allows the action through
    (classify + escalation rules remain the backstop for hard stop-states)."""
    _install(monkeypatch, RuntimeError("probe down"))
    g = gate_mod.captcha_gate(capture_server_url="http://mcp", browser_url="http://b")
    assert g(_result(), _obs()) is None


def _obs_with_named(bid, name, role="button"):
    return Observation(url="https://x.com", page_text="", screenshot_path="",
                       ax_candidates=[{"backend_node_id": bid, "role": role, "name": name,
                                       "bbox": {}}])


def _result_for(bid, action=ActionId.CLICK):
    return SelectionResult(action, bid, 0.95, False, ReasonCode.SOM_PICK,
                           layer="cache", cost_usd=0.0, fingerprint="fp")


def test_consequential_gate_blocks_submit_by_name():
    g = gate_mod.consequential_gate(allow=False)
    block = g(_result_for(9), _obs_with_named(9, "Submit application"))
    assert block is not None
    assert block["reason"] == "needs_approval"
    assert block["target"] == "Submit application"


def test_consequential_gate_blocks_submit_action_id():
    g = gate_mod.consequential_gate(allow=False)
    block = g(_result_for(9, action=ActionId.SUBMIT), _obs_with_named(9, "Go"))
    assert block is not None and block["action"] == "submit"


def test_consequential_gate_allows_benign_click():
    g = gate_mod.consequential_gate(allow=False)
    assert g(_result_for(9), _obs_with_named(9, "Continue")) is None


def test_consequential_gate_allow_true_is_transparent():
    assert gate_mod.consequential_gate(allow=True) is None


def test_compose_first_block_wins():
    hit = lambda r, o: {"reason": "second"}
    miss = lambda r, o: None
    g = gate_mod.compose(miss, hit, lambda r, o: {"reason": "third"})
    assert g(_result(), _obs())["reason"] == "second"


def test_compose_all_pass_returns_none():
    g = gate_mod.compose(lambda r, o: None, lambda r, o: None)
    assert g(_result(), _obs()) is None


def test_compose_drops_none_and_collapses():
    only = lambda r, o: {"reason": "x"}
    assert gate_mod.compose(None, only, None) is only     # single active gate returned as-is
    assert gate_mod.compose(None, None) is None
