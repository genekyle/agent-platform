"""`_capture_generic` and the error that traveled as content (fixed 2026-09-02).

An MCP server answers an unknown tool with `isError=true` content, not an exception — so for
weeks every artifact's `accessibility_snapshot` recorded the literal string
"MCP error -32602: Tool get_accessibility_tree not found" as a SUCCESSFUL snapshot and the
candidate fallthrough never ran. These pin the read-point: an error-shaped answer is a failed
candidate, and a run out of candidates is an honest `unavailable`, never a poisoned success."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app import main as app_main


def test_content_error_detects_wrapped_mcp_error():
    wrapped = [{"raw_text": "MCP error -32602: Tool get_accessibility_tree not found"}]
    assert app_main._content_error(wrapped) is not None
    assert app_main._content_error([{"raw_text": "role: button name: Apply"}]) is None
    assert app_main._content_error([]) is None
    assert app_main._content_error({"nodes": []}) is None
    # two real entries -> a real snapshot, even if one mentions the words
    assert app_main._content_error(
        [{"raw_text": "MCP error docs"}, {"raw_text": "node"}]) is None


class _Session:
    """call_tool answers per-tool from a script; unknown tools answer isError content —
    exactly what a live MCP server does instead of raising."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    async def call_tool(self, name, params):
        self.calls.append(name)
        if name in self.script:
            return self.script[name]
        return SimpleNamespace(
            isError=True,
            content=[SimpleNamespace(type="text",
                                     text=f"MCP error -32602: Tool {name} not found")])


def test_all_candidates_error_yields_unavailable_not_poison():
    status: dict = {}
    out = asyncio.run(app_main._capture_generic(_Session({}), "accessibility_snapshot", status))
    assert out == []                                            # the documented empty shape
    entry = status["accessibility_snapshot"]
    assert (entry.get("status") or entry.get("state")) != "success"
    # every candidate was actually tried — the fallthrough the bug had disabled
    assert len(_Session({}).script) == 0
    detail = str(entry)
    assert "get_accessibility_tree" in detail


def test_error_shaped_payload_falls_through_to_next_candidate(monkeypatch):
    good = SimpleNamespace(isError=False, content=[])
    session = _Session({"accessibility_snapshot": good})

    def fake_normalize(result):
        if result is good:
            return [{"raw_text": "role: button name: Apply"}]
        return [{"raw_text": "MCP error -32602: Tool get_accessibility_tree not found"}]

    monkeypatch.setattr(app_main, "normalize_capture_tool_payload", fake_normalize)
    status: dict = {}
    out = asyncio.run(app_main._capture_generic(session, "accessibility_snapshot", status))
    assert out == [{"raw_text": "role: button name: Apply"}]
    # candidate 1 (get_accessibility_tree) answered isError and was skipped; candidate 2 won
    assert session.calls[:2] == ["get_accessibility_tree", "accessibility_snapshot"]
    assert str(status["accessibility_snapshot"]).find("success") != -1
