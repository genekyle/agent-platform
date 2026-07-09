"""Facebook Marketplace routes — recipe spec, listing schema, and listing drafts.

Extracted from main.py (router split — docs/PLAN_main-split.md). Self-contained:
each handler defers its heavy import (facebook_recipe, facebook_listing_schema,
listing_draft) inside the function, so this module has no import-time coupling.
"""
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class ListingDraftBody(BaseModel):
    id: Optional[str] = None            # omit to create; include to update
    title: str = ""
    price: str = ""
    category: str = ""
    condition: str = ""
    description: str = ""
    location: str = ""
    photos: list[str] = []
    status: Optional[str] = None


@router.get("/api/runtime/facebook_recipe")
def facebook_recipe_spec():
    """Serve the seeded Facebook login + create-listing recipes (the dashboard renders these
    as the flow the loop will drive). Seeded, not yet live-verified."""
    import facebook_recipe
    return facebook_recipe.recipe_spec()


@router.get("/api/facebook/listing_schema")
def facebook_listing_schema():
    """FB Marketplace 'Item for sale' schema mirrored from the live create-listing UI — categories,
    conditions, colors, and the conditional fields each category reveals. The UI renders these as
    DROPDOWNS (not free-text) so a typo can't desync an item's category/condition from FB's taxonomy.
    Probed via CDP-AX; see facebook_listing_schema.py."""
    import facebook_listing_schema
    return facebook_listing_schema.listing_schema()


@router.get("/api/facebook/listings")
def facebook_list_drafts():
    """The operator's Marketplace listing drafts — the inputs a create-listing run fills from."""
    import listing_draft
    return {"drafts": listing_draft.list_drafts()}


@router.post("/api/facebook/listings")
def facebook_upsert_draft(body: ListingDraftBody):
    """Create or update a listing draft. Required to run a create-listing flow: title + price."""
    import listing_draft
    draft = listing_draft.upsert(body.model_dump(exclude_none=True))
    from dataclasses import asdict as _asdict
    return {"draft": _asdict(draft), "missing_required": draft.missing_required()}
