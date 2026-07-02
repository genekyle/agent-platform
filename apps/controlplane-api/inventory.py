"""Inventory — the channel-agnostic selling model behind the marketplace dashboard.

The design principle (operator's): internal INVENTORY is the source of truth; a marketplace
(Facebook today, eBay/OfferUp/Shopify later) is just a SALES CHANNEL an item may be posted to.
So the model separates the item from its per-channel listing, and nothing is hard-coded to
Facebook — `channel` is a field, defaulting to "facebook_marketplace".

Four entities, persisted together in one JSON doc (<artifacts>/cache/inventory.json — operator
-scale data, a lock + a file is plenty):

  * Item              — one physical thing to sell (title, price, condition, photos...). Truth.
  * MarketplaceListing — that item posted to ONE channel (url, status, response counts). 1 item
                         → N listings (one per channel), so the same inventory fans out later.
  * QueueTask         — a unit of work to run against a channel (post this item), with retry/error.
  * AgentLog          — an append-only activity log; every action drops an entry here.

The action functions (add_to_queue, run_queue, check_responses, mark_sold...) are real state
machines + logging today; the queue runner SIMULATES the actual post for now (clearly flagged),
leaving a single seam where the live runner loop (run_live create-listing) plugs in once an
authenticated channel session exists — so today's manual buttons become tomorrow's agent tasks
without reshaping the data.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from settings import settings

_lock = threading.Lock()

DEFAULT_CHANNEL = "facebook_marketplace"

# Internal item lifecycle (source-of-truth status, channel-independent).
ITEM_STATUSES = ("draft", "ready_to_post", "queued", "posting", "posted", "active",
                 "needs_attention", "sold", "error", "archived")
# Per-channel listing lifecycle.
LISTING_STATUSES = ("active", "needs_attention", "expired", "removed", "sold", "inactive")
# Queue task lifecycle.
TASK_STATUSES = ("waiting", "running", "posted", "failed", "skipped", "needs_review")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Entities ----------------------------------------------------------------
@dataclass
class Item:
    id: str
    title: str = ""
    description: str = ""
    category: str = ""
    price: str = ""
    condition: str = ""
    photos: list[str] = field(default_factory=list)
    pickup_location: str = ""
    internal_status: str = "draft"
    notes: str = ""                       # agent/operator notes
    created_at: str = ""
    updated_at: str = ""


@dataclass
class MarketplaceListing:
    id: str
    item_id: str
    channel: str = DEFAULT_CHANNEL
    listing_url: Optional[str] = None
    listing_status: str = "active"
    external_listing_id: Optional[str] = None
    response_count: int = 0
    unread_response_count: int = 0
    simulated: bool = False               # True until a real post replaces it
    posted_at: str = ""
    last_checked_at: Optional[str] = None


@dataclass
class QueueTask:
    id: str
    item_id: str
    task_type: str = "post"               # post | check_responses | sync | ...
    channel: str = DEFAULT_CHANNEL
    status: str = "waiting"
    priority: int = 100                   # lower = sooner
    attempts: int = 0
    last_attempt_at: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str = ""
    completed_at: Optional[str] = None


@dataclass
class AgentLog:
    id: str
    timestamp: str
    action_type: str
    status: str = "ok"                    # ok | error | info
    item_id: Optional[str] = None
    queue_task_id: Optional[str] = None
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# --- Persistence (one JSON doc) ----------------------------------------------
def _path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    p = base / "cache" / "inventory.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _blank() -> dict[str, list]:
    return {"items": [], "listings": [], "queue": [], "log": []}


def _load() -> dict[str, list]:
    p = _path()
    if not p.exists():
        return _blank()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        for k in ("items", "listings", "queue", "log"):
            data.setdefault(k, [])
        return data
    except Exception:
        return _blank()


def _save(doc: dict[str, list]) -> None:
    _path().write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _log_into(doc: dict, action_type: str, *, status: str = "ok", item_id: Optional[str] = None,
              queue_task_id: Optional[str] = None, message: str = "",
              metadata: Optional[dict] = None) -> dict:
    entry = asdict(AgentLog(id=_new_id("log"), timestamp=_now(), action_type=action_type,
                            status=status, item_id=item_id, queue_task_id=queue_task_id,
                            message=message, metadata=metadata or {}))
    doc["log"].append(entry)
    return entry


# --- Items -------------------------------------------------------------------
_ITEM_FIELDS = {"title", "description", "category", "price", "condition", "photos",
                "pickup_location", "internal_status", "notes"}


def _num(price: str) -> Optional[float]:
    try:
        return float(str(price).replace("$", "").replace(",", "").strip())
    except Exception:
        return None


def list_items(*, status: Optional[str] = None, category: Optional[str] = None,
               channel_status: Optional[str] = None, price_min: Optional[float] = None,
               price_max: Optional[float] = None, search: Optional[str] = None) -> list[dict]:
    doc = _load()
    listings_by_item = _listings_by_item(doc)
    rows = doc["items"]
    out = []
    q = (search or "").strip().lower()
    for it in rows:
        if status and it.get("internal_status") != status:
            continue
        if category and (it.get("category") or "").lower() != category.lower():
            continue
        if q and q not in (it.get("title") or "").lower():
            continue
        p = _num(it.get("price", ""))
        if price_min is not None and (p is None or p < price_min):
            continue
        if price_max is not None and (p is None or p > price_max):
            continue
        ch = listings_by_item.get(it["id"], [])
        if channel_status and not any(l.get("listing_status") == channel_status for l in ch):
            continue
        out.append(_item_view(it, ch))
    out.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return out


def _item_view(it: dict, channel_listings: list[dict]) -> dict:
    """An item enriched with a rollup of its channel listings (for the table row)."""
    active = [l for l in channel_listings if l.get("listing_status") in ("active", "needs_attention")]
    responses = sum(int(l.get("response_count") or 0) for l in channel_listings)
    unread = sum(int(l.get("unread_response_count") or 0) for l in channel_listings)
    last_checked = max((l.get("last_checked_at") or "" for l in channel_listings), default="") or None
    primary = channel_listings[0] if channel_listings else None
    return {
        **it,
        "channels": channel_listings,
        "listing_status": primary.get("listing_status") if primary else None,
        "listing_url": primary.get("listing_url") if primary else None,
        "active_listing_count": len(active),
        "response_count": responses,
        "unread_response_count": unread,
        "last_checked_at": last_checked,
    }


def get_item(item_id: str) -> Optional[dict]:
    doc = _load()
    it = next((i for i in doc["items"] if i["id"] == item_id), None)
    if it is None:
        return None
    return _item_view(it, _listings_by_item(doc).get(item_id, []))


def create_item(data: dict) -> dict:
    clean = {k: v for k, v in (data or {}).items() if k in _ITEM_FIELDS}
    with _lock:
        doc = _load()
        item = Item(id=_new_id("item"), created_at=_now(), updated_at=_now(), **clean)
        if item.internal_status not in ITEM_STATUSES:
            item.internal_status = "draft"
        doc["items"].append(asdict(item))
        _log_into(doc, "item_created", item_id=item.id, message=f"Added {item.title or 'item'} to inventory")
        _save(doc)
    return get_item(item.id)


def update_item(item_id: str, data: dict) -> Optional[dict]:
    clean = {k: v for k, v in (data or {}).items() if k in _ITEM_FIELDS}
    with _lock:
        doc = _load()
        it = next((i for i in doc["items"] if i["id"] == item_id), None)
        if it is None:
            return None
        it.update(clean)
        it["updated_at"] = _now()
        _log_into(doc, "item_updated", item_id=item_id, message=f"Edited {it.get('title') or 'item'}")
        _save(doc)
    return get_item(item_id)


def set_item_status(item_id: str, status: str, *, message: str = "") -> Optional[dict]:
    if status not in ITEM_STATUSES:
        return None
    with _lock:
        doc = _load()
        it = next((i for i in doc["items"] if i["id"] == item_id), None)
        if it is None:
            return None
        it["internal_status"] = status
        it["updated_at"] = _now()
        _log_into(doc, f"item_{status}", item_id=item_id,
                  message=message or f"{it.get('title') or 'Item'} → {status}")
        _save(doc)
    return get_item(item_id)


def mark_sold(item_id: str) -> Optional[dict]:
    return set_item_status(item_id, "sold", message="Marked sold")


def archive_item(item_id: str) -> Optional[dict]:
    return set_item_status(item_id, "archived", message="Archived")


# --- Listings ----------------------------------------------------------------
def _listings_by_item(doc: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for l in doc["listings"]:
        out.setdefault(l["item_id"], []).append(l)
    return out


def list_listings(*, active_only: bool = False, channel: Optional[str] = None) -> list[dict]:
    doc = _load()
    items_by_id = {i["id"]: i for i in doc["items"]}
    out = []
    for l in doc["listings"]:
        if channel and l.get("channel") != channel:
            continue
        if active_only and l.get("listing_status") not in ("active", "needs_attention"):
            continue
        it = items_by_id.get(l["item_id"], {})
        out.append({**l, "item_title": it.get("title"), "item_price": it.get("price")})
    out.sort(key=lambda r: r.get("posted_at") or "", reverse=True)
    return out


# --- Queue -------------------------------------------------------------------
def add_to_queue(item_ids: list[str], *, channel: str = DEFAULT_CHANNEL,
                 task_type: str = "post") -> dict:
    added = []
    with _lock:
        doc = _load()
        existing_item_ids = {i["id"] for i in doc["items"]}
        waiting = {(t["item_id"], t["channel"]) for t in doc["queue"]
                   if t["status"] in ("waiting", "running")}
        for iid in item_ids or []:
            if iid not in existing_item_ids or (iid, channel) in waiting:
                continue
            task = QueueTask(id=_new_id("qt"), item_id=iid, task_type=task_type,
                             channel=channel, created_at=_now())
            doc["queue"].append(asdict(task))
            added.append(task.id)
            it = next((i for i in doc["items"] if i["id"] == iid), None)
            if it is not None:
                it["internal_status"] = "queued"
                it["updated_at"] = _now()
            _log_into(doc, "queued", item_id=iid, queue_task_id=task.id,
                      message=f"Added {(it or {}).get('title') or 'item'} to the {channel} posting queue")
        _save(doc)
    return {"added": added, "count": len(added)}


def list_queue() -> list[dict]:
    doc = _load()
    items_by_id = {i["id"]: i for i in doc["items"]}
    rows = []
    order = {s: n for n, s in enumerate(("running", "waiting", "needs_review", "failed", "skipped", "posted"))}
    for pos, t in enumerate(doc["queue"]):
        it = items_by_id.get(t["item_id"], {})
        rows.append({**t, "position": pos + 1, "item_title": it.get("title"),
                     "item_price": it.get("price")})
    rows.sort(key=lambda r: (order.get(r["status"], 9), r.get("priority", 100), r.get("created_at") or ""))
    return rows


def run_queue(*, dry_run: bool = True, limit: int = 0) -> dict:
    """Process WAITING post tasks. For now the actual channel post is SIMULATED (a listing is
    created and flagged simulated=True) — the single seam where the live runner loop plugs in
    later. Honest by design: nothing claims a real post URL. Returns a run summary."""
    processed = []
    with _lock:
        doc = _load()
        items_by_id = {i["id"]: i for i in doc["items"]}
        waiting = [t for t in doc["queue"] if t["status"] == "waiting" and t["task_type"] == "post"]
        _log_into(doc, "queue_run_started", message=f"Started posting queue ({len(waiting)} waiting)")
        n = 0
        for t in waiting:
            if limit and n >= limit:
                break
            n += 1
            t["status"] = "running"
            t["attempts"] = int(t.get("attempts", 0)) + 1
            t["last_attempt_at"] = _now()
            it = items_by_id.get(t["item_id"])
            if it is None:
                t["status"] = "skipped"
                t["error_message"] = "item no longer exists"
                _log_into(doc, "queue_task_skipped", queue_task_id=t["id"], status="error",
                          message="Skipped a queued task — its item was removed")
                continue
            it["internal_status"] = "posting"
            # --- SEAM: real posting via the runner loop goes here (dry_run=False) ---
            listing = MarketplaceListing(
                id=_new_id("lst"), item_id=it["id"], channel=t["channel"],
                listing_status="active", simulated=bool(dry_run), posted_at=_now(),
                response_count=0, unread_response_count=0)
            doc["listings"].append(asdict(listing))
            t["status"] = "posted"
            t["completed_at"] = _now()
            it["internal_status"] = "active"
            it["updated_at"] = _now()
            processed.append(t["id"])
            _log_into(doc, "posted", item_id=it["id"], queue_task_id=t["id"],
                      message=f"Posted {it.get('title') or 'item'} to {t['channel']}"
                              + (" (simulated — connect the runner loop to post for real)" if dry_run else ""),
                      metadata={"simulated": bool(dry_run), "listing_id": listing.id})
        _log_into(doc, "queue_run_finished", message=f"Posting queue finished ({len(processed)} posted)")
        _save(doc)
    return {"posted": processed, "count": len(processed), "dry_run": dry_run}


def retry_failed() -> dict:
    with _lock:
        doc = _load()
        n = 0
        for t in doc["queue"]:
            if t["status"] in ("failed", "needs_review"):
                t["status"] = "waiting"
                t["error_message"] = None
                n += 1
        if n:
            _log_into(doc, "queue_retry", message=f"Reset {n} failed task(s) to waiting")
        _save(doc)
    return {"reset": n}


def remove_tasks(task_ids: list[str]) -> dict:
    ids = set(task_ids or [])
    with _lock:
        doc = _load()
        before = len(doc["queue"])
        doc["queue"] = [t for t in doc["queue"] if t["id"] not in ids]
        removed = before - len(doc["queue"])
        if removed:
            _log_into(doc, "queue_remove", message=f"Removed {removed} task(s) from the queue")
        _save(doc)
    return {"removed": removed}


def clear_completed() -> dict:
    with _lock:
        doc = _load()
        before = len(doc["queue"])
        doc["queue"] = [t for t in doc["queue"] if t["status"] not in ("posted", "skipped")]
        cleared = before - len(doc["queue"])
        if cleared:
            _log_into(doc, "queue_clear_completed", message=f"Cleared {cleared} completed task(s)")
        _save(doc)
    return {"cleared": cleared}


# --- Monitoring --------------------------------------------------------------
def check_responses(*, channel: Optional[str] = None) -> dict:
    """Stamp a marketplace check across active listings. Real buyer-message reading is a future
    runner task; today this records the check time + logs it honestly (no fabricated messages)."""
    with _lock:
        doc = _load()
        checked = 0
        for l in doc["listings"]:
            if l.get("listing_status") not in ("active", "needs_attention"):
                continue
            if channel and l.get("channel") != channel:
                continue
            l["last_checked_at"] = _now()
            checked += 1
        _log_into(doc, "check_responses", message=f"Checked {checked} active listing(s), 0 new responses",
                  metadata={"checked": checked})
        _save(doc)
    return {"checked": checked, "new_responses": 0, "checked_at": _now()}


# --- Log + overview ----------------------------------------------------------
def list_log(*, limit: int = 50) -> list[dict]:
    doc = _load()
    return list(reversed(doc["log"]))[:limit] if limit else list(reversed(doc["log"]))


def overview() -> dict:
    doc = _load()
    items = doc["items"]
    listings = doc["listings"]
    queue = doc["queue"]

    def n_items(status):
        return sum(1 for i in items if i.get("internal_status") == status)

    active_listings = [l for l in listings if l.get("listing_status") in ("active", "needs_attention")]
    unread = sum(int(l.get("unread_response_count") or 0) for l in listings)
    with_responses = sum(1 for l in listings if int(l.get("unread_response_count") or 0) > 0)
    last_check = max((l.get("last_checked_at") or "" for l in listings), default="") or None
    return {
        "total_items": len(items),
        "draft": n_items("draft") + n_items("ready_to_post"),
        "queued": sum(1 for t in queue if t.get("status") in ("waiting", "running")),
        "active_listings": len(active_listings),
        "items_with_responses": with_responses,
        "needs_attention": n_items("needs_attention") + n_items("error"),
        "sold": n_items("sold"),
        "unread_responses": unread,
        "last_checked_at": last_check,
    }
