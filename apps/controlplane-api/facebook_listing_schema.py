"""Facebook Marketplace 'Item for sale' listing SCHEMA — mirrored from the LIVE create-listing UI.

Probed live 2026-07-08 through the CDP-AX layer against the real create-listing form
(facebook.com/marketplace/create/item) so our own item config can offer the SAME constrained
dropdowns FB does. The point: a free-text category/condition invites a typo that silently desyncs
from FB's taxonomy and confuses the fill step — a dropdown of the exact FB option labels can't.

When FB reveals category-specific CONDITIONAL fields (apparel adds Color + Material), we mirror those
too, so driving the live form holds no surprises (the same reason vehicles ask make/model — out of
scope here; see below).

Scope: the "Item for sale" listing type ONLY. "Vehicle for sale" and "Home for sale or rent" are
separate listing types with their own heavy conditional forms — deliberately out of scope until we
sell those. Refresh by re-probing when FB changes the form. See PRINCIPLES §7 (mirror the observed
UI, don't assume).
"""

from __future__ import annotations

# Top-level "Item for sale" categories — verbatim FB accessible-names (the dropdown option labels).
# Order matches the live dropdown.
CATEGORIES: list[str] = [
    "Tools", "Furniture", "Household", "Garden", "Appliances", "Video Games",
    "Books, Movies & Music", "Bags & Luggage", "Women's clothing & shoes",
    "Men's clothing & shoes", "Jewelry & Accessories", "Health & beauty", "Pet Supplies",
    "Baby & kids", "Toys & Games", "Electronics & computers", "Mobile phones", "Bicycles",
    "Arts & Crafts", "Sports & Outdoors", "Auto parts", "Musical Instruments",
    "Antiques & Collectibles", "Garage Sale", "Miscellaneous",
]

# Condition dropdown — verbatim option labels.
CONDITIONS: list[str] = ["New", "Used - Like New", "Used - Good", "Used - Fair"]

# Color dropdown — a CONDITIONAL field (appears e.g. for apparel). Verbatim option labels.
COLORS: list[str] = [
    "Beige", "Black", "Blue", "Brown", "Clear", "Gold", "Gray", "Green", "Multi-Color",
    "Orange", "Pink", "Purple", "Red", "Silver", "White", "Yellow",
]

# Field descriptors reused below. kind: "text" | "enum" | "photos".
_SKU = {"name": "SKU", "kind": "text"}
_COLOR = {"name": "Color", "kind": "enum", "options": COLORS}
_MATERIAL = {"name": "Material", "kind": "text"}

# The categories that behave like apparel (FB reveals Color + Material + SKU on select).
_APPAREL = {"Women's clothing & shoes", "Men's clothing & shoes"}

# Which categories we've actually PROBED live (vs. defaulted). Honest provenance — SKU was universal
# across every probed category, so unprobed categories default to [SKU] until we probe them.
PROBED_CATEGORIES: set[str] = {
    "Women's clothing & shoes",  # apparel: Color + Material + SKU (probed)
    "Furniture", "Toys & Games", "Household",  # SKU only (probed)
}

# Base fields present BEFORE any category is chosen (from the live form).
BASE_FIELDS: list[dict] = [
    {"name": "Title", "kind": "text", "required": True},
    {"name": "Price", "kind": "text", "required": True},
    {"name": "Category", "kind": "enum", "options": CATEGORIES, "required": True},
    {"name": "Condition", "kind": "enum", "options": CONDITIONS},
    {"name": "Description", "kind": "text"},
    {"name": "Photos", "kind": "photos"},
]


def conditional_fields(category: str) -> list[dict]:
    """The extra fields FB reveals once `category` is selected. SKU is universal across every probed
    category; apparel (Women's/Men's clothing & shoes) additionally shows Color (enum) + Material.
    Unprobed categories default to [SKU] — refine by re-probing (they MAY add e.g. a brand field)."""
    if category in _APPAREL:
        return [_COLOR, _MATERIAL, _SKU]
    return [_SKU]


def is_valid_category(category: str) -> bool:
    return category in CATEGORIES


def is_valid_condition(condition: str) -> bool:
    return condition in CONDITIONS


def listing_schema() -> dict:
    """The full mirrored schema — what the UI needs to render dropdowns (not free-text) for category /
    condition / color and to reveal a category's conditional fields, matching the live FB form."""
    return {
        "listing_type": "item_for_sale",
        "categories": CATEGORIES,
        "conditions": CONDITIONS,
        "colors": COLORS,
        "base_fields": BASE_FIELDS,
        "conditional_fields_by_category": {c: conditional_fields(c) for c in CATEGORIES},
        "probed_categories": sorted(PROBED_CATEGORIES),
        "source": "probed live from facebook.com/marketplace/create/item via CDP-AX (2026-07-08)",
        "note": "Item-for-sale only; Vehicle/Home are separate listing types. SKU is universal; "
                "apparel adds Color + Material. Unprobed categories default to [SKU].",
    }
