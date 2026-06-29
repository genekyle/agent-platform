import json
import tempfile
import unittest
from pathlib import Path

from app.artifacts import (
    build_observation_artifact,
    write_observation_artifact,
    write_screenshot_asset,
)
from app.fixtures import load_source_fixture
from app.observer.acquisition import build_acquisition_input
from app.observer.pipeline import run_pipeline


class ArtifactWriterTests(unittest.TestCase):
    def test_writes_observer_artifact_with_metadata(self):
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

        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_observation_artifact(artifact, output_dir=Path(tmpdir))
            self.assertTrue(path.exists())
            self.assertIn("__fixture__documentation_page.json", path.name)

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["metadata"]["source"], "fixture")
            self.assertEqual(payload["metadata"]["observer_version"], "vision-first-observer-v1")
            self.assertIn("ranked_candidates", payload)
            self.assertNotIn("trace", payload)

    def test_writes_screenshot_asset_and_returns_reference(self):
        screenshot_payload = {
            "data_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z0i8AAAAASUVORK5CYII=",
            "mime_type": "image/png",
            "label": "page_screenshot",
            "width": 1,
            "height": 1,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            ref = write_screenshot_asset(
                screenshot_payload,
                scenario="documentation_page",
                output_dir=Path(tmpdir),
            )

            self.assertIsNotNone(ref)
            self.assertTrue(Path(ref["path"]).exists())
            self.assertEqual(ref["mime_type"], "image/png")
