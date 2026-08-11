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


class TabAddressingTest(unittest.IsolatedAsyncioTestCase):
    """An addressed capture must land on the addressed tab, or not happen at all.

    Live, 2026-07-22: `LiveActuator` addresses tabs by CDP `tab_id` only (an id survives a
    navigation; a url does not), but `list_pages` exposes a 1-based index and no target id, so the
    id comparison in `_select_tab` could never match. `_verify_target_tab`'s every branch was keyed
    on `expected_url`, which an id-only caller leaves as None — so the guard written to stop
    "poisoning the corpus with a mislabelled state" passed, and four captures of a stale
    post-apply tab were written carrying the state label of the page the drive was actually on.
    """

    def setUp(self):
        import app.main as m
        self.m = m

    def test_a_cdp_tab_id_resolves_to_its_url(self):
        targets = [{"id": "AAA", "url": "https://www.indeed.com/jobs?q=x"},
                   {"id": "BBB", "url": "https://smartapply.indeed.com/post-apply"}]

        class _Resp:
            def read(self_inner):
                return json.dumps(targets).encode()
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *a):
                return False

        with patch("urllib.request.urlopen", return_value=_Resp()):
            self.assertEqual(self.m._url_for_tab_id("http://localhost:9328", "AAA"),
                             "https://www.indeed.com/jobs?q=x")
            self.assertIsNone(self.m._url_for_tab_id("http://localhost:9328", "MISSING"))

    async def test_an_unpinnable_addressed_capture_refuses_instead_of_taking_the_front_tab(self):
        with self.assertRaises(RuntimeError) as ctx:
            await self.m._verify_target_tab(None, expected_url=None, tab_pinned=False,
                                            addressed=True)
        self.assertIn("frontmost", str(ctx.exception))

    async def test_a_caller_that_named_no_tab_still_gets_the_front_tab(self):
        """Not every capture addresses a tab; those callers asked for whatever is in front."""
        class _Session:
            async def call_tool(self_inner, name, args):
                return {"url": "https://example.test/x", "title": "t"}

        await self.m._verify_target_tab(_Session(), expected_url=None, tab_pinned=False,
                                        addressed=False)


class ListTabsTest(unittest.TestCase):
    """`/list_tabs` — the answer to "what is open right now", which nothing could give until now.

    /close_tab could close one and _discover_target could find one, but the controller had no way
    to SEE its window, and three faults on 2026-07-22 traced back to that.
    """

    def test_only_page_targets_are_reported(self):
        import asyncio

        import app.main_server as ms

        targets = [
            {"id": "A", "type": "page", "url": "https://www.indeed.com/jobs", "title": "Jobs"},
            {"id": "S", "type": "service_worker", "url": "https://x/sw.js", "title": ""},
            {"id": "B", "type": "page", "url": "https://smartapply.indeed.com/x", "title": "App"},
        ]

        class _Resp:
            def json(self_inner):
                return targets

        class _Client:
            async def __aenter__(self_inner):
                return self_inner
            async def __aexit__(self_inner, *a):
                return False
            async def get(self_inner, url):
                return _Resp()

        with patch("httpx.AsyncClient", lambda **k: _Client()):
            out = asyncio.run(ms.list_tabs(ms.ListTabsRequest(browser_url="http://b")))

        self.assertTrue(out["ok"])
        self.assertEqual([t["tab_id"] for t in out["tabs"]], ["A", "B"])
        self.assertEqual(out["count"], 2)

    def test_an_unreachable_browser_is_an_empty_list_not_a_crash(self):
        """A drive must degrade to "I cannot see the window", never die of not looking."""
        import asyncio

        import app.main_server as ms

        def _boom(**k):
            raise RuntimeError("browser gone")

        with patch("httpx.AsyncClient", _boom):
            out = asyncio.run(ms.list_tabs(ms.ListTabsRequest(browser_url="http://b")))
        self.assertFalse(out["ok"])
        self.assertEqual(out["tabs"], [])


def test_distance_target_and_the_already_rule():
    """Pin for the 2026-08-10 radius bug: the operator declared 50, the tab's URL still carried
    the previous search's radius=100, and an `already >= floor` early-exit accepted it. The
    target is the smallest offered option >= the floor, and "already" is EQUALITY with it —
    a leftover wider radius must operate the pill, not satisfy it."""
    from app.main_server import DISTANCE_OPTIONS, LINKEDIN_DISTANCE_OPTIONS, distance_target

    assert distance_target(50, DISTANCE_OPTIONS) == 50          # exactly-50 exists on Indeed
    assert distance_target(50, LINKEDIN_DISTANCE_OPTIONS) == 50
    assert distance_target(30, DISTANCE_OPTIONS) == 35          # smallest >= floor
    assert distance_target(999, DISTANCE_OPTIONS) == 100        # widest when none reaches
    # The live failure, as arithmetic: current 100, declared 50 — NOT "already".
    assert distance_target(50, DISTANCE_OPTIONS) != 100
