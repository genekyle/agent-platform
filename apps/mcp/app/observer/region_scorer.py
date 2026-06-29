from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Schema dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TaskContext:
    goal: str = ""
    action_type_hint: str = "any"  # click | type | select | toggle | any


@dataclass
class RegionBbox:
    x: float
    y: float
    width: float
    height: float


@dataclass
class RelativeGeometry:
    top_pct: float
    left_pct: float
    area_pct: float
    viewport_overlap_pct: float
    center_distance_pct: float


@dataclass
class RegionCounts:
    visible_buttons: int = 0
    visible_inputs: int = 0
    visible_links: int = 0
    forms: int = 0


@dataclass
class RegionFlags:
    contains_form: bool = False
    contains_submit_button: bool = False
    contains_search_input: bool = False
    contains_password_field: bool = False
    contains_primary_cta_like_text: bool = False


@dataclass
class RegionStructuredSummary:
    region_id: str
    role: str
    label: str
    visible: bool
    summary_text: str
    counts: RegionCounts = field(default_factory=RegionCounts)
    flags: RegionFlags = field(default_factory=RegionFlags)
    top_text: list[str] = field(default_factory=list)
    button_texts: list[str] = field(default_factory=list)
    input_labels: list[str] = field(default_factory=list)


@dataclass
class RegionScorerInput:
    task_context: TaskContext
    region_structured_summary: RegionStructuredSummary
    relative_geometry: RelativeGeometry
    fullpage_image: Optional[str] = None
    region_crop_image: Optional[str] = None
    region_bbox: Optional[RegionBbox] = None
    page_type_hint: Optional[str] = None


@dataclass
class RegionScorerOutput:
    region_id: str
    region_type: str          # navigation | primary_content | form | media | sidebar | footer | modal | unknown
    relevance_score: float
    routing_decision: str     # inspect_now | inspect_next | background_context_only | ignore
    confidence: float
    reason_codes: list[str]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BACKGROUND_ROLES = {"navigation", "banner", "contentinfo", "complementary", "nav", "header", "footer", "aside"}
_PRIMARY_ROLES = {"main", "article", "region"}
_FORM_ROLES = {"form", "search"}
_MODAL_ROLES = {"dialog", "alertdialog"}

_ROLE_TO_REGION_TYPE: dict[str, str] = {
    "navigation": "navigation", "nav": "navigation", "banner": "navigation",
    "main": "primary_content", "article": "primary_content", "region": "primary_content",
    "form": "form", "search": "form",
    "dialog": "modal", "alertdialog": "modal",
    "contentinfo": "footer", "footer": "footer",
    "complementary": "sidebar", "aside": "sidebar",
}

_CTA_TEXTS = {"submit", "sign in", "log in", "login", "register", "continue", "get started", "buy", "checkout", "add to cart"}

_VIEWPORT_W = 1920.0
_VIEWPORT_H = 1080.0


# ---------------------------------------------------------------------------
# Input builder
# ---------------------------------------------------------------------------

def build_scorer_input(
    region: dict[str, Any],
    acquisition: dict[str, Any],
    task_context: Optional[dict[str, Any]] = None,
    page_type_hint: Optional[str] = None,
) -> RegionScorerInput:
    tc_raw = task_context or {}
    task_ctx = TaskContext(
        goal=tc_raw.get("goal", ""),
        action_type_hint=tc_raw.get("action_type_hint", "any"),
    )

    region_id = region.get("region_id") or region.get("uid") or ""
    role = str(region.get("role", "")).strip()
    label = str(region.get("label", "")).strip()
    visible = bool(region.get("visible", False))
    summary_text = str(region.get("summary_text") or region.get("text", "")).strip()[:180]

    # Collect actionable elements belonging to this region
    region_elements = _collect_region_elements(region, acquisition)

    counts = _compute_counts(region_elements)
    flags = _compute_flags(region_elements)
    top_text = _collect_top_text(region_elements)
    button_texts = _collect_button_texts(region_elements)
    input_labels = _collect_input_labels(region_elements)

    structured = RegionStructuredSummary(
        region_id=region_id,
        role=role,
        label=label,
        visible=visible,
        summary_text=summary_text,
        counts=counts,
        flags=flags,
        top_text=top_text,
        button_texts=button_texts,
        input_labels=input_labels,
    )

    # Best-effort bbox from contained elements
    region_bbox = _aggregate_bbox(region_elements)

    # Screenshot dimensions for geometry (fall back to default viewport)
    screenshots = acquisition.get("screenshots", [])
    vw = _VIEWPORT_W
    vh = _VIEWPORT_H
    if screenshots:
        first = screenshots[0]
        vw = float(first.get("width") or _VIEWPORT_W)
        vh = float(first.get("height") or _VIEWPORT_H)
        if vw <= 0:
            vw = _VIEWPORT_W
        if vh <= 0:
            vh = _VIEWPORT_H

    relative_geometry = _compute_relative_geometry(region_bbox, vw, vh)

    fullpage_image: Optional[str] = None
    if screenshots:
        fullpage_image = screenshots[0].get("path")

    return RegionScorerInput(
        task_context=task_ctx,
        region_structured_summary=structured,
        relative_geometry=relative_geometry,
        fullpage_image=fullpage_image,
        region_crop_image=None,  # deferred to v2
        region_bbox=region_bbox,
        page_type_hint=page_type_hint,
    )


def _collect_region_elements(region: dict[str, Any], acquisition: dict[str, Any]) -> list[dict[str, Any]]:
    """Return actionable elements that belong to this region (best-effort match)."""
    region_id = region.get("region_id") or region.get("uid") or ""
    role = str(region.get("role", "")).strip()

    matched = []
    for elem in acquisition.get("actionable_elements", []):
        if not isinstance(elem, dict):
            continue
        elem_region = elem.get("region") or {}
        elem_region_uid = elem_region.get("uid", "")
        elem_region_role = str(elem_region.get("role", "") or elem_region.get("tag", "")).strip()

        if (region_id and elem_region_uid == region_id) or (role and elem_region_role == role):
            matched.append(elem)
    return matched


def _aggregate_bbox(elements: list[dict[str, Any]]) -> Optional[RegionBbox]:
    """Compute bounding box that encloses all element rects."""
    rects = [e["rect"] for e in elements if isinstance(e.get("rect"), dict)]
    if not rects:
        return None
    min_x = min(r["x"] for r in rects)
    min_y = min(r["y"] for r in rects)
    max_x = max(r["x"] + r.get("width", 0) for r in rects)
    max_y = max(r["y"] + r.get("height", 0) for r in rects)
    return RegionBbox(x=min_x, y=min_y, width=max_x - min_x, height=max_y - min_y)


def _compute_relative_geometry(bbox: Optional[RegionBbox], vw: float, vh: float) -> RelativeGeometry:
    if bbox is None or bbox.width <= 0 or bbox.height <= 0:
        return RelativeGeometry(
            top_pct=0.0, left_pct=0.0, area_pct=0.0,
            viewport_overlap_pct=0.0, center_distance_pct=1.0,
        )

    top_pct = round(bbox.y / vh, 4)
    left_pct = round(bbox.x / vw, 4)
    region_area = bbox.width * bbox.height
    viewport_area = vw * vh
    area_pct = round(region_area / viewport_area, 4)

    # Overlap with viewport [0, vw] x [0, vh]
    ox1 = max(0.0, bbox.x)
    oy1 = max(0.0, bbox.y)
    ox2 = min(vw, bbox.x + bbox.width)
    oy2 = min(vh, bbox.y + bbox.height)
    overlap_area = max(0.0, ox2 - ox1) * max(0.0, oy2 - oy1)
    viewport_overlap_pct = round(overlap_area / region_area if region_area > 0 else 0.0, 4)

    # Normalized distance from viewport center
    center_x = bbox.x + bbox.width / 2
    center_y = bbox.y + bbox.height / 2
    vc_x = vw / 2
    vc_y = vh / 2
    max_dist = math.hypot(vc_x, vc_y)
    dist = math.hypot(center_x - vc_x, center_y - vc_y)
    center_distance_pct = round(dist / max_dist if max_dist > 0 else 0.0, 4)

    return RelativeGeometry(
        top_pct=top_pct,
        left_pct=left_pct,
        area_pct=area_pct,
        viewport_overlap_pct=viewport_overlap_pct,
        center_distance_pct=center_distance_pct,
    )


def _compute_counts(elements: list[dict[str, Any]]) -> RegionCounts:
    buttons = inputs = links = forms = 0
    for e in elements:
        if not bool(e.get("visible", False)):
            continue
        tag = str(e.get("tag", "")).lower()
        role = str(e.get("role", "")).lower()
        if tag in {"button"} or role == "button":
            buttons += 1
        elif tag in {"input", "textarea", "select"} or role in {"textbox", "searchbox", "combobox", "listbox"}:
            inputs += 1
        elif tag == "a" or role == "link":
            links += 1
        if tag == "form" or role == "form":
            forms += 1
    return RegionCounts(visible_buttons=buttons, visible_inputs=inputs, visible_links=links, forms=forms)


def _compute_flags(elements: list[dict[str, Any]]) -> RegionFlags:
    has_form = any(str(e.get("tag", "")).lower() in {"form"} or str(e.get("role", "")).lower() == "form" for e in elements)
    has_submit = any(
        (str(e.get("tag", "")).lower() == "button" or str(e.get("type", "")).lower() == "submit")
        and str(e.get("type", "")).lower() in {"submit", ""} and bool(e.get("visible"))
        for e in elements
    )
    has_search = any(
        str(e.get("type", "")).lower() == "search"
        or str(e.get("role", "")).lower() in {"searchbox"}
        or str(e.get("label", "")).lower() in {"search"}
        for e in elements
    )
    has_password = any(str(e.get("type", "")).lower() == "password" for e in elements)
    has_cta = any(
        any(cta in str(e.get("text", "")).lower() or cta in str(e.get("label", "")).lower() for cta in _CTA_TEXTS)
        for e in elements
    )
    return RegionFlags(
        contains_form=has_form or any(str(e.get("tag", "")).lower() in {"input", "textarea", "select"} for e in elements),
        contains_submit_button=has_submit,
        contains_search_input=has_search,
        contains_password_field=has_password,
        contains_primary_cta_like_text=has_cta,
    )


def _collect_top_text(elements: list[dict[str, Any]]) -> list[str]:
    texts = []
    for e in elements:
        t = str(e.get("text", "") or e.get("label", "")).strip()
        if t and t not in texts:
            texts.append(t)
        if len(texts) >= 5:
            break
    return texts


def _collect_button_texts(elements: list[dict[str, Any]]) -> list[str]:
    texts = []
    for e in elements:
        if str(e.get("tag", "")).lower() != "button" and str(e.get("role", "")).lower() != "button":
            continue
        t = str(e.get("text", "") or e.get("label", "")).strip()
        if t and t not in texts:
            texts.append(t)
        if len(texts) >= 5:
            break
    return texts


def _collect_input_labels(elements: list[dict[str, Any]]) -> list[str]:
    labels = []
    for e in elements:
        tag = str(e.get("tag", "")).lower()
        role = str(e.get("role", "")).lower()
        if tag not in {"input", "textarea", "select"} and role not in {"textbox", "searchbox", "combobox"}:
            continue
        lbl = str(e.get("label", "") or e.get("placeholder", "")).strip()
        if lbl and lbl not in labels:
            labels.append(lbl)
        if len(labels) >= 5:
            break
    return labels


# ---------------------------------------------------------------------------
# Scoring dispatch
# ---------------------------------------------------------------------------

def score_region(scorer_input: RegionScorerInput, *, adapter_id: str = "heuristic") -> RegionScorerOutput:
    if adapter_id == "heuristic":
        return _heuristic_score(scorer_input)
    raise ValueError(f"Unknown region scorer adapter: {adapter_id!r}")


def _heuristic_score(inp: RegionScorerInput) -> RegionScorerOutput:
    summary = inp.region_structured_summary
    role = summary.role.lower()
    flags = summary.flags
    counts = summary.counts
    geometry = inp.relative_geometry
    task_hint = inp.task_context.action_type_hint.lower()

    # --- region_type ---
    region_type = _ROLE_TO_REGION_TYPE.get(role, "unknown")
    if region_type == "unknown" and (flags.contains_form or counts.visible_inputs > 0):
        region_type = "form"

    # --- reason_codes ---
    reason_codes: list[str] = []

    if not summary.visible:
        reason_codes.append("low_visibility")
    if role in _BACKGROUND_ROLES:
        reason_codes.append("navigation_region")
    if flags.contains_form or counts.visible_inputs > 0:
        reason_codes.append("contains_form_inputs")
    if geometry.viewport_overlap_pct < 0.3:
        reason_codes.append("low_viewport_overlap")
    if geometry.center_distance_pct < 0.25:
        reason_codes.append("near_viewport_center")
    if not summary.summary_text and counts.visible_buttons == 0 and counts.visible_inputs == 0:
        reason_codes.append("no_content")

    # Task relevance
    action_match = False
    if task_hint in {"type", "any"} and counts.visible_inputs > 0:
        action_match = True
    if task_hint in {"click", "any"} and counts.visible_buttons > 0:
        action_match = True
    if task_hint == "select" and counts.visible_inputs > 0:
        action_match = True

    if action_match:
        reason_codes.append("matches_action_type")

    # Salience-based relevance score (mirrors existing region_proposal salience logic, extended)
    salience = 0.2
    if summary.visible:
        salience += 0.4
    if role in {"main", "navigation", "feed", "search", "form", "dialog"}:
        salience += 0.2
    if summary.label or summary.summary_text:
        salience += 0.1
    if action_match:
        salience += 0.1
    if flags.contains_form or counts.visible_inputs > 0:
        salience += 0.05
    if geometry.center_distance_pct < 0.25:
        salience += 0.05

    relevance_score = round(min(salience, 1.0), 3)

    # task_relevant / task_irrelevant
    if relevance_score >= 0.5 and (action_match or role in _PRIMARY_ROLES | _FORM_ROLES | _MODAL_ROLES):
        reason_codes.append("task_relevant_content")
    elif relevance_score < 0.3 and not action_match:
        reason_codes.append("task_irrelevant")

    if role in {"main", "article", "region"} or flags.contains_form:
        reason_codes.append("high_salience_role")

    # --- routing_decision ---
    if role in _BACKGROUND_ROLES:
        routing_decision = "background_context_only"
    elif not summary.visible or "low_visibility" in reason_codes:
        routing_decision = "ignore"
    elif relevance_score >= 0.6:
        routing_decision = "inspect_now"
    elif relevance_score >= 0.3:
        routing_decision = "inspect_next"
    else:
        routing_decision = "ignore"

    # Modals always inspect_now
    if role in _MODAL_ROLES:
        routing_decision = "inspect_now"

    return RegionScorerOutput(
        region_id=summary.region_id,
        region_type=region_type,
        relevance_score=relevance_score,
        routing_decision=routing_decision,
        confidence=0.6,  # honest fixed value for heuristic adapter
        reason_codes=list(dict.fromkeys(reason_codes)),  # deduplicate, preserve order
    )
