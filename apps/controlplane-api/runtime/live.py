"""Live adapters — the bridge from the pure `run_loop` orchestrator to the real browser.

`runtime/loop.py` is deliberately side-effect-free: it observes through a `Proposer`
port and acts through an `Actor` port, both injected. In tests those are fakes. This
module supplies the LIVE implementations that talk to the mcp capture/executor server
over HTTP:

  * `LiveProposer` — each call triggers a fresh live capture (mcp `/capture`), then
    rebuilds a runtime `Observation` from the trace + AX sidecar it wrote. This is how
    the loop re-observes the page before every step and again after each executed action
    (the AFTER snapshot the verifier needs).
  * `LiveActor`  — performs the loop's decided action against the live tab by proxying to
    mcp `/execute` (the CDP driver). Defaults to the humanized driver so real drives carry
    a human-shaped motion/typing signature; pass `record_only=True` for a dry run.

Both are SYNCHRONOUS (blocking `httpx.Client`) on purpose: `run_loop` is sync, and the
`/api/runtime/run_live` endpoint is a sync FastAPI handler (runs in the threadpool), so a
blocking client here is correct and keeps the loop logic simple. Neither adapter raises
into the loop — a capture/executor failure degrades to an empty observation or an
un-executed action, which the loop turns into a clean human handoff rather than a crash.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import httpx

from select_stage import verifier

from .loop import ActResult, Observation

logger = logging.getLogger("controlplane.runtime.live")


# --- Observation from a written trace ---------------------------------------
def observation_from_trace(traces_dir: Path, filename: str) -> tuple[Observation, str]:
    """Build a runtime `Observation` (and the capture's recorded goal) from a stored
    capture artifact + its CDP-AX sidecar. Shared by the record-only capture replay
    path (main._observation_from_capture) and the LiveProposer. Raises FileNotFoundError
    if the artifact is missing; a capture with no `.ax.json` yields an empty candidate
    list (the select stage escalates no_match — itself a valid signal)."""
    import json

    artifact_path = traces_dir / filename
    if not artifact_path.exists():
        raise FileNotFoundError(f"capture artifact not found: {filename}")
    artifact = json.loads(artifact_path.read_text())

    ax_sidecar = traces_dir / f"{filename}.ax.json"
    ax_candidates = json.loads(ax_sidecar.read_text()).get("proposals", []) if ax_sidecar.exists() else []

    acq = artifact.get("acquisition", {}) or {}
    url = (acq.get("page_identity", {}) or {}).get("url", "")
    page_text = (acq.get("js_state", {}) or {}).get("body_text_preview", "") or ""
    viewport = acq.get("viewport_state", {}) or acq.get("training_metadata", {}) or {}
    shots = acq.get("screenshots") or []
    screenshot_path = (shots[0].get("path") if shots else "") or ""
    capture_goal = (acq.get("task_context", {}) or {}).get("goal", "") or ""

    observation = Observation(
        url=url, page_text=page_text, ax_candidates=ax_candidates,
        screenshot_path=screenshot_path, viewport=viewport,
        dom_clickables=acq.get("actionable_elements", []) or [],
        snapshot=verifier.snapshot_from_artifact(artifact, ax_candidates),
    )
    return observation, capture_goal


def _device_scale_factor(observation: Observation, backend_node_id: Optional[int]) -> float:
    """Screenshot bboxes are device pixels; CDP wants CSS px. Prefer the viewport dpr;
    fall back to the target candidate's own `_debug.dpr` recorded at capture time."""
    vp = observation.viewport or {}
    dsf = vp.get("device_scale_factor") or vp.get("dpr")
    if dsf:
        return float(dsf)
    if backend_node_id is not None:
        for c in observation.ax_candidates:
            bid = c.get("backend_node_id") or (c.get("_debug") or {}).get("backend_node_id")
            if bid is not None and int(bid) == int(backend_node_id):
                return float((c.get("_debug") or {}).get("dpr", 1.0) or 1.0)
    return 1.0


def _candidate_role_name(observation: Observation, backend_node_id: Optional[int]) -> tuple:
    """(role, accessible-name) of the selected candidate, so the executor can RE-RESOLVE the target
    by name at act time — closing the node-id-staleness gap between capture/select and act."""
    if backend_node_id is None:
        return None, None
    for c in observation.ax_candidates:
        bid = c.get("backend_node_id") or (c.get("_debug") or {}).get("backend_node_id")
        if bid is not None and int(bid) == int(backend_node_id):
            return (c.get("role"), (c.get("caption") or c.get("name") or "").strip() or None)
    return None, None


# --- Live proposer -----------------------------------------------------------
class LiveProposer:
    """Observe the live page: trigger a capture on the mcp server, then rebuild an
    `Observation` from the trace it wrote. One instance per run; called by the loop
    before every step (and after each executed action for the verify snapshot).

    Resilient by contract: any capture/parse failure returns an EMPTY observation
    (no candidates) rather than raising, so the loop escalates cleanly to a human
    ("couldn't read the page") instead of the whole run crashing."""

    def __init__(self, *, capture_server_url: str, browser_url: str, traces_dir: Path,
                 tab_id: Optional[str] = None, tab_url: Optional[str] = None,
                 goal: str = "", scenario: str = "runtime_loop", timeout: float = 120.0) -> None:
        self._capture_server_url = capture_server_url.rstrip("/")
        self._browser_url = browser_url
        self._traces_dir = traces_dir
        self._tab_id = tab_id
        self._tab_url = tab_url
        self._goal = goal
        self._scenario = scenario
        self._timeout = timeout
        self.last_filename: Optional[str] = None

    def _empty(self, url: str = "") -> Observation:
        return Observation(url=url, page_text="", ax_candidates=[], screenshot_path="",
                           viewport={}, dom_clickables=[], snapshot=verifier.Snapshot())

    def __call__(self) -> Observation:
        payload = {
            "tab_id": self._tab_id, "tab_url": self._tab_url,
            "scenario": self._scenario, "browser_url": self._browser_url,
            "task_context": {"goal": self._goal},
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.post(f"{self._capture_server_url}/capture", json=payload)
                r.raise_for_status()
                filename = (r.json() or {}).get("filename")
        except Exception as exc:  # noqa: BLE001
            logger.warning("LiveProposer capture failed: %s", exc)
            return self._empty()

        if not filename:
            logger.warning("LiveProposer: capture returned no filename")
            return self._empty()
        self.last_filename = filename
        try:
            observation, _ = observation_from_trace(self._traces_dir, filename)
            return observation
        except Exception as exc:  # noqa: BLE001
            logger.warning("LiveProposer: could not rebuild observation from %s: %s", filename, exc)
            return self._empty()


class PrimedProposer:
    """Serve a pre-fetched observation on the FIRST call, then delegate to the base proposer.

    Lets a caller do a pre-flight observation (e.g. an auth check) and hand that same
    observation to the loop as its first step — so the loop doesn't pay for a duplicate
    capture. Proxies `last_filename` so handoffs still resolve the latest page."""

    def __init__(self, primed: Observation, base: LiveProposer):
        self._primed = primed
        self._base = base
        self._used = False

    def __call__(self) -> Observation:
        if not self._used:
            self._used = True
            return self._primed
        return self._base()

    @property
    def last_filename(self) -> Optional[str]:
        return getattr(self._base, "last_filename", None)


# --- Live actor --------------------------------------------------------------
class LiveActor:
    """Perform the loop's decided action against the live tab via mcp `/execute`.

    Defaults to the humanized driver (human-shaped motion/typing) for real drives;
    `record_only=True` swaps in the dry-run driver so nothing fires. Never raises into
    the loop — an executor error returns `executed=False`, which the loop records and
    (because no page delta can be verified) hands to a human."""

    name = "live"

    def __init__(self, *, capture_server_url: str, browser_url: str,
                 tab_id: Optional[str] = None, tab_url: Optional[str] = None,
                 driver: str = "humanized", record_only: bool = False,
                 timeout: float = 60.0) -> None:
        self._capture_server_url = capture_server_url.rstrip("/")
        self._browser_url = browser_url
        self._tab_id = tab_id
        self._tab_url = tab_url
        self._driver = "record_only" if record_only else driver
        self._record_only = record_only
        self._timeout = timeout

    def perform(self, *, action_id: str, target_backend_node_id: Optional[int],
                target_bbox: dict[str, float], value: Optional[str],
                observation: Observation) -> ActResult:
        role, name = _candidate_role_name(observation, target_backend_node_id)
        payload: dict[str, Any] = {
            "action_id": action_id,
            "target_bbox": target_bbox or {},
            "value": value,
            "backend_node_id": target_backend_node_id,
            # Re-resolve by name at act time (immune to node-id churn); node id is the fallback.
            "target_role": role, "target_name": name,
            "device_scale_factor": _device_scale_factor(observation, target_backend_node_id),
            "tab_id": self._tab_id, "tab_url": self._tab_url,
            "browser_url": self._browser_url, "driver": self._driver,
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.post(f"{self._capture_server_url}/execute", json=payload)
                r.raise_for_status()
                res = r.json() or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("LiveActor execute failed: %s", exc)
            return ActResult(executed=False, driver=self._driver, detail=f"executor error: {exc}")

        # record_only never counts as executed (no delta to verify → clean handoff).
        executed = bool(res.get("ok")) and not self._record_only
        return ActResult(executed=executed, driver=res.get("driver", self._driver),
                         detail=res.get("detail", ""))
