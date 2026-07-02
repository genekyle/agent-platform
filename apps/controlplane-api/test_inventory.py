"""Tests for the channel-agnostic inventory model (items / listings / queue / log)."""

from __future__ import annotations

import pytest

import inventory


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, "_path", lambda: tmp_path / "inventory.json")


def _item(**kw):
    base = dict(title="Nike Tech Hoodie", price="45", condition="Used - Good", category="Apparel")
    base.update(kw)
    return inventory.create_item(base)


def test_create_and_list_item():
    it = _item()
    assert it["id"].startswith("item_")
    assert it["internal_status"] == "draft"
    rows = inventory.list_items()
    assert len(rows) == 1 and rows[0]["title"] == "Nike Tech Hoodie"


def test_filters_and_search():
    _item(title="Nike Hoodie", price="45", category="Apparel")
    _item(title="Gaming Chair", price="120", category="Furniture")
    assert len(inventory.list_items(search="gaming")) == 1
    assert len(inventory.list_items(category="Apparel")) == 1
    assert len(inventory.list_items(price_min=100)) == 1
    assert len(inventory.list_items(price_max=50)) == 1
    assert len(inventory.list_items(status="draft")) == 2


def test_update_item():
    it = _item()
    upd = inventory.update_item(it["id"], {"price": "40", "description": "barely worn"})
    assert upd["price"] == "40" and upd["description"] == "barely worn"
    assert upd["title"] == "Nike Tech Hoodie"     # preserved


def test_queue_add_dedupes_and_sets_status():
    it = _item()
    r1 = inventory.add_to_queue([it["id"]])
    assert r1["count"] == 1
    r2 = inventory.add_to_queue([it["id"]])          # already waiting → not re-added
    assert r2["count"] == 0
    assert inventory.get_item(it["id"])["internal_status"] == "queued"
    q = inventory.list_queue()
    assert len(q) == 1 and q[0]["status"] == "waiting" and q[0]["position"] == 1


def test_run_queue_simulates_post_and_creates_listing():
    it = _item()
    inventory.add_to_queue([it["id"]])
    res = inventory.run_queue(dry_run=True)
    assert res["count"] == 1 and res["dry_run"] is True
    # item advanced to active, a simulated listing exists
    assert inventory.get_item(it["id"])["internal_status"] == "active"
    listings = inventory.list_listings(active_only=True)
    assert len(listings) == 1
    assert listings[0]["simulated"] is True
    assert listings[0]["item_title"] == "Nike Tech Hoodie"
    # queue task marked posted
    assert inventory.list_queue()[0]["status"] == "posted"


def test_run_queue_separates_item_from_listing():
    """The core future-proofing: one Item, a separate MarketplaceListing per channel."""
    it = _item()
    inventory.add_to_queue([it["id"]], channel="facebook_marketplace")
    inventory.run_queue(dry_run=True)
    view = inventory.get_item(it["id"])
    assert view["title"] == "Nike Tech Hoodie"        # item is the source of truth
    assert len(view["channels"]) == 1                  # listing is a separate connected object
    assert view["channels"][0]["channel"] == "facebook_marketplace"


def test_clear_completed_and_retry():
    it = _item()
    inventory.add_to_queue([it["id"]])
    inventory.run_queue(dry_run=True)
    assert inventory.clear_completed()["cleared"] == 1
    assert inventory.list_queue() == []


def test_mark_sold_and_archive():
    it = _item()
    assert inventory.mark_sold(it["id"])["internal_status"] == "sold"
    assert inventory.archive_item(it["id"])["internal_status"] == "archived"


def test_check_responses_stamps_and_logs():
    it = _item()
    inventory.add_to_queue([it["id"]])
    inventory.run_queue(dry_run=True)
    res = inventory.check_responses()
    assert res["checked"] == 1 and res["new_responses"] == 0
    assert inventory.list_listings()[0]["last_checked_at"]


def test_overview_counts():
    a = _item(title="A")
    _item(title="B")
    inventory.add_to_queue([a["id"]])
    ov = inventory.overview()
    assert ov["total_items"] == 2
    assert ov["queued"] == 1
    assert ov["draft"] == 1          # B still draft; A is now queued


def test_log_records_actions_newest_first():
    it = _item()
    inventory.add_to_queue([it["id"]])
    log = inventory.list_log()
    assert log[0]["action_type"] == "queued"
    assert any(e["action_type"] == "item_created" for e in log)
