"""Tests for the tab-finder ('tab manager') scoping + the account-lifecycle gating logic
(operator directive 2026-07-19: scope discovery to Career-Search sessions; 'Login' only after the
account is fully CREATED, not merely has creds)."""

from __future__ import annotations

import ats_accounts
import tab_finder


def test_is_career_search_session():
    # Where account creation applies — Indeed / Career Search / an ATS.
    assert tab_finder.is_career_search_session("indeed_jobs")
    assert tab_finder.is_career_search_session("career_search")
    assert tab_finder.is_career_search_session("workday")
    assert tab_finder.is_career_search_session("greenhouse")
    # NOT a career-search context — never scanned for the account feature.
    assert not tab_finder.is_career_search_session("facebook_marketplace")
    assert not tab_finder.is_career_search_session("gmail")
    assert not tab_finder.is_career_search_session(None)


def test_ats_url_substrings_uses_login_url_host_then_platform():
    subs = tab_finder.ats_url_substrings(
        {"login_url": "https://bilh.wd1.myworkdayjobs.com/External", "ats_id": "workday"})
    assert subs[0] == "bilh.wd1.myworkdayjobs.com"      # the tenant host is tried FIRST (most specific)
    assert "myworkdayjobs.com" in subs                  # then the platform domain


def test_login_leg_only_at_active_checkpoint(monkeypatch):
    """next_account_action gates 'Login' on status=='active' — a fully-created checkpoint. A pending
    account WITH creds still routes to Create (staged password ≠ created on the ATS)."""
    def _acct(status, has_creds=False):
        return lambda aid: {"status": status, "has_creds": has_creds, "username_hint": "g***@x"}

    monkeypatch.setattr("accounts.get_account", _acct("pending"))
    assert ats_accounts.next_account_action("Acme", "workday")["leg"] == "create_account"

    # has_creds must NOT unlock Login — the key part of the directive.
    monkeypatch.setattr("accounts.get_account", _acct("pending", has_creds=True))
    r = ats_accounts.next_account_action("Acme", "workday")
    assert r["leg"] == "create_account" and r["button"] == "Create Account"

    monkeypatch.setattr("accounts.get_account", _acct("active", has_creds=True))
    r = ats_accounts.next_account_action("Acme", "workday")
    assert r["leg"] == "sign_in" and r["button"] == "Sign In"
