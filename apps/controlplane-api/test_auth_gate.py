"""Tests for the domain-aware auth gate."""

from __future__ import annotations

import auth_gate


def test_facebook_login_wall_is_not_authed():
    # login-wall URL + logged-out panel text
    assert auth_gate.is_authenticated("facebook_marketplace", "https://www.facebook.com/login/",
                                      "Log into Facebook") is False
    assert auth_gate.is_authenticated("facebook_marketplace", "https://www.facebook.com/",
                                      "Log in to Facebook · Create new account") is False


def test_facebook_checkpoint_and_2fa_not_authed():
    assert auth_gate.is_authenticated("facebook_marketplace",
                                      "https://www.facebook.com/checkpoint/1", "") is False
    assert auth_gate.is_authenticated("facebook_marketplace",
                                      "https://www.facebook.com/two_step_verification/", "") is False


def test_facebook_feed_composer_is_authed():
    assert auth_gate.is_authenticated("facebook_marketplace", "https://www.facebook.com/",
                                      "What's on your mind?") is True


def test_facebook_ambiguous_is_unknown():
    # marketplace is browseable logged-out → don't claim either way
    assert auth_gate.is_authenticated("facebook_marketplace",
                                      "https://www.facebook.com/marketplace", "Today's picks") is None


def test_other_domains_return_unknown():
    # Indeed has its own probe elsewhere; this gate must never false-block it
    assert auth_gate.is_authenticated("indeed_jobs", "https://indeed.com/jobs", "Sign in") is None
    assert auth_gate.is_authenticated("", "https://x.com", "") is None


def test_auth_status_shape():
    s = auth_gate.auth_status("facebook_marketplace", "https://www.facebook.com/login/", "Log into Facebook")
    assert s["authed"] is False
    assert s["state"] == "fb_login_wall"
    assert s["guidance"]                       # a concrete next step when logged out
    ok = auth_gate.auth_status("facebook_marketplace", "https://www.facebook.com/", "What's on your mind?")
    assert ok["authed"] is True and ok["guidance"] is None
