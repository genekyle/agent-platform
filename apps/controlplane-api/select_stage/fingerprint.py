"""Re-export shim — StateFingerprintV1 moved to the shared `interaction` package.

The fingerprint is the join key between the selector's corpus (`selection_telemetry.jsonl`),
the loop's (`loop_steps.jsonl`) and the actor's (`intent_journal.jsonl`). The actor lives in
`apps/mcp`, which cannot import the control plane — so the fingerprint had to move somewhere
both can reach. Copying it would have been the cheaper edit and the wrong one: this repo has
already proven that duplicated logic drifts silently (the autofill question-matcher in
`mcp/app/main_server.py` diverged from its twin in `application_answers.py` — different
stop-word lists, so the same question scored differently on each side).

Kept as a shim rather than rewriting ~14 call sites (`from select_stage import fingerprint`
+ `fingerprint.compute(...)`), which would have made the move a large diff for no benefit.
New code should import from `interaction.fingerprint` directly.
"""

from interaction.fingerprint import (  # noqa: F401
    _FINGERPRINT_VERSION,
    _normalize_ax_name,
    ax_summary,
    compute,
    dom_summary,
    route_template,
    viewport_class,
)

# `_normalize_ax_name` is private but re-exported deliberately: select_stage/test_select_core.py
# tests it directly (it holds the volatile-token rules that stop "Messages (3)" and "Messages (7)"
# fingerprinting differently), and the test is worth more than the underscore.
__all__ = ["_FINGERPRINT_VERSION", "_normalize_ax_name", "ax_summary", "compute",
           "dom_summary", "route_template", "viewport_class"]
