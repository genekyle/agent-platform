"""CDP accessibility-tree candidate proposer — the primary `propose` stage.

This is the locked AX + Haiku method's candidate source (see the per-step
verification loop). It talks **raw Chrome DevTools Protocol** to the same
endpoint the chrome-devtools-mcp server uses (default http://127.0.0.1:9222),
so it needs no extra deps beyond `httpx` (target discovery) and `websockets`
(the CDP session) — both already in the venv.

Why raw CDP and not the MCP `get_accessibility_tree` tool: that tool returns
nodes as `{uid, role, name, ignored}` with **no bounding boxes**, so it cannot
ground a click. We need pixel coordinates, which only come from pairing each AX
node's `backendDOMNodeId` with `DOM.getBoxModel`.

Pipeline per capture:
    1. GET /json/list  -> pick the page target (by url/id, else first page)
    2. ws connect      -> CDP session on that target
    3. DOM.enable + Accessibility.enable
    4. Accessibility.getFullAXTree              -> all AX nodes
    5. keep interactive/named, non-ignored nodes
    6. DOM.getBoxModel(backendNodeId) per node  -> CSS-px quad (nodes with no
       layout box are skipped — offscreen / display:none / zero-area)
    7. quad -> bbox, scaled to SCREENSHOT pixels (CSS px x device_scale_factor)

Output shape — matches vision_proposer so it drops into the labeler alongside
observer / vision / manual candidates:

    {
        "candidate_id": "cdp-ax-<hash>",
        "bbox": {"x", "y", "width", "height"},   # SCREENSHOT pixel space
        "confidence": float,                      # heuristic (AX has no score)
        "caption": str,                           # accessible name (the SoM label)
        "role": str,                              # AX role
        "source": "cdp-ax",
        "model_version": "cdp-ax/getFullAXTree+getBoxModel",
        "created_at": iso8601,
        "_debug": {"bbox_css": {...}, "dpr": float, "backend_node_id": int},
    }

COORDINATE CRUX (must be validated on first live run — see __main__):
`DOM.getBoxModel` returns CSS pixels relative to the layout viewport (modern
Chrome already accounts for scroll). The screenshot from Page.captureScreenshot
is in DEVICE pixels (= CSS x devicePixelRatio). So screenshot-space bbox =
CSS bbox * device_scale_factor. On a Retina Mac (DPR=2) skipping this scale
lands every mark at half position. We keep the raw CSS bbox in `_debug` so the
overlay runner can confirm which space the screenshot is actually in.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("mcp.ax_proposer")

DEFAULT_BROWSER_URL = "http://127.0.0.1:9222"
MODEL_VERSION = "cdp-ax/getFullAXTree+getBoxModel"

# AX roles we treat as actionable candidates. A node is also kept if it is
# non-ignored and has a non-empty accessible name (covers custom-role widgets
# that still carry a screen-reader label). Tune from the 30-day logs, not now.
INTERACTIVE_ROLES = frozenset({
    "button", "link", "textbox", "searchbox", "combobox", "checkbox",
    "radio", "switch", "slider", "spinbutton", "menuitem", "menuitemcheckbox",
    "menuitemradio", "tab", "option", "treeitem", "listbox", "menu",
    "menubar", "radiogroup", "disclosuretriangle",
})

# getBoxModel throws "Could not compute box model" for nodes with no layout
# (display:none, detached, zero-area). We skip those silently — they're not
# clickable targets anyway.
_NO_BOX_HINT = "box model"


@dataclass
class AXProposerStats:
    """Per-capture observability — mirror of the vision proposer's stats dict so
    the labeler can show what AX proposal cost and where nodes were dropped."""
    browser_url: str = ""
    target_url: str = ""
    total_ms: int = 0
    ax_nodes: int = 0
    candidate_nodes: int = 0          # passed the role/name filter
    boxed: int = 0                    # had a usable layout box
    no_box: int = 0                   # filtered out by getBoxModel
    device_scale_factor: float = 1.0
    # Iframe traversal (Gmail / Docs / Workday etc. render in child frames).
    frames_total: int = 0             # frames in Page.getFrameTree (incl. main)
    frames_with_ax: int = 0           # frames that returned an AX tree
    frame_ax_nodes: int = 0           # AX nodes pulled from non-main frames
    errors: list[str] = field(default_factory=list)


class _CDPSession:
    """Minimal CDP-over-websocket client: one in-flight request/response map.

    Deliberately tiny — no event subscriptions, no reconnection. We open it,
    fire a handful of request/response commands, and close it. CDP allows
    multiple clients on a target, so coexisting with chrome-devtools-mcp is fine.
    """

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._next_id = 0

    async def send(self, method: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        self._next_id += 1
        msg_id = self._next_id
        await self._ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        # Drain until we see our id. We don't subscribe to events, but Chrome may
        # still emit some on the default session; ignore anything without our id.
        while True:
            raw = await self._ws.recv()
            msg = json.loads(raw)
            if msg.get("id") != msg_id:
                continue
            if "error" in msg:
                raise CDPError(method, msg["error"])
            return msg.get("result", {})


class CDPError(RuntimeError):
    def __init__(self, method: str, error: dict[str, Any]) -> None:
        self.method = method
        self.code = error.get("code")
        self.cdp_message = error.get("message", "")
        super().__init__(f"{method}: {self.cdp_message} (code {self.code})")


async def _discover_target(
    browser_url: str,
    *,
    tab_id: Optional[str],
    tab_url: Optional[str],
    retries: int = 3,
    retry_delay: float = 0.4,
) -> dict[str, Any]:
    """Pick the target to attach to: match targetId first, then url substring, else the first page.

    Includes OOPIF **iframe** targets, not just pages: a cross-origin embedded ATS form (KKR serves
    Greenhouse from a job-boards.greenhouse.io iframe) is its own attachable target, and filtering to
    type=='page' made those forms undriveable — the main frame's Runtime can't see inside them.

    Retries when /json/list momentarily has NO attachable page target — during a live
    drive the page target briefly vanishes mid-navigation (the #1 cause of empty AX
    sidecars: "No attachable page targets"). It reappears within a few hundred ms, so a
    short retry recovers it instead of writing a dry, useless capture.

    Raises when an explicit tab_id/tab_url matches nothing. It used to fall through to the FIRST
    page, so a stale or mistyped tab_url silently drove/read the WRONG tab and looked successful —
    the same failure that wrote an Indeed capture into the corpus labelled as a Workday state.
    """
    import httpx

    pages: list[dict[str, Any]] = []
    attachable: list[dict[str, Any]] = []
    for attempt in range(retries):
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{browser_url}/json/list")
            resp.raise_for_status()
            targets = resp.json()
        pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        # iframes are addressable ONLY by explicit id/url — never a default pick.
        attachable = pages + [t for t in targets
                              if t.get("type") == "iframe" and t.get("webSocketDebuggerUrl")]
        if pages:
            break
        if attempt < retries - 1:
            await asyncio.sleep(retry_delay)  # transient mid-navigation gap; let the target reappear
    if not pages:
        raise RuntimeError(
            f"No attachable page targets at {browser_url}/json/list (after {retries} attempts)"
        )

    if tab_id:
        for t in attachable:
            if str(t.get("id")) == tab_id or str(t.get("targetId")) == tab_id:
                return t
        raise RuntimeError(f"No target with id {tab_id!r} — refusing to fall back to another tab.")
    if tab_url:
        hits = [t for t in attachable if tab_url in str(t.get("url", ""))]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise RuntimeError(
                f"No target whose URL contains {tab_url!r} (open: "
                f"{[str(t.get('url',''))[:60] for t in attachable]}). Refusing to fall back to "
                "another tab — that silently drives the wrong page.")
        # Ambiguous: prefer a real page over an iframe, else refuse rather than guess.
        page_hits = [t for t in hits if t.get("type") == "page"]
        if len(page_hits) == 1:
            return page_hits[0]
        raise RuntimeError(
            f"{tab_url!r} matches {len(hits)} targets — pass a more specific tab_url or a tab_id.")
    # Avoid accidentally grabbing the control panel (same guard as _verify_target_tab)
    non_panel = [t for t in pages if "localhost:5173" not in str(t.get("url", ""))]
    return (non_panel or pages)[0]


def _quad_to_bbox(quad: list[float]) -> dict[str, float]:
    """CDP content quad = [x1,y1, x2,y2, x3,y3, x4,y4] (CSS px). Reduce to an
    axis-aligned bbox; quads are normally rectangular but min/max is robust to
    any rotation."""
    xs = quad[0::2]
    ys = quad[1::2]
    x0, y0 = min(xs), min(ys)
    return {"x": x0, "y": y0, "width": max(xs) - x0, "height": max(ys) - y0}


def _candidate_id(bbox: dict[str, float], role: str, name: str) -> str:
    payload = (
        f"{role}|{name}|"
        f"{bbox['x']:.1f},{bbox['y']:.1f},{bbox['width']:.1f},{bbox['height']:.1f}"
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
    return f"cdp-ax-{digest}"


def _ax_value(node: dict[str, Any], key: str) -> str:
    """AX node fields are wrapped as {'value': ...}. Pull the plain string."""
    field_val = node.get(key)
    if isinstance(field_val, dict):
        return str(field_val.get("value", "") or "")
    return str(field_val or "")


def _enumerate_frame_ids(frame_tree: dict[str, Any]) -> list[str]:
    """Flatten Page.getFrameTree into a list of frame ids (main frame first, then
    children depth-first). Gmail/Docs/Workday/Salesforce render their real UI
    inside child frames — without this walk the AX proposer only sees the
    outer Google/Workday chrome and Gmail's inbox looks empty (the symptom that
    triggered this code: 11 candidates for a real inbox)."""
    out: list[str] = []

    def _walk(node: dict[str, Any]) -> None:
        frame = node.get("frame") or {}
        fid = frame.get("id")
        if fid:
            out.append(fid)
        for child in node.get("childFrames") or []:
            _walk(child)

    _walk(frame_tree)
    return out


def _keep_node(role: str, name: str, ignored: bool) -> bool:
    if ignored:
        return False
    if role in INTERACTIVE_ROLES:
        return True
    # Non-interactive role but has a real accessible name -> still a usable,
    # labelable target (custom widgets). Skip bare structural / document-level
    # nodes (RootWebArea is the whole page; StaticText/InlineTextBox are text runs).
    return bool(name.strip()) and role not in {
        "", "none", "presentation", "generic", "InlineTextBox", "StaticText",
        "RootWebArea", "WebArea", "document", "paragraph", "LineBreak",
    }


async def propose_ax_candidates(
    *,
    browser_url: str = DEFAULT_BROWSER_URL,
    tab_id: Optional[str] = None,
    tab_url: Optional[str] = None,
    device_scale_factor: float = 1.0,
    stats: Optional[AXProposerStats] = None,
) -> list[dict[str, Any]]:
    """Run the CDP-AX proposer against a live Chrome at `browser_url`.

    `device_scale_factor` scales CSS-px boxes into screenshot-px space; pass the
    capture's viewport_state.device_scale_factor. Returns [] (not raises) on a
    missing/unreachable browser so a capture degrades gracefully to other
    candidate sources.
    """
    import websockets

    t0 = time.perf_counter()
    st = stats if stats is not None else AXProposerStats()
    st.browser_url = browser_url
    st.device_scale_factor = device_scale_factor

    try:
        target = await _discover_target(browser_url, tab_id=tab_id, tab_url=tab_url)
    except Exception as exc:
        st.errors.append(f"target_discovery: {exc}")
        logger.warning("CDP-AX target discovery failed: %s", exc)
        return []

    st.target_url = str(target.get("url", ""))
    ws_url = target["webSocketDebuggerUrl"]

    out: list[dict[str, Any]] = []
    try:
        # max_size lifted: getFullAXTree on dense pages can exceed the 1 MiB default.
        async with websockets.connect(ws_url, max_size=32 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            await cdp.send("DOM.enable")
            await cdp.send("Page.enable")
            await cdp.send("Accessibility.enable")
            tree = await cdp.send("Accessibility.getFullAXTree")
            nodes = list(tree.get("nodes", []))

            # IFRAME TRAVERSAL: getFullAXTree on the session returns only the
            # current (main) frame's tree. Sites like Gmail/Docs/Workday render
            # the real UI inside child frames, so without this pass the proposer
            # is blind to anything useful there. We walk Page.getFrameTree and
            # call getFullAXTree(frameId) per child. backendDOMNodeIds are
            # session-wide unique, so the downstream getBoxModel still works.
            try:
                frame_tree = (await cdp.send("Page.getFrameTree")).get("frameTree", {})
                frame_ids = _enumerate_frame_ids(frame_tree)
                st.frames_total = len(frame_ids)
                for fid in frame_ids[1:]:  # skip main frame (already pulled above)
                    try:
                        sub = await cdp.send("Accessibility.getFullAXTree", {"frameId": fid})
                        sub_nodes = sub.get("nodes", []) or []
                        if sub_nodes:
                            st.frames_with_ax += 1
                            st.frame_ax_nodes += len(sub_nodes)
                            nodes.extend(sub_nodes)
                    except CDPError as exc:
                        # OOPIF cross-origin frames need Target.attachToTarget; defer
                        # that until/if real-world sites surface it. Record + continue.
                        st.errors.append(f"frame_ax({fid[:8]}): {exc.cdp_message}")
            except CDPError as exc:
                st.errors.append(f"frame_tree: {exc.cdp_message}")

            st.ax_nodes = len(nodes)

            now = datetime.now(timezone.utc).isoformat()
            for node in nodes:
                role = _ax_value(node, "role")
                name = _ax_value(node, "name")
                ignored = bool(node.get("ignored", False))
                if not _keep_node(role, name, ignored):
                    continue
                backend_id = node.get("backendDOMNodeId")
                if backend_id is None:
                    continue
                st.candidate_nodes += 1

                try:
                    box = await cdp.send("DOM.getBoxModel", {"backendNodeId": backend_id})
                except CDPError as exc:
                    # Expected for offscreen / unlaid-out nodes — count, don't spam.
                    if _NO_BOX_HINT in exc.cdp_message.lower():
                        st.no_box += 1
                    else:
                        st.errors.append(str(exc))
                    continue

                model = box.get("model") or {}
                quad = model.get("content")
                if not quad or len(quad) < 8:
                    st.no_box += 1
                    continue

                bbox_css = _quad_to_bbox(quad)
                if bbox_css["width"] <= 0 or bbox_css["height"] <= 0:
                    st.no_box += 1
                    continue

                bbox = {k: v * device_scale_factor for k, v in bbox_css.items()}
                st.boxed += 1
                out.append({
                    "candidate_id": _candidate_id(bbox, role, name),
                    "bbox": bbox,
                    "confidence": _confidence(role, name),
                    "caption": name,
                    "role": role,
                    # Stable AX identity — first-class so the SELECT stage can key
                    # cache + result on it (not just the screenshot-relative bbox).
                    "backend_node_id": backend_id,
                    "source": "cdp-ax",
                    "model_version": MODEL_VERSION,
                    "created_at": now,
                    "_debug": {
                        "bbox_css": bbox_css,
                        "dpr": device_scale_factor,
                    },
                })
    except Exception as exc:
        st.errors.append(f"cdp_session: {exc}")
        logger.warning("CDP-AX session failed: %s", exc)
        # Return whatever we gathered before the failure rather than nothing.

    st.total_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "CDP-AX: nodes=%d (frames=%d, w/ax=%d, sub_nodes=%d) candidates=%d boxed=%d no_box=%d dpr=%.2f total=%dms url=%s",
        st.ax_nodes, st.frames_total, st.frames_with_ax, st.frame_ax_nodes,
        st.candidate_nodes, st.boxed, st.no_box,
        device_scale_factor, st.total_ms, st.target_url,
    )
    return out


def _confidence(role: str, name: str) -> float:
    """AX has no objectness score; synthesize a coarse one so the candidate
    shape matches vision (which carries YOLO confidence). Named interactive
    roles are the strongest; unnamed-but-interactive next; named-only last."""
    interactive = role in INTERACTIVE_ROLES
    named = bool(name.strip())
    if interactive and named:
        return 0.9
    if interactive:
        return 0.7
    return 0.5


def propose_ax_candidates_sync(**kwargs: Any) -> list[dict[str, Any]]:
    """Sync convenience for non-async callers / the CLI runner."""
    return asyncio.run(propose_ax_candidates(**kwargs))


# --- Live validation overlay -------------------------------------------------
# Run against a live Chrome (launched with --remote-debugging-port=9222) to
# eyeball the COORDINATE CRUX: draws every candidate bbox onto a fresh CDP
# screenshot. If boxes hug their elements -> the dpr scaling is right. If they
# sit up-and-left at half scale -> device_scale_factor was wrong / double-applied.
#
#   .venv/bin/python -m app.observer.ax_proposer --url http://127.0.0.1:9222 \
#       --dpr 2 --out /tmp/ax_overlay.png
async def _overlay_main(browser_url: str, dpr: float, out_path: str) -> int:
    import base64
    import websockets
    from PIL import Image, ImageDraw

    stats = AXProposerStats()
    candidates = await propose_ax_candidates(
        browser_url=browser_url, device_scale_factor=dpr, stats=stats,
    )
    print(json.dumps(stats.__dict__, indent=2, default=str))
    if not candidates:
        print("No candidates — is Chrome up with --remote-debugging-port and a real page loaded?")
        return 1

    target = await _discover_target(browser_url, tab_id=None, tab_url=None)
    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
        shot = await _CDPSession(ws).send("Page.captureScreenshot", {"format": "png"})
    img_bytes = base64.b64decode(shot["data"])
    from io import BytesIO
    img = Image.open(BytesIO(img_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    print(f"screenshot size = {img.size}  (compare to viewport_css * dpr)")
    for c in candidates:
        b = c["bbox"]
        draw.rectangle([b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"]], outline=(255, 0, 0), width=2)
    img.save(out_path)
    print(f"Wrote overlay -> {out_path}  ({len(candidates)} boxes). Eyeball the alignment.")
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CDP-AX proposer live validation overlay")
    parser.add_argument("--url", default=DEFAULT_BROWSER_URL)
    parser.add_argument("--dpr", type=float, default=1.0, help="device_scale_factor (Retina Mac = 2)")
    parser.add_argument("--out", default="/tmp/ax_overlay.png")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_overlay_main(args.url, args.dpr, args.out)))
