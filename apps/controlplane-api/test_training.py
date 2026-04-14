import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from training import build_grounding_dataset, merge_training_annotation, train_grounding_model


@dataclass
class FakeCapture:
    artifact_filename: str
    captured_at: datetime
    review_status: str
    positive_candidate_id: str | None
    rejected_candidate_ids: list[str]
    candidate_labels: dict
    approved_bbox: dict | None
    url: str
    title: str
    viewport_width: int
    viewport_height: int
    device_scale_factor: float
    scroll_x: float
    scroll_y: float
    tab_id: str
    browser_session_id: str
    domain_id: str
    goal_id: str
    task_id: str | None
    action_type_hint: str
    notes: str | None
    capture_profile: str
    screenshot_refs: list[dict]


def test_merge_training_annotation_normalizes_positive_and_rejects():
    merged = merge_training_annotation(
        {
            "domain_id": "indeed_jobs",
            "goal_id": "search_jobs",
            "action_type_hint": "click",
            "capture_profile": "viewport",
            "browser_session_id": "training-session-1",
        },
        {
            "candidate_labels": {
                "candidate:one": "approve",
                "candidate:two": "reject",
            },
            "approved_bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
        },
    )

    assert merged["positive_candidate_id"] == "candidate:one"
    assert merged["rejected_candidate_ids"] == ["candidate:two"]
    assert merged["review_status"] == "reviewed"
    assert merged["domain_id"] == "indeed_jobs"
    assert merged["goal_id"] == "search_jobs"


def test_build_dataset_and_train_model(tmp_path: Path):
    traces_dir = tmp_path / "observer-traces"
    screenshots_dir = tmp_path / "observer-screenshots"
    traces_dir.mkdir(parents=True)
    screenshots_dir.mkdir(parents=True)

    screenshot_path = screenshots_dir / "shot.png"
    screenshot_path.write_bytes(b"fake")

    artifact = {
        "metadata": {"timestamp": "2026-04-08T00:00:00+00:00", "source": "live_mcp", "scenario": "training_capture"},
        "acquisition": {
            "page_identity": {"url": "https://example.com", "title": "Example"},
            "screenshots": [{"path": str(screenshot_path), "image_path": str(screenshot_path), "width": 1200, "height": 800, "shot_type": "viewport"}],
            "actionable_elements": [
                {"uid": "button|idx:0", "rect": {"x": 10, "y": 20, "width": 100, "height": 30}},
                {"uid": "a|idx:1", "rect": {"x": 300, "y": 400, "width": 80, "height": 20}},
            ],
        },
        "ranked_candidates": [
            {
                "candidate_id": "candidate:button|idx:0",
                "element_id": "button|idx:0",
                "action_type": "click",
                "target": {"label": "Submit", "tag": "button", "role": "button"},
                "grounding": {"bbox": {"x": 10, "y": 20, "width": 100, "height": 30}},
                "score": 0.9,
                "confidence": 0.9,
            },
            {
                "candidate_id": "candidate:a|idx:1",
                "element_id": "a|idx:1",
                "action_type": "click",
                "target": {"label": "Cancel", "tag": "a", "role": "link"},
                "grounding": {"bbox": {"x": 300, "y": 400, "width": 80, "height": 20}},
                "score": 0.4,
                "confidence": 0.4,
            },
        ],
    }
    filename = "artifact.json"
    (traces_dir / filename).write_text(json.dumps(artifact), encoding="utf-8")

    capture = FakeCapture(
        artifact_filename=filename,
        captured_at=datetime(2026, 4, 8, tzinfo=timezone.utc),
        review_status="reviewed",
        positive_candidate_id="candidate:button|idx:0",
        rejected_candidate_ids=["candidate:a|idx:1"],
        candidate_labels={"candidate:button|idx:0": "approve", "candidate:a|idx:1": "reject"},
        approved_bbox={"x": 10, "y": 20, "width": 100, "height": 30},
        url="https://example.com",
        title="Example",
        viewport_width=1200,
        viewport_height=800,
        device_scale_factor=2.0,
        scroll_x=0.0,
        scroll_y=0.0,
        tab_id="tab-1",
        browser_session_id="training-session-1",
        domain_id="indeed_jobs",
        goal_id="search_jobs",
        task_id=None,
        action_type_hint="click",
        notes="capture notes",
        capture_profile="viewport",
        screenshot_refs=[{"path": str(screenshot_path), "image_path": str(screenshot_path), "width": 1200, "height": 800, "shot_type": "viewport"}],
    )

    manifest = build_grounding_dataset(tmp_path, captures=[capture])
    assert manifest["record_count"] == 1
    assert Path(manifest["path"]).exists()

    training_result = train_grounding_model(tmp_path, dataset_manifest=manifest)
    assert training_result["ok"] is True
    assert training_result["metrics"]["target_accuracy"] >= 0.0
    assert Path(training_result["model_dir"]).exists()
