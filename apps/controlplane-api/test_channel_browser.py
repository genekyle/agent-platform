"""Tests for channel browser supervision (config + the CDP reachability health check)."""

from __future__ import annotations

import channel_browser


class _Resp:
    def __init__(self, code):
        self.status_code = code


class _Client:
    def __init__(self, code=None, boom=False):
        self._code = code
        self._boom = boom

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        if self._boom:
            raise ConnectionError("refused")
        return _Resp(self._code)


def test_channel_config():
    cfg = channel_browser.channel_config("facebook_marketplace")
    assert cfg and cfg["profile"] == "facebook" and cfg["domain_id"] == "facebook_marketplace"
    assert channel_browser.channel_config("nope") is None


def test_cdp_reachable_true(monkeypatch):
    monkeypatch.setattr(channel_browser.httpx, "Client", _Client(code=200))
    assert channel_browser.cdp_reachable(9333) is True


def test_cdp_reachable_non_200(monkeypatch):
    monkeypatch.setattr(channel_browser.httpx, "Client", _Client(code=500))
    assert channel_browser.cdp_reachable(9333) is False


def test_cdp_reachable_connection_error_is_false(monkeypatch):
    monkeypatch.setattr(channel_browser.httpx, "Client", _Client(boom=True))
    assert channel_browser.cdp_reachable(9333) is False


def test_cdp_reachable_no_port():
    assert channel_browser.cdp_reachable(None) is False
    assert channel_browser.cdp_reachable(0) is False
