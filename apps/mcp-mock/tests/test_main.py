import contextlib
import importlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class RunObserverSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_observer_prints_observer_artifact_and_writes_artifacts(self):
        js_capture = {
            "page_identity": {"title": "Docs", "url": "https://example.com/docs"},
            "frame_state": {"frame_count": 1, "dialog_present": False, "active_element": None},
            "actionable_elements": [
                {
                    "uid": "input|idx:0",
                    "tag": "input",
                    "type": "search",
                    "role": "",
                    "name": "q",
                    "label": "Search docs",
                    "text": "",
                    "placeholder": "Search docs",
                    "href": "",
                    "disabled": False,
                    "hidden": False,
                    "visible": True,
                    "user_facing": True,
                    "checked": None,
                    "expanded": None,
                    "selected": None,
                    "value": "",
                    "rect": {"x": 0, "y": 0, "width": 100, "height": 24},
                    "parent_tag": "form",
                    "parent_role": None,
                    "nearby_context": "Search docs",
                    "region": {
                        "uid": "nav|idx:0",
                        "tag": "nav",
                        "role": "navigation",
                        "label": "Docs nav",
                        "id": "",
                        "className": "",
                    },
                }
            ],
            "regions": [
                {
                    "uid": "nav|idx:0",
                    "tag": "nav",
                    "role": "navigation",
                    "label": "Docs nav",
                    "id": "",
                    "className": "",
                    "visible": True,
                    "text": "Docs",
                }
            ],
            "dom_context": {
                "headings": ["Docs"],
                "dialogs": [],
                "landmarks": [{"uid": "main|idx:0", "tag": "main", "role": "main", "label": ""}],
            },
            "js_state": {
                "ready_state": "complete",
                "location_href": "https://example.com/docs",
                "title": "Docs",
                "forms_count": 1,
                "inputs_count": 1,
                "links_count": 3,
                "buttons_count": 0,
                "selection_text": "",
                "body_text_preview": "Docs page",
            },
        }
        accessibility = {"nodes": [{"uid": "ax-1", "role": "main", "name": "Documentation", "ignored": False}]}
        console_entries = {"entries": [{"level": "warning", "text": "Deprecated API"}, {"level": "info", "text": "ignored"}]}
        network_entries = {"requests": [{"url": "https://example.com/bootstrap", "method": "GET", "status": 200}]}
        screenshot_payload = {
            "data_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z0i8AAAAASUVORK5CYII=",
            "mime_type": "image/png",
            "width": 1,
            "height": 1,
            "label": "page_screenshot",
        }

        fake_mcp = types.ModuleType("mcp")

        class FakeStdioServerParameters:
            def __init__(self, command, args):
                self.command = command
                self.args = args

        class FakeClientSession:
            def __init__(self, read, write):
                self.read = read
                self.write = write

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def initialize(self):
                return None

            async def call_tool(self, name, payload):
                mapping = {
                    "evaluate_script": js_capture,
                    "get_accessibility_tree": accessibility,
                    "list_console_messages": console_entries,
                    "list_network_requests": network_entries,
                    "take_screenshot": screenshot_payload,
                }
                if name not in mapping:
                    raise RuntimeError(f"unsupported tool: {name}")
                return types.SimpleNamespace(content=[types.SimpleNamespace(text=json.dumps(mapping[name]))])

        fake_mcp.ClientSession = FakeClientSession
        fake_mcp.StdioServerParameters = FakeStdioServerParameters

        fake_stdio_module = types.ModuleType("mcp.client.stdio")

        class FakeStdioContext:
            async def __aenter__(self):
                return ("read", "write")

            async def __aexit__(self, exc_type, exc, tb):
                return False

        def fake_stdio_client(_params):
            return FakeStdioContext()

        fake_stdio_module.stdio_client = fake_stdio_client

        fake_client_module = types.ModuleType("mcp.client")
        fake_client_module.stdio = fake_stdio_module

        with patch.dict(
            sys.modules,
            {
                "mcp": fake_mcp,
                "mcp.client": fake_client_module,
                "mcp.client.stdio": fake_stdio_module,
            },
        ):
            main_module = importlib.import_module("app.main")
            importlib.reload(main_module)
            artifacts_module = importlib.import_module("app.artifacts")
            importlib.reload(artifacts_module)

            buffer = io.StringIO()
            with tempfile.TemporaryDirectory() as artifact_tmpdir, tempfile.TemporaryDirectory() as screenshot_tmpdir:
                with patch.object(artifacts_module, "ARTIFACTS_DIR", Path(artifact_tmpdir)):
                    with patch.object(artifacts_module, "SCREENSHOTS_DIR", Path(screenshot_tmpdir)):
                        with patch.object(main_module, "write_observation_artifact", side_effect=artifacts_module.write_observation_artifact):
                            with patch.object(main_module, "write_screenshot_asset", side_effect=artifacts_module.write_screenshot_asset):
                                with contextlib.redirect_stdout(buffer):
                                    await main_module.run_observer()

                        artifacts = list(Path(artifact_tmpdir).glob("*.json"))
                        screenshots = list(Path(screenshot_tmpdir).glob("*.png"))
                        self.assertEqual(len(artifacts), 1)
                        self.assertEqual(len(screenshots), 1)
                        saved_payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
                        self.assertEqual(saved_payload["metadata"]["source"], "live_mcp")
                        self.assertEqual(saved_payload["metadata"]["observer_version"], "vision-first-observer-v1")
                        self.assertIn("pipeline", saved_payload)
                        self.assertIn("ranked_candidates", saved_payload)
                        self.assertEqual(saved_payload["acquisition"]["screenshots"][0]["shot_type"], "viewport")
                        self.assertNotIn("trace", saved_payload)

        payload = json.loads(buffer.getvalue())
        self.assertIn("acquisition", payload)
        self.assertIn("pipeline", payload)
        self.assertIn("grounded_candidates", payload)
        self.assertIn("ranked_candidates", payload)
        self.assertNotIn("diagnostics", payload)

    async def test_partial_failures_record_capture_status_and_empty_candidates(self):
        fake_mcp = types.ModuleType("mcp")

        class FakeStdioServerParameters:
            def __init__(self, command, args):
                self.command = command
                self.args = args

        class FakeClientSession:
            def __init__(self, read, write):
                self.read = read
                self.write = write

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def initialize(self):
                return None

            async def call_tool(self, name, payload):
                if name == "evaluate_script":
                    return types.SimpleNamespace(
                        content=[
                            types.SimpleNamespace(
                                text=json.dumps(
                                    {
                                        "page_identity": {"title": "X", "url": "https://example.com"},
                                        "frame_state": {},
                                        "actionable_elements": [],
                                        "regions": [],
                                        "dom_context": {},
                                        "js_state": {},
                                    }
                                )
                            )
                        ]
                    )
                raise RuntimeError(f"{name} unavailable")

        fake_mcp.ClientSession = FakeClientSession
        fake_mcp.StdioServerParameters = FakeStdioServerParameters

        fake_stdio_module = types.ModuleType("mcp.client.stdio")

        class FakeStdioContext:
            async def __aenter__(self):
                return ("read", "write")

            async def __aexit__(self, exc_type, exc, tb):
                return False

        def fake_stdio_client(_params):
            return FakeStdioContext()

        fake_stdio_module.stdio_client = fake_stdio_client
        fake_client_module = types.ModuleType("mcp.client")
        fake_client_module.stdio = fake_stdio_module

        with patch.dict(
            sys.modules,
            {
                "mcp": fake_mcp,
                "mcp.client": fake_client_module,
                "mcp.client.stdio": fake_stdio_module,
            },
        ):
            main_module = importlib.import_module("app.main")
            importlib.reload(main_module)
            artifact = await main_module.observe_live_capture(
                training_metadata={
                    "browser_session_id": "training-session-11",
                    "domain_id": "indeed_jobs",
                    "goal_id": "search_jobs",
                    "task_id": None,
                    "action_type_hint": "click",
                    "notes": "note",
                    "capture_profile": "viewport",
                    "tab_id": "tab-1",
                }
            )

        self.assertEqual(artifact["acquisition"]["capture_status"]["js_state"]["status"], "success")
        self.assertEqual(artifact["acquisition"]["capture_status"]["accessibility_snapshot"]["status"], "unavailable")
        self.assertEqual(artifact["acquisition"]["capture_status"]["console"]["status"], "unavailable")
        self.assertEqual(artifact["acquisition"]["capture_status"]["network"]["status"], "unavailable")
        self.assertEqual(artifact["acquisition"]["training_metadata"]["browser_session_id"], "training-session-11")
        self.assertEqual(artifact["acquisition"]["capture_status"]["screenshot"]["status"], "unavailable")
        self.assertEqual(artifact["grounded_candidates"], [])
        self.assertEqual(artifact["ranked_candidates"], [])
