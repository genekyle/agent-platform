"""Tests for stop-state escalation + challenge-frame (captcha) detection.

The frame detector is the fix for the live gap: a reCAPTCHA living in an iframe inside an
Indeed search page left the top-level url/text checks blind (needs_human=false). Scanning the
browser's full frame list catches it, and active-vs-passive avoids false-stopping on the
invisible enterprise badge that rides along on many pages."""

import escalation_rules as er


def test_active_recaptcha_challenge_frame_detected():
    # The bframe is the popup image-challenge — an active block.
    frames = [
        "https://www.indeed.com/jobs?q=analyst&l=Concord",
        "https://www.google.com/recaptcha/enterprise/anchor?ar=2&k=abc",
        "https://www.google.com/recaptcha/enterprise/bframe?hl=en&k=abc",
    ]
    hit = er.detect_block_frames(frames)
    assert hit is not None
    assert hit["provider"] == "recaptcha"
    assert hit["strength"] == "active"


def test_anchor_only_is_passive_not_a_hard_block():
    # Only the checkbox/anchor widget (no challenge popup) → passive, don't false-stop.
    frames = ["https://site.com/page", "https://www.google.com/recaptcha/enterprise/anchor?k=x"]
    hit = er.detect_block_frames(frames)
    assert hit is not None and hit["strength"] == "passive"


def test_active_beats_passive_when_both_present():
    frames = [
        "https://www.google.com/recaptcha/enterprise/anchor?k=x",
        "https://www.google.com/recaptcha/enterprise/bframe?k=x",
    ]
    assert er.detect_block_frames(frames)["strength"] == "active"


def test_other_providers_detected():
    assert er.detect_block_frames(["https://hcaptcha.com/captcha/v1"])["provider"] == "hcaptcha"
    assert er.detect_block_frames(["https://challenges.cloudflare.com/x"])["provider"] == "cloudflare_turnstile"


def test_no_challenge_frame_returns_none():
    assert er.detect_block_frames(["https://www.indeed.com/jobs", "https://x.com/sw.js"]) is None
    assert er.detect_block_frames([]) is None
    assert er.detect_block_frames(None) is None


# --- visibility refinement: a preloaded-but-hidden reCAPTCHA must not hard-stop -------------------
_ACTIVE = {"provider": "recaptcha", "strength": "active", "reason": "bframe present"}


def test_hidden_preload_downgrades_active_to_passive():
    # Indeed's case: bframe present in the frame list but nothing visibly shown.
    vis = {"ok": True, "challenge_visible": False, "checkbox_visible": False, "bframe_count": 1}
    out = er.downgrade_block_if_hidden(_ACTIVE, vis)
    assert out["strength"] == "passive"
    assert out["visibility"] is vis
    assert "not shown" in out["reason"]


def test_visible_challenge_stays_active():
    vis = {"ok": True, "challenge_visible": True, "checkbox_visible": False}
    assert er.downgrade_block_if_hidden(_ACTIVE, vis)["strength"] == "active"


def test_visible_checkbox_stays_active():
    # A shown v2 checkbox is itself a human action — keep it active.
    vis = {"ok": True, "challenge_visible": False, "checkbox_visible": True}
    assert er.downgrade_block_if_hidden(_ACTIVE, vis)["strength"] == "active"


def test_unreadable_or_missing_probe_stays_active():
    assert er.downgrade_block_if_hidden(_ACTIVE, {"ok": False})["strength"] == "active"
    assert er.downgrade_block_if_hidden(_ACTIVE, None)["strength"] == "active"


def test_passive_and_none_blocks_pass_through_untouched():
    passive = {"provider": "recaptcha_checkbox", "strength": "passive"}
    vis = {"ok": True, "challenge_visible": False, "checkbox_visible": False}
    assert er.downgrade_block_if_hidden(passive, vis) is passive
    assert er.downgrade_block_if_hidden(None, vis) is None
