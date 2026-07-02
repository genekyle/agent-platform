"""Tests for the ListingDraft store + the field-value matcher."""

from __future__ import annotations

import listing_draft as ld
from listing_draft import ListingDraft


def _draft(**kw):
    base = dict(id="lst_test", title="Trek bike", price="450", condition="Used - Good",
                description="Great commuter", category="Bicycles", location="Nashua, NH")
    base.update(kw)
    return ListingDraft(**base)


def test_value_for_field_matches_by_label():
    d = _draft()
    assert ld.value_for_field(d, "Title") == "Trek bike"
    assert ld.value_for_field(d, "Price") == "450"
    assert ld.value_for_field(d, "Condition") == "Used - Good"
    assert ld.value_for_field(d, "Description") == "Great commuter"
    assert ld.value_for_field(d, "What are you selling?") == "Trek bike"


def test_value_for_field_unknown_returns_none():
    assert ld.value_for_field(_draft(), "Tax ID") is None


def test_value_for_field_empty_value_returns_none():
    assert ld.value_for_field(_draft(location=""), "Location") is None


def test_missing_required():
    assert _draft().missing_required() == []
    assert set(_draft(title="", price="").missing_required()) == {"title", "price"}


def test_upsert_create_then_update(monkeypatch, tmp_path):
    path = tmp_path / "listing_drafts.json"
    monkeypatch.setattr(ld, "_path", lambda: path)

    created = ld.upsert({"title": "Couch", "price": "100"})
    assert created.id.startswith("lst_")
    assert created.title == "Couch"
    assert ld.list_drafts()[0]["id"] == created.id

    updated = ld.upsert({"id": created.id, "price": "80", "status": "listed"})
    assert updated.price == "80"
    assert updated.status == "listed"
    assert updated.title == "Couch"                 # preserved across the update
    assert len(ld.list_drafts()) == 1               # updated in place, not duplicated

    fetched = ld.get_draft(created.id)
    assert fetched is not None and fetched.price == "80"


def test_upsert_ignores_unknown_fields(monkeypatch, tmp_path):
    path = tmp_path / "listing_drafts.json"
    monkeypatch.setattr(ld, "_path", lambda: path)
    d = ld.upsert({"title": "Lamp", "price": "20", "evil": "nope"})
    assert not hasattr(d, "evil")
