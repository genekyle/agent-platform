import copy
import unittest

from app.artifacts import build_observation_artifact
from app.fixtures import load_golden_fixture, load_observer_fixture, load_source_fixture
from app.observer.acquisition import build_acquisition_input
from app.observer.adapters import DEFAULT_STAGE_REGISTRY
from app.observer.pipeline import run_pipeline


class ObserverArtifactShapeTests(unittest.TestCase):
    def test_observer_fixture_contains_required_sections(self):
        artifact = load_observer_fixture("documentation_page")
        required = {
            "metadata",
            "acquisition",
            "pipeline",
            "scene_interpretation",
            "region_proposals",
            "region_scores",
            "visual_element_proposals",
            "grounded_candidates",
            "ranked_candidates",
        }
        self.assertEqual(set(artifact.keys()), required)
        self.assertEqual(artifact["metadata"]["observer_version"], "vision-first-observer-v1")
        self.assertEqual(
            artifact["pipeline"]["stage_order"],
            [
                "screenshot_capture",
                "scene_interpreter",
                "region_proposal",
                "region_scorer",
                "visual_element_proposal",
                "grounding",
                "fusion",
            ],
        )
        self.assertNotIn("trace", artifact)
        self.assertNotIn("derived_state", artifact)

    def test_pipeline_handles_empty_candidates_and_missing_screenshot(self):
        acquisition = {
            "page_identity": {"title": "Blocked", "url": "https://example.com/blocked"},
            "frame_state": {"frame_count": 0, "dialog_present": True, "active_element": None},
            "accessibility_snapshot": [],
            "actionable_elements": [],
            "regions": [],
            "dom_context": {"headings": ["Access Denied"], "dialogs": [], "landmarks": []},
            "js_state": {"inputs_count": 0},
            "console": [{"level": "error", "text": "403 blocked"}],
            "network": [{"url": "https://example.com/blocked", "method": "GET", "status": 403}],
            "screenshots": [],
            "capture_status": {
                "screenshot": {"status": "unavailable", "details": "screenshot capture disabled"},
            },
        }

        pipeline_run = run_pipeline(acquisition)

        self.assertEqual(pipeline_run["stages"]["screenshot_capture"]["status"], "unavailable")
        self.assertEqual(pipeline_run["stages"]["grounding"]["output"], [])
        self.assertEqual(pipeline_run["stages"]["fusion"]["output"], [])

    def test_acquisition_includes_structured_training_metadata_when_provided(self):
        acquisition = build_acquisition_input(
            js_capture={
                "page_identity": {"title": "Example", "url": "https://example.com"},
                "viewport_state": {
                    "viewport_width": 1200,
                    "viewport_height": 800,
                    "device_scale_factor": 2,
                    "scroll_x": 10,
                    "scroll_y": 20,
                },
            },
            accessibility_snapshot=[],
            console_entries=[],
            network_entries=[],
            screenshot_refs=[{"path": "/tmp/shot.png", "image_path": "/tmp/shot.png", "width": 1200, "height": 800, "shot_type": "viewport"}],
            capture_status={},
            task_context={"goal": "Search Jobs", "action_type_hint": "click"},
            training_metadata={
                "browser_session_id": "training-session-1",
                "domain_id": "indeed_jobs",
                "goal_id": "search_jobs",
                "task_id": None,
                "action_type_hint": "click",
                "notes": "session note",
                "capture_profile": "viewport",
                "tab_id": "tab-1",
            },
        )

        self.assertEqual(acquisition["training_metadata"]["browser_session_id"], "training-session-1")
        self.assertEqual(acquisition["training_metadata"]["viewport_width"], 1200)


class GoldenObserverTests(unittest.TestCase):
    def test_documentation_page_matches_golden(self):
        source = load_source_fixture("documentation_page")
        acquisition = build_acquisition_input(
            js_capture=source["js_capture"],
            accessibility_snapshot=source["accessibility_snapshot"],
            console_entries=source["console_entries"],
            network_entries=source["network_entries"],
            screenshot_refs=source["screenshot_refs"],
            capture_status=source["capture_status"],
        )
        artifact = build_observation_artifact(
            source="fixture",
            scenario="documentation_page",
            acquisition=acquisition,
            pipeline_run=run_pipeline(acquisition),
        )
        artifact["metadata"]["timestamp"] = "__dynamic__"
        self.assertEqual(artifact, load_golden_fixture("documentation_page"))

    def test_social_feed_matches_golden(self):
        source = load_source_fixture("social_feed")
        acquisition = build_acquisition_input(
            js_capture=source["js_capture"],
            accessibility_snapshot=source["accessibility_snapshot"],
            console_entries=source["console_entries"],
            network_entries=source["network_entries"],
            screenshot_refs=source["screenshot_refs"],
            capture_status=source["capture_status"],
        )
        artifact = build_observation_artifact(
            source="fixture",
            scenario="social_feed",
            acquisition=acquisition,
            pipeline_run=run_pipeline(acquisition),
        )
        artifact["metadata"]["timestamp"] = "__dynamic__"
        self.assertEqual(artifact, load_golden_fixture("social_feed"))

    def test_config_selects_stage_adapter_without_rewiring_pipeline(self):
        source = load_source_fixture("documentation_page")
        acquisition = build_acquisition_input(
            js_capture=source["js_capture"],
            accessibility_snapshot=source["accessibility_snapshot"],
            console_entries=source["console_entries"],
            network_entries=source["network_entries"],
            screenshot_refs=source["screenshot_refs"],
            capture_status=source["capture_status"],
        )

        custom_registry = copy.deepcopy(DEFAULT_STAGE_REGISTRY)

        def custom_scene(acquisition_payload, _context):
            return {
                "adapter_id": "custom",
                "status": "success",
                "output": {
                    "page_type": "custom_docs",
                    "primary_goal": "custom_goal",
                    "headline": acquisition_payload["page_identity"]["title"],
                    "summary_text": "custom summary",
                    "visual_context": "screenshot_available",
                },
                "diagnostics": {"selected": True},
            }

        custom_registry["scene_interpreter"]["custom"] = copy.deepcopy(
            custom_registry["scene_interpreter"]["heuristic"]
        )
        custom_registry["scene_interpreter"]["custom"] = type(custom_registry["scene_interpreter"]["heuristic"])(
            name="scene_interpreter",
            adapter_id="custom",
            handler=custom_scene,
        )

        pipeline_run = run_pipeline(
            acquisition,
            config={"stages": {"scene_interpreter": {"adapter_id": "custom"}}},
            registry=custom_registry,
        )

        self.assertEqual(pipeline_run["stages"]["scene_interpreter"]["adapter_id"], "custom")
        self.assertEqual(pipeline_run["stages"]["scene_interpreter"]["output"]["page_type"], "custom_docs")
