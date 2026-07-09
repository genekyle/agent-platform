"""Inventory routes — channel-agnostic selling model (items / listings / queue / log).

Extracted from main.py (router split — docs/PLAN_main-split.md). Internal inventory is the
source of truth; a marketplace is a sales channel. Handlers delegate to the `inventory`
module (lazy import); _validate_marketplace_account guards create/post against the accounts
registry. Self-contained: no DB session, no main helpers.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class ItemBody(BaseModel):
    title: str = ""
    description: str = ""
    category: str = ""
    price: str = ""
    condition: str = ""
    photos: list[str] = []
    pickup_location: str = ""
    internal_status: Optional[str] = None
    notes: str = ""
    attributes: dict[str, str] = {}       # category-conditional fields (Color/Material/SKU/…) — FB names


class QueueAddBody(BaseModel):
    item_ids: list[str]
    channel: str = "facebook_marketplace"
    task_type: str = "post"
    account_id: Optional[str] = None       # which account to post as


class IdsBody(BaseModel):
    task_ids: list[str] = []


class CreateListingBody(BaseModel):
    account_id: Optional[str] = None       # which account this listing is posted under
    channel: str = "facebook_marketplace"
    listing_url: Optional[str] = None      # set → records a REAL post; omit → a stub listing
    external_listing_id: Optional[str] = None


def _validate_marketplace_account(account_id: Optional[str]):
    """A create-listing/post account must be a real, active facebook_marketplace account. None is
    allowed (unattributed) so nothing breaks, but a BAD id is rejected loudly."""
    if not account_id:
        return
    import accounts
    acct = accounts.get_account(account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail=f"Unknown account '{account_id}'")
    if acct["domain_id"] != "facebook_marketplace":
        raise HTTPException(status_code=400, detail=f"Account '{account_id}' is not a Facebook Marketplace account")
    if acct["status"] != "active":
        raise HTTPException(status_code=400, detail=f"Account '{account_id}' is disabled")


@router.get("/api/inventory/overview")
def inventory_overview():
    import inventory
    return inventory.overview()


@router.post("/api/inventory/items/{item_id}/create-listing")
def inventory_create_listing(item_id: str, body: CreateListingBody):
    """Create a Marketplace listing for an item, tied to the account it's posted under. Pass a
    listing_url to record a REAL post you made; omit it for a stub the live drive fills later."""
    import inventory
    _validate_marketplace_account(body.account_id)
    view = inventory.create_listing(
        item_id, account_id=body.account_id, channel=body.channel,
        listing_url=body.listing_url, external_listing_id=body.external_listing_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"item": view}


@router.post("/api/inventory/reset")
def inventory_reset():
    """Wipe ALL inventory (items/listings/queue/log) — clears example/seed data for a clean slate."""
    import inventory
    return inventory.reset()


@router.get("/api/inventory/items")
def inventory_items(status: Optional[str] = None, category: Optional[str] = None,
                    channel_status: Optional[str] = None, price_min: Optional[float] = None,
                    price_max: Optional[float] = None, search: Optional[str] = None):
    import inventory
    return {"items": inventory.list_items(status=status, category=category,
                                          channel_status=channel_status, price_min=price_min,
                                          price_max=price_max, search=search)}


@router.post("/api/inventory/items")
def inventory_create_item(body: ItemBody):
    import inventory
    return {"item": inventory.create_item(body.model_dump(exclude_none=True))}


@router.get("/api/inventory/items/{item_id}")
def inventory_get_item(item_id: str):
    import inventory
    item = inventory.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"item": item}


@router.patch("/api/inventory/items/{item_id}")
def inventory_update_item(item_id: str, body: ItemBody):
    import inventory
    item = inventory.update_item(item_id, body.model_dump(exclude_none=True))
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"item": item}


@router.post("/api/inventory/items/{item_id}/sold")
def inventory_mark_sold(item_id: str):
    import inventory
    item = inventory.mark_sold(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"item": item}


@router.post("/api/inventory/items/{item_id}/archive")
def inventory_archive(item_id: str):
    import inventory
    item = inventory.archive_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"item": item}


@router.delete("/api/inventory/items/{item_id}")
def inventory_delete_item(item_id: str):
    import inventory
    if not inventory.delete_item(item_id):
        raise HTTPException(status_code=404, detail="Item not found")
    return {"ok": True, "id": item_id}


@router.get("/api/inventory/queue")
def inventory_queue():
    import inventory
    return {"queue": inventory.list_queue()}


@router.post("/api/inventory/queue")
def inventory_queue_add(body: QueueAddBody):
    import inventory
    _validate_marketplace_account(body.account_id)
    return inventory.add_to_queue(body.item_ids, channel=body.channel,
                                  task_type=body.task_type, account_id=body.account_id)


@router.post("/api/inventory/queue/run")
def inventory_queue_run(dry_run: bool = True, limit: int = 0):
    import inventory
    return inventory.run_queue(dry_run=dry_run, limit=limit)


@router.post("/api/inventory/queue/retry")
def inventory_queue_retry():
    import inventory
    return inventory.retry_failed()


@router.post("/api/inventory/queue/remove")
def inventory_queue_remove(body: IdsBody):
    import inventory
    return inventory.remove_tasks(body.task_ids)


@router.post("/api/inventory/queue/clear")
def inventory_queue_clear():
    import inventory
    return inventory.clear_completed()


@router.get("/api/inventory/listings")
def inventory_listings(active_only: bool = False, channel: Optional[str] = None):
    import inventory
    return {"listings": inventory.list_listings(active_only=active_only, channel=channel)}


@router.post("/api/inventory/check-responses")
def inventory_check_responses(channel: Optional[str] = None):
    import inventory
    return inventory.check_responses(channel=channel)


@router.get("/api/inventory/log")
def inventory_log(limit: int = 50):
    import inventory
    return {"log": inventory.list_log(limit=limit)}
