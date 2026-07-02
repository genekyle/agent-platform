"""ActionVerifierV1 — the per-step "did that work?" gate (Phase 7).

After the executor performs an action, we compare a BEFORE and AFTER snapshot of
the page (AX identities, route, focus, dialog, body text, scroll) and ask: did
the page change the way this action predicted? A "Send" click that produced no
change is caught here — one step later — instead of 15 steps later when the task
has silently failed. Verification converts silent failures into clean
escalations, which is what makes human-in-the-loop supervisable.

Pure + side-effect-free: `verify(...)` operates on two Snapshots, so it's fully
testable without a live browser. The bounded-retry policy (`next_step`) keeps the
runtime from retrying forever: at most MAX_RETRIES, then escalate to a human.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from . import fingerprint
from .schema import VerificationResult

# After a failed verify: retry at most this many times (stepping down a modality
# in the runtime loop), then escalate to a human.
MAX_RETRIES = 1
_SCROLL_EPS = 8.0  # px


@dataclass(frozen=True)
class Snapshot:
    url: str = ""
    route: str = ""
    ax: frozenset = field(default_factory=frozenset)        # {(role, name)}
    ax_by_backend: dict = field(default_factory=dict)        # backend_node_id -> (role, name)
    dialog_present: bool = False
    active_element: str = ""
    body_text: str = ""
    scroll_y: float = 0.0


def snapshot_from_artifact(artifact: dict[str, Any], ax_candidates: list[dict[str, Any]]) -> Snapshot:
    acq = artifact.get("acquisition", {}) or {}
    url = (acq.get("page_identity", {}) or {}).get("url", "") or ""
    frame = acq.get("frame_state", {}) or {}
    vp = acq.get("viewport_state", {}) or {}
    js = acq.get("js_state", {}) or {}
    ax_set = set()
    by_backend = {}
    for c in ax_candidates:
        ident = (str(c.get("role", "")), (c.get("caption") or c.get("name") or "").strip())
        ax_set.add(ident)
        bid = c.get("backend_node_id") or (c.get("_debug") or {}).get("backend_node_id")
        if bid is not None:
            by_backend[int(bid)] = ident
    return Snapshot(
        url=url,
        route=fingerprint.route_template(url),
        ax=frozenset(ax_set),
        ax_by_backend=by_backend,
        dialog_present=bool(frame.get("dialog_present", False)),
        active_element=str(frame.get("active_element") or ""),
        body_text=(js.get("body_text_preview") or "").strip(),
        scroll_y=float(vp.get("scroll_y", 0) or 0),
    )


@dataclass(frozen=True)
class Delta:
    route_changed: bool
    ax_added: frozenset
    ax_removed: frozenset
    dialog_changed: bool
    focus_changed: bool
    text_changed: bool
    scroll_changed: bool

    @property
    def any_change(self) -> bool:
        return any([self.route_changed, bool(self.ax_added), bool(self.ax_removed),
                    self.dialog_changed, self.focus_changed, self.text_changed, self.scroll_changed])

    def summary(self) -> str:
        bits = []
        if self.route_changed: bits.append("route")
        if self.ax_added or self.ax_removed: bits.append(f"ax(+{len(self.ax_added)}/-{len(self.ax_removed)})")
        if self.dialog_changed: bits.append("dialog")
        if self.focus_changed: bits.append("focus")
        if self.text_changed: bits.append("text")
        if self.scroll_changed: bits.append("scroll")
        return ",".join(bits) or "no change"


def compute_delta(before: Snapshot, after: Snapshot) -> Delta:
    return Delta(
        route_changed=before.route != after.route,
        ax_added=frozenset(after.ax - before.ax),
        ax_removed=frozenset(before.ax - after.ax),
        dialog_changed=before.dialog_present != after.dialog_present,
        focus_changed=before.active_element != after.active_element,
        text_changed=before.body_text != after.body_text,
        scroll_changed=abs(before.scroll_y - after.scroll_y) > _SCROLL_EPS,
    )


def verify(
    *,
    action_id: str,
    before: Snapshot,
    after: Snapshot,
    target_backend_node_id: Optional[int] = None,
    expected_value: Optional[str] = None,
) -> VerificationResult:
    """Did the AFTER state change the way `action_id` predicted? Returns a
    VerificationResult(ok, predicted, observed, reason)."""
    d = compute_delta(before, after)
    observed = d.summary()

    if action_id in ("submit",):
        ok = d.route_changed or d.text_changed
        predicted = "navigation / page-state change"
    elif action_id == "scroll":
        ok = d.scroll_changed
        predicted = "viewport scrolled"
    elif action_id == "type":
        # Strongest signal: the typed value now appears in the page text. Else a
        # weaker signal: focus moved onto the target and the page text changed.
        predicted = "target field reflects typed input"
        if expected_value:
            ok = expected_value.strip().lower() in after.body_text.lower()
        else:
            ok = d.text_changed or d.focus_changed
    elif action_id == "clear":
        predicted = "target field cleared"
        ok = d.text_changed or bool(d.ax_removed)
    else:  # click / select / default
        predicted = "page state changed (something happened)"
        ok = d.any_change

    reason = "expected change observed" if ok else (
        f"NO expected change after '{action_id}' — predicted {predicted!r}, observed {observed!r}")
    return VerificationResult(ok=ok, predicted=predicted, observed=observed, reason=reason)


def next_step(result: VerificationResult, retry_count: int, max_retries: int = MAX_RETRIES) -> str:
    """Bounded-retry policy. 'ok' | 'retry' | 'escalate'. The runtime steps down a
    modality on 'retry' and hands to a human on 'escalate' (never loops forever).
    `max_retries` lets the caller widen the budget (e.g. live runs try a few times
    before giving up / running the captcha diagnostic)."""
    if result.ok:
        return "ok"
    if retry_count < max_retries:
        return "retry"
    return "escalate"
