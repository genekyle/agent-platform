"""Tests for the FB Marketplace listing schema mirrored from the live create-listing UI."""

from __future__ import annotations

import facebook_listing_schema as fls


def test_categories_and_conditions_are_the_probed_values():
    assert "Furniture" in fls.CATEGORIES
    assert "Women's clothing & shoes" in fls.CATEGORIES
    assert len(fls.CATEGORIES) == 25          # the 25 item-for-sale categories probed
    assert fls.CONDITIONS == ["New", "Used - Like New", "Used - Good", "Used - Fair"]


def test_conditional_fields_apparel_vs_default():
    # apparel reveals Color (enum) + Material + SKU
    ap = {f["name"] for f in fls.conditional_fields("Women's clothing & shoes")}
    assert ap == {"Color", "Material", "SKU"}
    color = next(f for f in fls.conditional_fields("Women's clothing & shoes") if f["name"] == "Color")
    assert color["kind"] == "enum" and "Black" in color["options"]
    # non-apparel (probed) reveals SKU only
    assert [f["name"] for f in fls.conditional_fields("Furniture")] == ["SKU"]
    assert [f["name"] for f in fls.conditional_fields("Toys & Games")] == ["SKU"]


def test_validators():
    assert fls.is_valid_category("Furniture") is True
    assert fls.is_valid_category("furnature") is False       # the typo a dropdown prevents
    assert fls.is_valid_condition("Used - Good") is True
    assert fls.is_valid_condition("Used") is False


def test_listing_schema_shape():
    s = fls.listing_schema()
    assert s["listing_type"] == "item_for_sale"
    assert s["categories"] == fls.CATEGORIES
    assert set(s["conditional_fields_by_category"]) == set(fls.CATEGORIES)
    # Category is a required enum in the base fields
    cat = next(f for f in s["base_fields"] if f["name"] == "Category")
    assert cat["kind"] == "enum" and cat["required"] is True
