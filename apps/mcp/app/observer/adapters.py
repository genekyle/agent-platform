from __future__ import annotations

from typing import Any

from app.observer.region_scorer import build_scorer_input, score_region
from app.observer.schemas import StageDefinition
from app.observer.training_logger import log_training_record, write_region_scores


def _stage_result(
    *,
    adapter_id: str,
    status: str,
    output: Any,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "adapter_id": adapter_id,
        "status": status,
        "output": output,
        "diagnostics": diagnostics,
    }


def heuristic_screenshot_capture(acquisition: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    screenshots = acquisition.get("screenshots", [])
    capture_status = acquisition.get("capture_status", {}).get("screenshot", {})
    status = "success" if screenshots else capture_status.get("status", "unavailable")
    return _stage_result(
        adapter_id="heuristic",
        status=status,
        output={
            "screenshots": screenshots,
            "primary_screenshot": screenshots[0] if screenshots else None,
        },
        diagnostics={
            "screenshot_count": len(screenshots),
            "capture_status": capture_status,
        },
    )


def heuristic_scene_interpreter(acquisition: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    page_identity = acquisition.get("page_identity", {})
    dom_context = acquisition.get("dom_context", {})
    js_state = acquisition.get("js_state", {})
    accessibility = acquisition.get("accessibility_snapshot", [])
    screenshot_output = context["stage_outputs"]["screenshot_capture"]["output"]
    title = str(page_identity.get("title", ""))
    url = str(page_identity.get("url", ""))
    headings = [heading for heading in dom_context.get("headings", []) if isinstance(heading, str)]
    roles = [str(node.get("role", "")) for node in accessibility if isinstance(node, dict)]

    page_type = "generic_webpage"
    if "feed" in roles or "facebook" in title.lower():
        page_type = "social_feed"
    elif "docs" in title.lower() or "documentation" in title.lower() or "developers.openai.com" in url:
        page_type = "documentation"

    primary_goal = "inspect_content"
    if acquisition.get("frame_state", {}).get("dialog_present"):
        primary_goal = "resolve_dialog"
    elif js_state.get("inputs_count", 0):
        primary_goal = "locate_and_interact"

    summary = {
        "page_type": page_type,
        "primary_goal": primary_goal,
        "headline": headings[0] if headings else title,
        "summary_text": " | ".join(headings[:3]) if headings else title,
        "visual_context": "screenshot_available" if screenshot_output.get("primary_screenshot") else "no_screenshot",
    }
    return _stage_result(
        adapter_id="heuristic",
        status="success",
        output=summary,
        diagnostics={
            "heading_count": len(headings),
            "accessibility_role_count": len([role for role in roles if role]),
        },
    )


def heuristic_region_proposer(acquisition: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    proposals: list[dict[str, Any]] = []
    for index, region in enumerate(acquisition.get("regions", [])):
        if not isinstance(region, dict):
            continue
        text = str(region.get("text", "")).strip()
        label = str(region.get("label", "")).strip()
        visible = bool(region.get("visible", False))
        role = str(region.get("role", "")).strip() or str(region.get("tag", "")).strip()
        salience = 0.2
        if visible:
            salience += 0.4
        if role in {"main", "navigation", "feed", "search"}:
            salience += 0.2
        if label or text:
            salience += 0.1
        proposals.append(
            {
                "region_id": region.get("uid") or f"region:{index}",
                "role": role,
                "label": label,
                "visible": visible,
                "summary_text": text[:180],
                "bbox": region.get("rect") if isinstance(region.get("rect"), dict) else None,
                "source": "dom_region_heuristic",
                "salience": round(min(salience, 1.0), 2),
            }
        )
    return _stage_result(
        adapter_id="heuristic",
        status="success",
        output=proposals,
        diagnostics={"proposal_count": len(proposals)},
    )


def heuristic_visual_element_proposer(acquisition: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    proposals: list[dict[str, Any]] = []
    for index, element in enumerate(acquisition.get("actionable_elements", [])):
        if not isinstance(element, dict):
            continue
        rect = element.get("rect")
        proposal = {
            "element_id": element.get("uid") or f"element:{index}",
            "tag": element.get("tag", ""),
            "role": element.get("role", ""),
            "type": element.get("type", ""),
            "label": element.get("label", ""),
            "text": element.get("text", ""),
            "placeholder": element.get("placeholder", ""),
            "visible": bool(element.get("visible", False)),
            "user_facing": bool(element.get("user_facing", False)),
            "disabled": bool(element.get("disabled", False)),
            "region_hint": (element.get("region") or {}).get("tag") or (element.get("region") or {}).get("role"),
            "region_id": (element.get("region") or {}).get("uid"),
            "bbox": rect if isinstance(rect, dict) else None,
            "source": "dom_actionable_heuristic",
        }
        proposals.append(proposal)
    return _stage_result(
        adapter_id="heuristic",
        status="success",
        output=proposals,
        diagnostics={"proposal_count": len(proposals)},
    )


def _infer_action_type(proposal: dict[str, Any]) -> str:
    tag = str(proposal.get("tag", "")).lower()
    role = str(proposal.get("role", "")).lower()
    input_type = str(proposal.get("type", "")).lower()
    if tag in {"input", "textarea"} or role in {"textbox", "searchbox"}:
        return "type"
    if tag == "select" or role in {"option", "listbox"}:
        return "select"
    if input_type in {"checkbox", "radio"}:
        return "toggle"
    return "click"


def heuristic_grounding(acquisition: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    region_proposals = context["stage_outputs"]["region_proposal"]["output"]
    visual_proposals = context["stage_outputs"]["visual_element_proposal"]["output"]
    region_index = {region["region_id"]: region for region in region_proposals}
    grounded: list[dict[str, Any]] = []

    for index, proposal in enumerate(visual_proposals):
        region_match = None
        source_region = acquisition.get("actionable_elements", [])[index].get("region") if index < len(acquisition.get("actionable_elements", [])) else None
        if isinstance(source_region, dict):
            source_region_id = source_region.get("uid")
            if source_region_id and source_region_id in region_index:
                region_match = region_index[source_region_id]
            elif source_region.get("role") or source_region.get("tag"):
                for region in region_proposals:
                    if region["role"] in {source_region.get("role"), source_region.get("tag")}:
                        region_match = region
                        break

        confidence = 0.25
        if proposal.get("visible"):
            confidence += 0.25
        if proposal.get("user_facing"):
            confidence += 0.2
        if proposal.get("label") or proposal.get("text") or proposal.get("placeholder"):
            confidence += 0.15
        if region_match is not None:
            confidence += 0.15

        grounded.append(
            {
                "candidate_id": f"candidate:{proposal['element_id']}",
                "element_id": proposal["element_id"],
                "region_id": region_match["region_id"] if region_match else None,
                "action_type": _infer_action_type(proposal),
                "target": {
                    "label": proposal.get("label") or proposal.get("text") or proposal.get("placeholder") or proposal.get("tag"),
                    "tag": proposal.get("tag"),
                    "role": proposal.get("role"),
                },
                "grounding": {
                    "bbox": proposal.get("bbox"),
                    "region_hint": region_match["role"] if region_match else proposal.get("region_hint"),
                    "screenshot_available": bool(acquisition.get("screenshots")),
                },
                "confidence": round(min(confidence, 1.0), 2),
                "evidence": {
                    "page_url": acquisition.get("page_identity", {}).get("url"),
                    "nearby_context": acquisition.get("actionable_elements", [])[index].get("nearby_context") if index < len(acquisition.get("actionable_elements", [])) else "",
                },
            }
        )
    return _stage_result(
        adapter_id="heuristic",
        status="success",
        output=grounded,
        diagnostics={"candidate_count": len(grounded)},
    )


def heuristic_fusion(acquisition: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    grounded = context["stage_outputs"]["grounding"]["output"]
    ranked: list[dict[str, Any]] = []
    for candidate in grounded:
        score = float(candidate.get("confidence", 0.0))
        action_type = candidate.get("action_type")
        if action_type == "type":
            score += 0.08
        if candidate.get("region_id"):
            score += 0.05
        if candidate.get("grounding", {}).get("bbox"):
            score += 0.05
        if acquisition.get("frame_state", {}).get("active_element") and candidate["element_id"].startswith("input"):
            score += 0.03
        ranked.append(
            {
                **candidate,
                "score": round(min(score, 1.0), 2),
            }
        )

    ranked.sort(key=lambda item: item["score"], reverse=True)
    for rank, candidate in enumerate(ranked, start=1):
        candidate["rank"] = rank
    return _stage_result(
        adapter_id="heuristic",
        status="success",
        output=ranked,
        diagnostics={"ranked_candidate_count": len(ranked)},
    )


def heuristic_region_scorer(acquisition: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    region_proposals = context["stage_outputs"]["region_proposal"]["output"]
    task_context = context.get("config", {}).get("task_context")
    scene = context["stage_outputs"]["scene_interpreter"]["output"]
    page_type_hint = scene.get("page_type")

    scored_outputs: list[dict[str, Any]] = []
    scorer_outputs = []

    # Derive artifact_stem for training logger (best-effort; pipeline has no filename context here)
    artifact_stem: str | None = context.get("config", {}).get("_artifact_stem")

    for region in region_proposals:
        scorer_input = build_scorer_input(
            region=region,
            acquisition=acquisition,
            task_context=task_context,
            page_type_hint=page_type_hint,
        )
        scorer_output = score_region(scorer_input, adapter_id="heuristic")
        log_training_record(scorer_input, scorer_output)
        scorer_outputs.append(scorer_output)
        scored_outputs.append({
            **region,
            "region_type": scorer_output.region_type,
            "relevance_score": scorer_output.relevance_score,
            "routing_decision": scorer_output.routing_decision,
            "confidence": scorer_output.confidence,
            "reason_codes": scorer_output.reason_codes,
        })

    if artifact_stem:
        write_region_scores(artifact_stem, scorer_outputs)

    return _stage_result(
        adapter_id="heuristic",
        status="success",
        output=scored_outputs,
        diagnostics={
            "scored_region_count": len(scored_outputs),
            "inspect_now_count": sum(1 for r in scored_outputs if r["routing_decision"] == "inspect_now"),
            "inspect_next_count": sum(1 for r in scored_outputs if r["routing_decision"] == "inspect_next"),
            "background_context_only_count": sum(1 for r in scored_outputs if r["routing_decision"] == "background_context_only"),
            "ignore_count": sum(1 for r in scored_outputs if r["routing_decision"] == "ignore"),
        },
    )


DEFAULT_STAGE_REGISTRY: dict[str, dict[str, StageDefinition]] = {
    "screenshot_capture": {
        "heuristic": StageDefinition(
            name="screenshot_capture",
            adapter_id="heuristic",
            handler=heuristic_screenshot_capture,
        )
    },
    "scene_interpreter": {
        "heuristic": StageDefinition(
            name="scene_interpreter",
            adapter_id="heuristic",
            handler=heuristic_scene_interpreter,
        )
    },
    "region_proposal": {
        "heuristic": StageDefinition(
            name="region_proposal",
            adapter_id="heuristic",
            handler=heuristic_region_proposer,
        )
    },
    "region_scorer": {
        "heuristic": StageDefinition(
            name="region_scorer",
            adapter_id="heuristic",
            handler=heuristic_region_scorer,
        )
    },
    "visual_element_proposal": {
        "heuristic": StageDefinition(
            name="visual_element_proposal",
            adapter_id="heuristic",
            handler=heuristic_visual_element_proposer,
        )
    },
    "grounding": {
        "heuristic": StageDefinition(
            name="grounding",
            adapter_id="heuristic",
            handler=heuristic_grounding,
        )
    },
    "fusion": {
        "heuristic": StageDefinition(
            name="fusion",
            adapter_id="heuristic",
            handler=heuristic_fusion,
        )
    },
}
