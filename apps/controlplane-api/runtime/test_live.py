"""Tests for the live adapters (LiveProposer / LiveActor) and the shared trace builder.

The HTTP boundary (mcp capture/execute) is faked by monkeypatching `httpx.Client`, so
these exercise the adapter logic — payload shaping, dpr resolution, resilience — without a
browser or a running mcp server.
"""

from __future__ import annotations

import json

from runtime import live
from runtime.live import LiveActor, LiveProposer, observation_from_trace
from runtime.loop import Observation


# --- a fake httpx.Client -----------------------------------------------------
class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Records POSTs and replies from a scripted {path_suffix: payload|Exception} map."""
    last_posts: list = []

    def __init__(self, replies):
        self._replies = replies

    def __call__(self, *a, **k):   # so it can stand in for httpx.Client(...)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, **k):
        _FakeClient.last_posts.append({"url": url, "json": json})
        for suffix, reply in self._replies.items():
            if url.endswith(suffix):
                if isinstance(reply, Exception):
                    raise reply
                return _FakeResp(reply)
        return _FakeResp({}, status=404)


def _install(monkeypatch, replies):
    _FakeClient.last_posts = []
    client = _FakeClient(replies)
    monkeypatch.setattr(live.httpx, "Client", client)
    return client


# --- a capture on disk for observation_from_trace ----------------------------
def _write_capture(tmp_path, filename="cap.json", *, url="https://x.com/p", with_ax=True):
    traces = tmp_path / "observer-traces"
    traces.mkdir(parents=True, exist_ok=True)
    artifact = {
        "acquisition": {
            "page_identity": {"url": url},
            "js_state": {"body_text_preview": "hello world"},
            "viewport_state": {"device_scale_factor": 2.0},
            "screenshots": [{"path": "/shots/x.png"}],
            "task_context": {"goal": "click apply"},
            "actionable_elements": [],
        }
    }
    (traces / filename).write_text(json.dumps(artifact))
    if with_ax:
        (traces / f"{filename}.ax.json").write_text(json.dumps({"proposals": [
            {"backend_node_id": 5, "role": "button", "name": "Apply",
             "bbox": {"x": 10, "y": 20, "width": 40, "height": 12}, "_debug": {"dpr": 2.0}},
        ]}))
    return traces


def test_observation_from_trace_builds_observation(tmp_path):
    traces = _write_capture(tmp_path)
    obs, goal = observation_from_trace(traces, "cap.json")
    assert obs.url == "https://x.com/p"
    assert goal == "click apply"
    assert len(obs.ax_candidates) == 1
    assert obs.viewport["device_scale_factor"] == 2.0


def test_observation_from_trace_missing_raises(tmp_path):
    traces = tmp_path / "observer-traces"
    traces.mkdir(parents=True)
    try:
        observation_from_trace(traces, "nope.json")
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_live_proposer_captures_then_builds(monkeypatch, tmp_path):
    traces = _write_capture(tmp_path)
    _install(monkeypatch, {"/capture": {"filename": "cap.json"}})
    p = LiveProposer(capture_server_url="http://mcp", browser_url="http://b", traces_dir=traces,
                     tab_url="x.com", goal="click apply")
    obs = p()
    assert obs.url == "https://x.com/p"
    assert p.last_filename == "cap.json"
    post = _FakeClient.last_posts[-1]
    assert post["url"].endswith("/capture")
    assert post["json"]["browser_url"] == "http://b"
    assert post["json"]["task_context"]["goal"] == "click apply"


def test_live_proposer_capture_failure_returns_empty(monkeypatch, tmp_path):
    traces = _write_capture(tmp_path)
    _install(monkeypatch, {"/capture": RuntimeError("mcp down")})
    p = LiveProposer(capture_server_url="http://mcp", browser_url="http://b", traces_dir=traces)
    obs = p()
    assert obs.ax_candidates == []      # degraded, not crashed
    assert obs.url == ""


def test_live_proposer_no_filename_returns_empty(monkeypatch, tmp_path):
    traces = _write_capture(tmp_path)
    _install(monkeypatch, {"/capture": {"filename": None}})
    p = LiveProposer(capture_server_url="http://mcp", browser_url="http://b", traces_dir=traces)
    assert p().ax_candidates == []


def _obs_with_candidate() -> Observation:
    return Observation(
        url="https://x.com/p", page_text="", screenshot_path="/s.png",
        viewport={"device_scale_factor": 2.0},
        ax_candidates=[{"backend_node_id": 5, "role": "button", "name": "Apply",
                        "bbox": {"x": 10, "y": 20, "width": 40, "height": 12}, "_debug": {"dpr": 2.0}}],
    )


def test_live_actor_executes_and_shapes_payload(monkeypatch):
    _install(monkeypatch, {"/execute": {"ok": True, "driver": "humanized", "detail": "clicked"}})
    a = LiveActor(capture_server_url="http://mcp", browser_url="http://b", tab_url="x.com")
    res = a.perform(action_id="click", target_backend_node_id=5,
                    target_bbox={"x": 10, "y": 20, "width": 40, "height": 12},
                    value=None, observation=_obs_with_candidate())
    assert res.executed is True
    assert res.driver == "humanized"
    payload = _FakeClient.last_posts[-1]["json"]
    assert payload["driver"] == "humanized"
    assert payload["backend_node_id"] == 5
    assert payload["device_scale_factor"] == 2.0
    # act-by-name: the selected candidate's role+name travel so the executor re-resolves fresh
    assert payload["target_role"] == "button"
    assert payload["target_name"] == "Apply"


def test_live_actor_record_only_never_executes(monkeypatch):
    _install(monkeypatch, {"/execute": {"ok": True, "driver": "record_only"}})
    a = LiveActor(capture_server_url="http://mcp", browser_url="http://b", record_only=True)
    res = a.perform(action_id="click", target_backend_node_id=5, target_bbox={},
                    value=None, observation=_obs_with_candidate())
    assert res.executed is False                 # dry run → not executed → loop hands off
    assert _FakeClient.last_posts[-1]["json"]["driver"] == "record_only"


def test_live_actor_executor_error_returns_not_executed(monkeypatch):
    _install(monkeypatch, {"/execute": RuntimeError("cdp boom")})
    a = LiveActor(capture_server_url="http://mcp", browser_url="http://b")
    res = a.perform(action_id="click", target_backend_node_id=5, target_bbox={},
                    value=None, observation=_obs_with_candidate())
    assert res.executed is False
    assert "cdp boom" in res.detail


def test_primed_proposer_serves_prefetched_then_delegates():
    from runtime.live import PrimedProposer

    calls = {"n": 0}

    class _Base:
        last_filename = "cap_2.json"

        def __call__(self):
            calls["n"] += 1
            return Observation(url="https://second", page_text="", ax_candidates=[], screenshot_path="")

    primed = Observation(url="https://first", page_text="", ax_candidates=[], screenshot_path="")
    base = _Base()
    p = PrimedProposer(primed, base)
    assert p().url == "https://first"          # first call: the pre-fetched observation
    assert calls["n"] == 0                       # base NOT called yet
    assert p().url == "https://second"          # second call delegates to base
    assert calls["n"] == 1
    assert p.last_filename == "cap_2.json"       # proxies the base's latest filename
